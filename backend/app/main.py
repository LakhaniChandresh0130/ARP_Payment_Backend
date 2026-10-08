import asyncio
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, Request
from typing import Dict, Any, Optional

from app.services.supabase_client import supabase
from app.workflows.intake import process_intake
from app.workflows.classification import process_classification
from app.workflows.matching import process_po_matching, process_grn_matching, process_duplicate_detection
from app.workflows.risk import process_risk_analysis
from app.workflows.approval import process_approval_routing
from app.workflows.notification import process_notification
from app.workflows.payment import process_payment_preparation
from app.workflows.tally import process_tally_export

ROUTES = {
    'invoice.created': process_intake,
    'invoice.ocr.completed': process_classification,
    'invoice.classified': process_po_matching,
    'invoice.po.matched': process_grn_matching,
    'invoice.matched': process_duplicate_detection,
    'invoice.duplicate.checked': process_risk_analysis,
    'invoice.risk.scored': process_approval_routing,
    'invoice.approval.required': process_notification,
    'invoice.flagged': process_notification,
    'invoice.info.requested': process_notification,
    'invoice.rejected': process_notification,
    'invoice.approved': process_payment_preparation,
    'invoice.payment.ready': process_notification,
    'invoice.exported': process_notification,
    'tally.export.requested': process_tally_export
}


def _event_row_id(event: Dict[str, Any]) -> Optional[int]:
    """invoice_events.id is bigint. Webhooks sometimes send a fake UUID instead."""
    raw = event.get('id')
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _resolve_event_id(event: Dict[str, Any]) -> Optional[int]:
    event_id = _event_row_id(event)
    if event_id is not None:
        return event_id
    if not supabase:
        return None
    invoice_id = event.get('invoice_id')
    event_type = event.get('event_type')
    if not invoice_id or not event_type:
        return None
    try:
        res = supabase.table('invoice_events') \
            .select('id,attempts') \
            .eq('invoice_id', invoice_id) \
            .eq('event_type', event_type) \
            .is_('processed_at', 'null') \
            .order('id', desc=True) \
            .limit(1) \
            .execute()
        if res.data:
            event['id'] = res.data[0]['id']
            if event.get('attempts') is None:
                event['attempts'] = res.data[0].get('attempts', 0)
            return res.data[0]['id']
    except Exception as e:
        print(f"[Dispatch] Could not resolve event id: {e}")
    return None


def _mark_event(event: Dict[str, Any], data: Dict[str, Any]):
    if not supabase:
        return
    event_id = _resolve_event_id(event)
    if event_id is None:
        print("[Dispatch] Skipping invoice_events update: missing numeric event id")
        return
    try:
        supabase.table('invoice_events').update(data).eq('id', event_id).execute()
    except Exception as e:
        print(f"[Dispatch] Failed to update event {event_id}: {e}")


def dispatch_event(event: Dict[str, Any]):
    event_type = event.get('event_type')
    invoice_id = event.get('invoice_id')
    handler = ROUTES.get(event_type)

    if not handler:
        print(f"[Dispatch] No handler for event type: {event_type} (acking)")
        _mark_event(event, {
            'processed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'error': None
        })
        return

    try:
        print(f"[Dispatch] → {event_type} | invoice={invoice_id} | event={event.get('id')}")

        if handler == process_notification or handler == process_tally_export:
            handler(event)
        else:
            handler(invoice_id)

        print(f"[Dispatch] ✓ Done: {event_type} | invoice={invoice_id}")
        _mark_event(event, {
            'processed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'error': None
        })

    except Exception as e:
        print(f"[Dispatch] ✗ Error: {event_type} | invoice={invoice_id} | {e}")
        attempts = int(event.get('attempts') or 0) + 1
        _mark_event(event, {
            'attempts': attempts,
            'error': str(e)[:300]
        })


def reset_stale_model_errors():
    """Unstick events that failed only because Groq retired the old Llama model."""
    if not supabase:
        return
    try:
        res = supabase.table('invoice_events') \
            .select('id,error') \
            .is_('processed_at', 'null') \
            .execute()
        for ev in res.data or []:
            err = (ev.get('error') or '')
            if 'model_not_found' in err or 'llama-3.3-70b-versatile' in err:
                supabase.table('invoice_events').update({
                    'attempts': 0,
                    'error': None
                }).eq('id', ev['id']).execute()
                print(f"[Startup] Reset stuck event {ev['id']} after Groq model change")
    except Exception as e:
        print(f"[Startup] Could not reset stuck events: {e}")


async def poll_events():
    """Background task to poll for ALL unprocessed events every 10 seconds."""
    while True:
        if supabase:
            try:
                res = supabase.table('invoice_events') \
                    .select('*') \
                    .is_('processed_at', 'null') \
                    .lt('attempts', 5) \
                    .order('created_at') \
                    .limit(50) \
                    .execute()

                events = res.data or []
                if events:
                    print(f"[Poller] Found {len(events)} unprocessed event(s)")
                for ev in events:
                    dispatch_event(ev)
            except Exception as e:
                print(f"[Poller] Error: {e}")

        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    reset_stale_model_errors()
    task = asyncio.create_task(poll_events())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan, title="LedgerFlow Backend")

@app.post("/events")
async def receive_event(request: Request, background_tasks: BackgroundTasks):
    """Webhook endpoint for receiving events immediately."""
    try:
        body = await request.json()
        record = body.get('record')
        if record and (record.get('id') or record.get('invoice_id')):
            background_tasks.add_task(dispatch_event, record)
            return {"status": "accepted", "event_id": record.get('id')}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {"status": "ignored"}

@app.get("/health")
def health_check():
    return {"status": "ok"}
