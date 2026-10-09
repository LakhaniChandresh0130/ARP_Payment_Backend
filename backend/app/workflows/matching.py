from app.services.supabase_client import supabase
from app.services.utils import similarity, levenshtein
from app.workflows.helpers import patch_invoice, audit_log, emit_event, fail_invoice, get_invoice

def process_po_matching(invoice_id: str):
    inv = get_invoice(invoice_id)
    if not inv or inv.get('classification_status') != 'completed' or inv.get('matching_status') != 'pending':
        return

    ex = inv.get('extracted', {})
    TOL = 0.02
    PO_REQUIRED_ABOVE = 50000

    try:
        po = None
        if ex.get('po_number'):
            res = supabase.table('purchase_orders').select('*').eq('po_number', ex['po_number']).execute()
            if res.data:
                po = res.data[0]
        
        inv_total = float(inv.get('total', 0) or 0)

        if not ex.get('po_number') and inv_total > PO_REQUIRED_ABOVE:
            patch_invoice(inv['id'], {
                'matching_status': 'no_po',
                'status': 'needs_info',
                'match_result': {'po': {'status': 'missing', 'required': True}}
            })
            audit_log(inv['id'], 'po_missing', 'PO required but not found on invoice')
            emit_event('invoice.info.requested', inv['id'], {'reason': f"Please provide the purchase order for invoice {ex.get('invoice_number', '')}."})
            return

        if not po:
            bad = bool(ex.get('po_number'))
            patch_invoice(inv['id'], {
                'matching_status': 'mismatch' if bad else 'no_po',
                'match_result': {
                    'po': {
                        'status': 'not_found' if bad else 'not_required',
                        'diffs': [{'type': 'po_not_found', 'po_number': ex.get('po_number')}] if bad else []
                    },
                    'lines': []
                }
            })
            emit_event('invoice.po.matched', inv['id'])
            return

        items = supabase.table('po_items').select('id,description,quantity,unit_price').eq('po_id', po['id']).execute().data or []
        diffs = []
        lines = []
        used = set()

        if po.get('vendor_id') != inv.get('vendor_id'):
            diffs.append({'type': 'vendor', 'message': 'PO belongs to a different vendor'})
        if po.get('status') in ['closed', 'cancelled', 'draft']:
            diffs.append({'type': 'po_status', 'message': f"PO is {po.get('status')}"})
        if inv_total > float(po.get('total', 0) or 0) * (1 + TOL):
            diffs.append({'type': 'total', 'invoice': inv_total, 'po': float(po.get('total', 0) or 0)})

        for i, l in enumerate(ex.get('line_items') or []):
            b = None
            bs = 0
            for p in items:
                if p['id'] in used: continue
                s = similarity(l.get('description', ''), p.get('description', ''))
                if s > bs:
                    bs = s
                    b = p
            if not b or bs < 0.4:
                diffs.append({'type': 'line_not_on_po', 'line': i+1, 'description': str(l.get('description', ''))[:80]})
                continue
            
            used.add(b['id'])
            lines.append({'line': i+1, 'po_item_id': b['id'], 'invoice_qty': l.get('quantity', 0)})
            
            l_qty = float(l.get('quantity', 0) or 0)
            b_qty = float(b.get('quantity', 0) or 0)
            l_price = float(l.get('unit_price', 0) or 0)
            b_price = float(b.get('unit_price', 0) or 0)

            if l_qty > b_qty:
                diffs.append({'type': 'quantity', 'line': i+1, 'invoice': l_qty, 'po': b_qty})
            if l_price > b_price * (1 + TOL):
                diffs.append({'type': 'price', 'line': i+1, 'invoice': l_price, 'po': b_price})

        ok = len(diffs) == 0
        patch_invoice(inv['id'], {
            'po_id': po['id'],
            'matching_status': '2_way_match' if ok else 'mismatch',
            'match_result': {
                'po': {
                    'status': 'match' if ok else 'mismatch',
                    'po_number': po['po_number'],
                    'diffs': diffs
                },
                'lines': lines
            }
        })
        audit_log(inv['id'], 'po_matched', 'PO match passed' if ok else 'PO match failed', {'diffs': diffs})
        emit_event('invoice.po.matched', inv['id'])

    except Exception as e:
        fail_invoice(inv['id'], e)

def process_grn_matching(invoice_id: str):
    inv = get_invoice(invoice_id)
    mr = inv.get('match_result') if inv else None
    if not inv or not mr or 'po' not in mr or 'grn' in mr or inv.get('status') != 'processing':
        return

    try:
        if not inv.get('po_id') or not mr.get('lines'):
            mr['grn'] = {'status': 'n/a'}
            patch_invoice(inv['id'], {'match_result': mr})
            emit_event('invoice.matched', inv['id'])
            return

        grns = supabase.table('goods_received').select('id,grn_number,received_date').eq('po_id', inv['po_id']).in_('status', ['received', 'partial']).order('received_date', desc=True).execute().data or []
        diffs = []
        
        if not grns:
            diffs.append({'type': 'no_grn', 'message': 'No goods received note for this PO'})
        else:
            grn_ids = [g['id'] for g in grns]
            gi = supabase.table('grn_items').select('po_item_id,quantity_received').in_('grn_id', grn_ids).execute().data or []
            recv = {}
            for r in gi:
                recv[r['po_item_id']] = recv.get(r['po_item_id'], 0) + float(r.get('quantity_received', 0) or 0)
            
            for l in mr['lines']:
                got = recv.get(l.get('po_item_id'), 0)
                inv_qty = float(l.get('invoice_qty', 0) or 0)
                if inv_qty > got:
                    diffs.append({'type': 'quantity', 'line': l.get('line'), 'invoice_qty': inv_qty, 'received_qty': got, 'difference': inv_qty - got})

        ok = len(diffs) == 0
        mr['grn'] = {
            'status': 'match' if ok else 'mismatch',
            'grn_number': grns[0]['grn_number'] if grns else None,
            'diffs': diffs
        }
        patch_invoice(inv['id'], {
            'grn_id': grns[0]['id'] if grns else None,
            'matching_status': '3_way_match' if ok and inv.get('matching_status') == '2_way_match' else 'mismatch',
            'match_result': mr
        })
        audit_log(inv['id'], 'grn_matched', 'GRN match passed' if ok else 'GRN match failed', {'diffs': diffs})
        emit_event('invoice.matched', inv['id'])

    except Exception as e:
        fail_invoice(inv['id'], e)

def process_duplicate_detection(invoice_id: str):
    inv = get_invoice(invoice_id)
    mr = inv.get('match_result') if inv else None
    if not inv or not mr or 'grn' not in mr or 'duplicates' in mr or inv.get('status') != 'processing':
        return

    try:
        found = []
        if inv.get('file_hash'):
            r = supabase.table('invoices').select('id').eq('file_hash', inv['file_hash']).neq('id', inv['id']).limit(5).execute().data or []
            for x in r:
                found.append({'id': x['id'], 'type': 'same_file', 'score': 100})
        
        if inv.get('vendor_id') and inv.get('invoice_number'):
            r = supabase.table('invoices').select('id,total,invoice_date').eq('vendor_id', inv['vendor_id']).eq('invoice_number', inv['invoice_number']).neq('id', inv['id']).limit(5).execute().data or []
            for x in r:
                same = float(x.get('total', 0) or 0) == float(inv.get('total', 0) or 0)
                strong = same and x.get('invoice_date') == inv.get('invoice_date')
                found.append({'id': x['id'], 'type': 'strong' if strong else 'exact', 'score': 100 if same else 90})

        inv_total = float(inv.get('total', 0) or 0)
        if inv.get('vendor_id') and inv_total > 0:
            r = supabase.table('invoices').select('id,invoice_number,invoice_date,po_id').eq('vendor_id', inv['vendor_id']).neq('id', inv['id']).gte('total', inv_total * 0.99).lte('total', inv_total * 1.01).limit(20).execute().data or []
            import datetime
            def parse_date(d):
                try:
                    return datetime.datetime.strptime(d, "%Y-%m-%d")
                except:
                    return None

            inv_d = parse_date(inv.get('invoice_date', ''))
            
            for x in r:
                if any(f['id'] == x['id'] for f in found): continue
                s = 40
                x_d = parse_date(x.get('invoice_date', ''))
                if x_d and inv_d and abs((x_d - inv_d).days) <= 7:
                    s += 20
                if x.get('po_id') and x['po_id'] == inv.get('po_id'):
                    s += 20
                if x.get('invoice_number') and inv.get('invoice_number') and levenshtein(x['invoice_number'], inv['invoice_number']) <= 2:
                    s += 20
                if s >= 60:
                    found.append({'id': x['id'], 'type': 'fuzzy', 'score': s})

        score = max((f['score'] for f in found), default=0)
        mr['duplicates'] = found[:10]
        patch_invoice(inv['id'], {
            'duplicate_score': score,
            'match_result': mr
        })
        audit_log(inv['id'], 'duplicate_checked', 'Possible duplicates found' if found else 'No duplicates', {'score': score})
        emit_event('invoice.duplicate.checked', inv['id'], {'score': score})

    except Exception as e:
        fail_invoice(inv['id'], e)
