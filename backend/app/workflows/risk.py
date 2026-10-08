import json
import datetime
from app.services.groq_client import create_chat_completion
from app.services.supabase_client import supabase
from app.workflows.helpers import patch_invoice, audit_log, emit_event, fail_invoice, get_invoice

def process_risk_analysis(invoice_id: str):
    inv = get_invoice(invoice_id)
    mr = inv.get('match_result') if inv else None
    if not inv or not mr or 'duplicates' not in mr or inv.get('risk_factors') or inv.get('status') != 'processing':
        return

    ex = inv.get('extracted', {})
    factors = []

    try:
        vendor = None
        if inv.get('vendor_id'):
            v_res = supabase.table('vendors').select('*').eq('id', inv['vendor_id']).execute().data
            if v_res:
                vendor = v_res[0]

        hist = []
        if vendor:
            hist = supabase.table('invoices').select('total').eq('vendor_id', vendor['id']).neq('id', inv['id']).in_('status', ['approved', 'payment_ready', 'paid']).order('created_at', desc=True).limit(12).execute().data or []

        if float(inv.get('duplicate_score', 0) or 0) >= 60:
            factors.append({'code': 'duplicate', 'points': 30, 'detail': f"Duplicate-like invoice (score {inv.get('duplicate_score')})"})

        inv_total = float(inv.get('total', 0) or 0)
        if len(hist) >= 3:
            avg = sum(float(b.get('total', 0) or 0) for b in hist) / len(hist)
            if avg > 0 and inv_total > avg * 1.5:
                factors.append({'code': 'amount_high', 'points': 20, 'detail': f"Amount is {round(inv_total/avg, 1)}x the vendor average"})

        if not vendor or vendor.get('status') != 'active' or not hist:
            factors.append({'code': 'new_vendor', 'points': 15, 'detail': 'New or unverified vendor'})

        import re
        def nb(s): return re.sub(r'\D', '', str(s or ''))

        if vendor and nb(vendor.get('bank_account')) and nb(ex.get('bank_account')) and nb(vendor.get('bank_account')) != nb(ex.get('bank_account')):
            factors.append({'code': 'bank_changed', 'points': 10, 'detail': 'Bank details differ from vendor master'})

        if inv.get('matching_status') == 'mismatch':
            factors.append({'code': 'po_mismatch', 'points': 10, 'detail': 'PO / GRN mismatch'})

        def parse_date(d):
            try:
                return datetime.datetime.strptime(d, "%Y-%m-%d")
            except:
                return None

        d = parse_date(inv.get('invoice_date', ''))
        due = parse_date(inv.get('due_date', ''))
        now = datetime.datetime.now()

        if not d or d > now + datetime.timedelta(days=1) or (now - d).days > 180 or (due and due < d):
            factors.append({'code': 'timing', 'points': 5, 'detail': 'Unusual invoice or due date'})

        if (ex.get('gstin') and not ex.get('gstin_valid')) or ex.get('math_ok') is False:
            factors.append({'code': 'data_quality', 'points': 5, 'detail': 'GSTIN format or totals look wrong'})

        score = min(100, sum(f['points'] for f in factors))
        level = 'LOW' if score <= 30 else 'MEDIUM' if score <= 60 else 'HIGH' if score <= 80 else 'CRITICAL'

        messages = [
            {"role": "system", "content": "Explain invoice risk to an accounts-payable approver in at most two plain sentences. Use only the JSON given."},
            {"role": "user", "content": json.dumps({"score": score, "level": level, "factors": factors})}
        ]

        chat_completion = create_chat_completion(
            messages=messages,
            temperature=0.2,
            max_tokens=200
        )

        summary = chat_completion.choices[0].message.content[:500]

        r_data = {
            "level": level,
            "factors": factors,
            "summary": summary
        }

        patch_invoice(inv['id'], {
            "risk_score": score,
            "risk_factors": r_data
        })
        audit_log(inv['id'], "risk_scored", f"Risk {score} {level}", {"factors": factors})
        emit_event('invoice.risk.scored', inv['id'], {"score": score, "level": level})

        if level in ['HIGH', 'CRITICAL'] or inv.get('matching_status') == 'mismatch' or float(inv.get('duplicate_score', 0) or 0) >= 60:
            reason_str = "; ".join(f['detail'] for f in factors)[:300]
            emit_event('invoice.flagged', inv['id'], {"level": level, "score": score, "reason": reason_str})

    except Exception as e:
        fail_invoice(inv['id'], e)
