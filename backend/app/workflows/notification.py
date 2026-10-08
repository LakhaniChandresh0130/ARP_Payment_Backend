from app.config import APP_URL
from app.services.supabase_client import supabase
from app.workflows.helpers import get_invoice

def process_notification(event_data: dict):
    # event_data is the whole event dict from invoice_events
    invoice_id = event_data.get('invoice_id')
    if not invoice_id: return
    
    inv = get_invoice(invoice_id)
    if not inv: return

    try:
        def clean(s): return str(s or '').replace('\r', ' ').replace('\n', ' ')[:80]

        P = supabase.table('profiles').select('id,name,email,role,department_id').execute().data or []
        t = event_data.get('event_type')
        pl = event_data.get('payload', {})
        ref = clean(inv.get('invoice_number') or inv.get('original_filename'))

        to_users = []
        title = ''
        body = ''

        if t == 'invoice.approval.required':
            role = pl.get('approver_role')
            for p in P:
                if p.get('role') == role:
                    if role in ['poc', 'dept_manager']:
                        if p.get('department_id') == inv.get('department_id'):
                            to_users.append(p)
                    else:
                        to_users.append(p)
            if not to_users:
                to_users = [p for p in P if p.get('role') == 'finance_admin']
            
            title = f"Approval required: {ref}"
            body = f"Invoice {ref} for INR {inv.get('total')} is waiting for your approval."

        elif t == 'invoice.flagged':
            to_users = [p for p in P if p.get('role') in ['ap_manager', 'finance_admin']]
            title = f"Invoice flagged: {ref}"
            body = f"Invoice {ref} was flagged ({clean(pl.get('level'))}): {clean(pl.get('reason'))}."

        elif t == 'invoice.info.requested':
            to_users = [p for p in P if p.get('id') == inv.get('submitted_by')]
            title = f"Information needed: {ref}"
            body = clean(pl.get('reason')) or "More information is required for this invoice."

        elif t == 'invoice.rejected':
            to_users = [p for p in P if p.get('id') in [inv.get('submitted_by'), inv.get('assigned_poc_id')]]
            title = f"Invoice rejected: {ref}"
            body = f"Invoice {ref} was rejected. Open LedgerFlow for details."

        elif t == 'invoice.payment.ready':
            to_users = [p for p in P if p.get('role') in ['finance_admin', 'cfo', 'ap_manager']]
            title = f"Payment ready: {ref}"
            body = f"Invoice {ref} is queued for payment and can be exported to Tally."

        elif t == 'invoice.exported':
            to_users = [p for p in P if p.get('role') in ['finance_admin', 'ap_manager']]
            title = f"Tally export: {ref}"
            body = f"Invoice {ref} was included in a Tally Excel export."

        unique_to = {p['id']: p for p in to_users}.values()
        if not unique_to: return

        notifications = []
        for p in unique_to:
            notifications.append({
                "user_id": p['id'],
                "invoice_id": inv['id'],
                "title": title,
                "body": body
            })
            
            # Here we would normally also send an email
            email_text = f"Hi {clean(p.get('name'))},\n\n{body}\n\n{APP_URL}"
            print(f"Would send email to {p.get('email')} with subject '{title}':\n{email_text}")

        if notifications:
            supabase.table('notifications').insert(notifications).execute()

    except Exception as e:
        print(f"Failed to process notification for event {event_data.get('id')}: {e}")
