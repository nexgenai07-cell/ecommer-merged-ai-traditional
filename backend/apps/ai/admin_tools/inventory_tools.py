# PATH: apps/ai/admin_tools/inventory_tools.py

# FLOW: registry.py se yahan aata hai. Note: koi alag "Inventory"
# endpoint nahi hai — stock seedha Product model ka field hai, is liye
# ye tools bhi Product endpoints hi use karte hain (api_client.py se).

from apps.ai.admin_tools.api_client import call_internal_api
from apps.ai.admin_tools.pending_actions import create_pending_action


def check_inventory(user, product_id: int) -> dict:
    """FLOW: Read-only — GET /api/v1/products/{id}/ se stock nikalta hai."""
    """Read-only — GET /api/v1/products/{id}/ se stock nikalta hai."""
    result = call_internal_api(user, 'GET', f'/api/v1/products/{product_id}/')
    if not result['success']:
        return {'success': False, 'error': result['error']}

    data = result['data']
    return {
        'success': True,
        'product_id': data.get('id'),
        'name': data.get('name'),
        'quantity': data.get('stock'),
        'low_stock_threshold': data.get('low_stock_threshold'),
        'in_stock': data.get('in_stock'),
    }


def propose_update_inventory(session_key: str, product_id: int, quantity: int) -> dict:
    """FLOW: registry.py ke update_inventory tool se call hota hai — preview banata hai."""
    """Stock update ka preview — asal PATCH confirm ke baad."""
    preview = {'action': 'update_inventory', 'product_id': product_id, 'new_quantity': quantity}
    pending_kwargs = {'product_id': product_id, 'quantity': quantity}
    action_id = create_pending_action(session_key, 'update_inventory', pending_kwargs, preview)
    return {'requires_confirmation': True, 'action_id': action_id, 'preview': preview}


def execute_update_inventory(user, payload: dict) -> dict:
    """FLOW: confirm_pending_action se call hota hai — YAHAN asal stock update hota hai."""
    """Confirm hone ke baad PATCH /api/v1/products/{id}/ sirf 'stock' field ke sath."""
    product_id = payload['product_id']
    quantity = payload['quantity']
    # FLOW → api_client.py → PATCH /api/v1/products/{id}/ (sirf 'stock' field)
    result = call_internal_api(user, 'PATCH', f'/api/v1/products/{product_id}/', json_body={'stock': quantity})
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'product_id': product_id, 'quantity': quantity}


def low_stock(user, threshold: int = None) -> dict:

    """FLOW: Read-only — GET /api/v1/products/low-stock/ hit karta hai,
    phir agar admin ne extra threshold diya ho to client-side filter lagata hai."""
    
    """
    Read-only. GET /api/v1/products/low-stock/ har product ke apne
    low_stock_threshold ke against check karta hai (endpoint khud koi
    query param accept nahi karta — ye ek existing limitation hai jo
    hum ne code mein dekhi). Agar admin ne extra 'threshold' diya hai,
    hum client-side pe additional filter laga dete hain (results ko
    us threshold se aur chhaanti hai) — taake tool ka contract match ho.
    """
    result = call_internal_api(user, 'GET', '/api/v1/products/low-stock/')
    if not result['success']:
        return {'success': False, 'error': result['error'], 'low_stock_products': []}

    products = result['data'] or []
    mapped = [
        {'product_id': p['id'], 'name': p['name'], 'quantity': p['stock']}
        for p in products
    ]

    if threshold is not None:
        mapped = [p for p in mapped if p['quantity'] <= threshold]

    return {'success': True, 'low_stock_products': mapped}