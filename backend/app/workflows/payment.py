import datetime
from app.services.supabase_client import supabase
from app.workflows.helpers import patch_invoice, audit_log, emit_event, fail_invoice, get_invoice

def process_payment_preparation(invoice_id: str):
    inv = get_invoice(invoice_id)
    if not inv or inv.get('status') != 'approved' or inv.get('payment_status') != 'not_ready':
        return

    try:
        vendor_res = supabase.table('vendors').select('*').eq('id', inv['vendor_id']).execute().data
        vendor = vendor_res[0] if vendor_res else None
        
        issues = []
        if not vendor or vendor.get('status') != 'active':
            issues.append('Vendor is not verified/active')
        if not vendor or not vendor.get('bank_account') or not vendor.get('ifsc'):
            issues.append('Vendor bank details missing')
            
        if float(inv.get('risk_score', 0) or 0) > 80:
            ap_res = supabase.table('invoice_approvals').select('approver_id').eq('invoice_id', inv['id']).eq('decision', 'approved').order('created_at', desc=True).limit(1).execute().data
            if ap_res:
                role_res = supabase.table('profiles').select('role').eq('id', ap_res[0]['approver_id']).execute().data
                role = role_res[0].get('role') if role_res else None
                if not role or role not in ['cfo', 'finance_admin']:
                    issues.append('Critical-risk invoice needs CFO approval')
            else:
                issues.append('Critical-risk invoice needs CFO approval')

        if issues:
            issues_str = "; ".join(issues)[:300]
            patch_invoice(inv['id'], {'status': 'on_hold', 'n8n_error': issues_str})
            audit_log(inv['id'], 'payment_hold', "; ".join(issues))
            emit_event('invoice.flagged', inv['id'], {'level': 'HOLD', 'reason': "; ".join(issues)})
            return

        try:
            supabase.table('payments').insert({
                'invoice_id': inv['id'],
                'vendor_id': inv['vendor_id'],
                'amount': inv['total'],
                'payment_date': inv.get('due_date') or datetime.datetime.now().strftime("%Y-%m-%d"),
                'payment_method': 'neft',
                'bank_account': vendor['bank_account'],
                'status': 'queued'
            }).execute()
        except Exception as e:
            if not any(x in str(e).lower() for x in ['409', '23505', 'duplicate']):
                raise e

        patch_invoice(inv['id'], {'status': 'payment_ready', 'payment_status': 'ready', 'n8n_error': None})
        audit_log(inv['id'], 'payment_queued', 'Added to payment queue')
        emit_event('invoice.payment.ready', inv['id'])

    except Exception as e:
        fail_invoice(inv['id'], e)
