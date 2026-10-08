import io
import re
from datetime import datetime, timezone
from openpyxl import Workbook
from app.services.supabase_client import supabase
from app.workflows.helpers import emit_event, audit_log

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)

HEADERS = [
    "Date", "Voucher Type", "Voucher No", "Vendor", "GSTIN", "Ledger",
    "Amount", "GST", "Total", "Department", "Cost Center",
]


def process_tally_export(event_data: dict):
    payload = event_data.get("payload") or {}
    if isinstance(payload, str):
        return
    eid = payload.get("export_id")
    if not eid or not UUID.match(str(eid)):
        return

    ex_res = supabase.table("tally_exports").select("*").eq("id", eid).execute().data
    if not ex_res:
        return
    ex = ex_res[0]
    if ex.get("status") == "ready" and ex.get("file_path"):
        return

    supabase.table("tally_exports").update({"status": "processing"}).eq("id", eid).execute()

    try:
        invoice_ids = ex.get("invoice_ids") or []
        invs = supabase.table("invoices").select(
            "id,invoice_number,invoice_date,subtotal,tax,total,extracted,classification,department_id,"
            "vendors(vendor_name,gstin)"
        ).in_("id", invoice_ids).execute().data or []
        maps = supabase.table("tally_mappings").select("*").execute().data or []
        depts = supabase.table("departments").select("id,name").execute().data or []

        def safe(v):
            s = str(v if v is not None else "")
            if re.match(r"^[=+\-@\t\r]", s):
                return "'" + s
            return s

        def fmt_date(d):
            if not d:
                return ""
            parts = str(d).split("-")
            return "-".join(reversed(parts)) if len(parts) == 3 else str(d)

        rows = []
        issues = []
        ids = []

        for i in invs:
            cls = i.get("classification") or {}
            cat = cls.get("category")
            ref = i.get("invoice_number") or i["id"]
            m = next((x for x in maps if x.get("expense_category") == cat), None)
            if not m:
                m = {
                    "expense_category": cat,
                    "tally_ledger": cat or "Unmapped Expenses",
                    "gst_rate": 18,
                    "cost_center": None,
                }
                issues.append({"invoice": ref, "issue": f"No Tally ledger mapping for category {cat}; used category name"})

            tax = float(i.get("tax") or 0)
            subtotal = float(i.get("subtotal") or 0)
            extracted = i.get("extracted") or {}
            if tax > 0 and not extracted.get("gstin_valid"):
                issues.append({"invoice": ref, "issue": "GSTIN missing or invalid"})
                continue
            expected = subtotal * float(m.get("gst_rate") or 0) / 100
            if tax > 0 and abs(expected - tax) > max(1.0, expected * 0.02):
                issues.append({"invoice": ref, "issue": f"GST amount does not match ledger rate {m.get('gst_rate')}%"})
                continue

            d = next((x for x in depts if x.get("id") == i.get("department_id")), None)
            v = i.get("vendors") or {}
            dn = (d or {}).get("name") or ""
            rows.append({
                "Date": fmt_date(i.get("invoice_date")),
                "Voucher Type": "Purchase",
                "Voucher No": safe(i.get("invoice_number")),
                "Vendor": safe(v.get("vendor_name")),
                "GSTIN": safe(v.get("gstin")),
                "Ledger": safe(m.get("tally_ledger")),
                "Amount": subtotal,
                "GST": tax,
                "Total": float(i.get("total") or 0),
                "Department": safe(dn),
                "Cost Center": safe(m.get("cost_center") or dn),
            })
            ids.append(i["id"])

        validation = {"validated": len(rows), "issues": issues, "included_ids": ids}
        supabase.table("tally_exports").update({
            "entry_count": len(rows),
            "validation": validation,
        }).eq("id", eid).execute()

        if not rows:
            supabase.table("tally_exports").update({"status": "failed"}).eq("id", eid).execute()
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Tally"
        ws.append(HEADERS)
        for row in rows:
            ws.append([row[h] for h in HEADERS])
        buf = io.BytesIO()
        wb.save(buf)
        file_bytes = buf.getvalue()

        path = f"tally/{eid}.xlsx"
        xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        # storage3 defaults to text/plain unless content-type uses this exact key.
        opts = {"content-type": xlsx_type, "upsert": "true"}
        try:
            supabase.storage.from_("exports").upload(path, file_bytes, opts)
        except Exception:
            supabase.storage.from_("exports").update(path, file_bytes, {"content-type": xlsx_type})

        supabase.table("tally_exports").update({
            "status": "ready",
            "file_path": path,
            "entry_count": len(rows),
            "validation": validation,
        }).eq("id", eid).execute()

        now = datetime.now(timezone.utc).isoformat()
        if ids:
            supabase.table("invoices").update({"exported_at": now}).in_("id", ids).execute()
        for inv_id in ids:
            audit_log(inv_id, "tally_exported", f"Included in Tally export {eid}")
            emit_event("invoice.exported", inv_id, {"export_id": eid})

    except Exception as e:
        print(f"[Tally] export {eid} failed: {e}")
        supabase.table("tally_exports").update({
            "status": "failed",
            "validation": {"issues": [{"invoice": "", "issue": str(e)[:300]}]},
        }).eq("id", eid).execute()
