# PATH: apps/ai/admin_tools/order_tools.py

# FLOW: registry.py se yahan aata hai. Same propose_*/execute_* pattern.
# Note: get_order_details/track_order lightweight endpoint use karte
# hain (AdminOrderFilterView) — koi "full order detail" admin endpoint
# nahi hai abhi.

from apps.ai.admin_tools.api_client import call_internal_api
from apps.ai.admin_tools.pending_actions import create_pending_action


def get_order_details(user, order_id: str) -> dict:
    """FLOW: Read-only — GET /api/v1/admin/orders/filter/?order_number=... hit karta hai"""
    """
    Read-only. AdminOrderFilterView se order dhoondta hai (order_number filter se).
    Note: ye halka data hai (items/shipping address shamil nahi) — sirf
    id, order_number, customer{name,phone}, total_amount, discount_amount,
    status, created_at milta hai.
    """
    result = call_internal_api(user, 'GET', '/api/v1/admin/orders/filter/', params={'order_number': order_id})
    if not result['success']:
        return {'success': False, 'error': result['error']}

    results = (result['data'] or {}).get('results', [])
    if not results:
        return {'success': False, 'error': f'Order {order_id} not found.'}

    return {'success': True, 'order': results[0]}


"""FLOW: propose_update_order ke payment-gate check ke liye — 'processing'/
'shipped'/'delivered' jaisi status sirf tab propose hoti hai jab order
'pending_payment' state mein na ho. get_order_details() (isi file mein
neeche) reuse karte hain — ek alag HTTP round-trip nahi, order_tools.py
ke andar hi function-call hai."""
PAYMENT_REQUIRED_STATUSES = {'processing', 'shipped', 'delivered'}


def propose_update_order(session_key: str, user, order_id: str, fields: dict) -> dict:
    """FLOW: preview banata hai — sirf 'status'/'tracking_number' allow karta hai"""
    """
    Order update ka preview. Backend endpoint (AdminOrderStatusUpdateView)
    sirf 'status' aur 'tracking_number' fields accept karta hai — agar
    admin ne koi aur field diya ho, us ko filter kar dete hain aur AI ko
    bata dete hain ke sirf ye 2 fields update ho sakti hain.
    """
    allowed_fields = {'status', 'tracking_number'}
    filtered_fields = {k: v for k, v in fields.items() if k in allowed_fields}
    ignored_fields = set(fields.keys()) - allowed_fields

    # NEW — CRITICAL FIX: pehle koi payment-status check nahi tha — admin
    # order ko 'processing'/'shipped'/'delivered' mein move karne ka
    # preview maang sakta tha chahe order abhi tak 'pending_payment' state
    # mein ho (customer ne payment abhi ki hi na ho). Ab preview banane se
    # PEHLE hi current order fetch karke check karte hain — agar order
    # abhi bhi pending_payment hai aur requested status payment maangti
    # hai, to preview banaye bina hi saaf error de dete hain (koi
    # pending_action create nahi hoti, is liye "Confirm karen?" kabhi
    # dikhta hi nahi is case mein).
    new_status = filtered_fields.get('status')
    if new_status in PAYMENT_REQUIRED_STATUSES:
        current = get_order_details(user, order_id)
        if not current['success']:
            return {'success': False, 'error': current['error']}
        current_status = (current['order'] or {}).get('status')
        if current_status == 'pending_payment':
            return {
                'success': False,
                'error': (
                    f"Order {order_id} ki payment abhi tak nahi hui hai (status: "
                    f"pending_payment) — is liye ise '{new_status}' mein move nahi "
                    f"kiya ja sakta. Customer ke payment complete karne ke baad hi "
                    f"status update ho sakega."
                ),
            }

    user_id = user.id
    preview = {
        'action': 'update_order',
        'order_id': order_id,
        'fields': filtered_fields,
    }
    if ignored_fields:
        preview['note'] = f"These fields are not supported by the order update endpoint and were ignored: {', '.join(ignored_fields)}"

    pending_kwargs = {'order_id': order_id, 'fields': filtered_fields}
    # FIX — pehle yahan 'user_id' argument missing tha (jaisa product/
    # category/inventory tools mein pehle tha), is liye arguments shift ho
    # kar TypeError: "create_pending_action() missing 1 required positional
    # argument: 'preview'" aata tha — order update kabhi propose hi nahi ho
    # pata tha. Ab user_id sahi tarah pass ho raha hai.
    result = create_pending_action(session_key, user_id, 'update_order', pending_kwargs, preview)
    # FIX — pehle poora {'action_id':..., 'expires_at':...} dict seedha
    # 'action_id' field mein daal diya jata tha (string ki jagah), aur
    # 'action_type' bhi missing tha (jo baaki tools ke response mein hai).
    return {
        'requires_confirmation': True,
        'action_id': result['action_id'],
        'action_type': 'update_order',
        'preview': preview,
        'expires_at': result['expires_at'],
    }


def execute_update_order(user, payload: dict) -> dict:
    """FLOW: confirm hone ke baad YAHAN asal order status update hota hai."""
    """Confirm hone ke baad PUT /api/v1/admin/orders/{order_number}/status/ call karta hai."""
    order_id = payload['order_id']
    fields = payload['fields']

    # FLOW → api_client.py → PUT /api/v1/admin/orders/{order_number}/status/
    # → apps/orders/views.py ka AdminOrderStatusUpdateView (stock restore,
    #   payment refund waghera bhi WAHAN automatically hoti hai)

    result = call_internal_api(user, 'PUT', f'/api/v1/admin/orders/{order_id}/status/', json_body=fields)
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'order_id': order_id, **result['data']}


def propose_cancel_order(session_key: str,user_id: int, order_id: str, reason: str = "") -> dict:
    """
    Order cancel ka preview. Note: backend endpoint 'reason' field store
    nahi karta (sirf status update karta hai) — lekin reason AuditLog mein
    save ho jayega (registry.py confirm_pending_action mein wired hai),
    taake koi record rahe ke cancel kyun kiya gaya.
    """
    preview = {'action': 'cancel_order', 'order_id': order_id, 'reason': reason}
    pending_kwargs = {'order_id': order_id, 'reason': reason}
    # FIX — same 'user_id' missing bug as propose_update_order above.
    result = create_pending_action(session_key, user_id, 'cancel_order', pending_kwargs, preview)
    return {
        'requires_confirmation': True,
        'action_id': result['action_id'],
        'action_type': 'cancel_order',
        'preview': preview,
        'expires_at': result['expires_at'],
    }


def execute_cancel_order(user, payload: dict) -> dict:
    """FLOW: confirm hone ke baad status='cancelled' bhejta hai —
    stock restore/refund backend (AdminOrderStatusUpdateView) khud handle karta hai."""
    """
    Confirm hone ke baad PUT .../status/ ke sath status='cancelled' bhejta hai.
    Backend ye khud automatically stock restore aur payment refund-mark
    kar deta hai (jaisa AdminOrderStatusUpdateView mein already likha hai).
    """
    order_id = payload['order_id']
    result = call_internal_api(user, 'PUT', f'/api/v1/admin/orders/{order_id}/status/', json_body={'status': 'cancelled'})
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'order_id': order_id, 'reason': payload.get('reason', ''), **result['data']}


def track_order(user, order_id: str) -> dict:
    """FLOW: get_order_details() ko hi reuse karta hai (upar wala function), sirf shape thora alag deta hai."""
    """
    Read-only. Poori "timeline" (status history) database mein exist nahi
    karti (koi OrderStatusHistory table nahi hai) — isliye timeline mein
    sirf current status + known timestamps deते hain, purani history nahi.
    """
    details = get_order_details(user, order_id)     # FLOW: isi file ka upar wala function
    if not details['success']:
        return details

    order = details['order']
    return {
        'success': True,
        'order_id': order.get('order_number'),
        'status': order.get('status'),
        'timeline': [
            {'event': 'created', 'timestamp': order.get('created_at')},
            {'event': 'current_status', 'status': order.get('status')},
        ],
        'note': 'Detailed status-change history is not tracked in the database — only current status is available.',
    }