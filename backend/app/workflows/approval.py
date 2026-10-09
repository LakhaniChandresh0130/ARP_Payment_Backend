from app.workflows.helpers import patch_invoice, audit_log, emit_event, fail_invoice, get_invoice

def process_approval_routing(invoice_id: str):
    inv = get_invoice(invoice_id)
    if not inv or not inv.get('risk_factors') or inv.get('status') != 'processing':
        return

    try:
        t = float(inv.get('total', 0) or 0)
        rank = {'poc': 1, 'dept_manager': 2, 'ap_manager': 3, 'cfo': 4}
        lvl = inv.get('risk_factors', {}).get('level')
        cl = inv.get('classification', {})

        if t <= 25000:
            role = 'poc'
        elif t <= 200000:
            role = 'dept_manager'
        elif t <= 1000000:
            role = 'ap_manager'
        else:
            role = 'cfo'

        min_role = 'poc'
        if lvl == 'HIGH' or inv.get('matching_status') == 'mismatch' or cl.get('needs_review') or float(inv.get('duplicate_score', 0) or 0) >= 60:
            min_role = 'ap_manager'
        if lvl == 'CRITICAL':
            min_role = 'cfo'

        if rank.get(min_role, 1) > rank.get(role, 1):
            role = min_role

        patch_invoice(inv['id'], {
            'status': 'pending_approval',
            'approval_status': 'pending',
            'approver_role': role
        })
        audit_log(inv['id'], 'routed', f"Routed to {role}", {'amount': t, 'risk': lvl})
        emit_event('invoice.approval.required', inv['id'], {'approver_role': role})

    except Exception as e:
        fail_invoice(inv['id'], e)
