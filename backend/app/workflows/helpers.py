from app.services.supabase_client import supabase
from typing import Optional, Dict, Any

def emit_event(event_type: str, invoice_id: str, payload: Optional[Dict[str, Any]] = None):
    payload = payload or {}
    try:
        supabase.table("invoice_events").insert({
            "event_type": event_type,
            "invoice_id": invoice_id,
            "payload": payload
        }).execute()
    except Exception as e:
        print(f"Failed to emit event {event_type} for invoice {invoice_id}: {e}")

def audit_log(invoice_id: str, action: str, message: str, new_value: Optional[Dict[str, Any]] = None):
    try:
        supabase.table("invoice_audit_logs").insert({
            "invoice_id": invoice_id,
            "action": action,
            "message": message,
            "new_value": new_value
        }).execute()
    except Exception as e:
        print(f"Failed to write audit log for invoice {invoice_id}: {e}")

def patch_invoice(invoice_id: str, data: Dict[str, Any]):
    try:
        supabase.table("invoices").update(data).eq("id", invoice_id).execute()
    except Exception as e:
        print(f"Failed to patch invoice {invoice_id}: {e}")

def fail_invoice(invoice_id: str, error: Exception):
    try:
        error_msg = str(error)[:300]
        patch_invoice(invoice_id, {"n8n_error": error_msg})
        print(f"Failed invoice {invoice_id}: {error_msg}")
    except Exception:
        pass
    raise error

def get_invoice(invoice_id: str) -> Optional[Dict[str, Any]]:
    import re
    if not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", invoice_id, re.I):
        return None
    response = supabase.table("invoices").select("*").eq("id", invoice_id).execute()
    if response.data:
        return response.data[0]
    return None
