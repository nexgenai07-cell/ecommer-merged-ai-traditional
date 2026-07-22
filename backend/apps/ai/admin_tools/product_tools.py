# PATH: apps/ai/admin_tools/product_tools.py
#
# Admin Operations Agent ke product tools — Day 2 REAL implementation.
# HTTP approach (PDF requirement): tools seedha DB nahi chhoote, apni
# Django REST API ko call karte hain (call_internal_api se).
#
# Pattern: har mutating action ke liye do functions:
#   propose_*()  — preview banata hai, pending_actions cache mein store karta hai
#   execute_*()  — confirm hone ke baad asal HTTP request bhejta hai
# registry.py ka confirm_pending_action tool in dono ko jorta hai.

import random
import string

from apps.ai.admin_tools.api_client import call_internal_api
from apps.ai.admin_tools.pending_actions import create_pending_action


def _generate_sku(name: str) -> str:
    """
    Product model mein SKU required + unique hai, lekin PDF ke tool
    signature mein SKU mention nahi — is liye agar admin na de, khud
    generate karte hain (product naam se prefix + random 4 digits).
    """
    prefix = ''.join(ch for ch in name.upper() if ch.isalnum())[:6] or 'PROD'
    suffix = ''.join(random.choices(string.digits, k=4))
    return f"{prefix}-{suffix}"


def propose_create_product(session_key: str, name: str, price: float, stock: int,
                            category_id: int = None, description: str = "",
                            original_price: float = None, sku: str = None,
                            low_stock_threshold: int = None) -> dict:
    """Product creation ka preview banata hai — POST abhi nahi bheji jati."""
    if not sku:
        sku = _generate_sku(name)

    payload = {
        'name': name,
        'description': description or '',
        'price': price,
        'original_price': original_price,
        'stock': stock,
        'sku': sku,
        'category': category_id,  # serializer field ka naam 'category' hai, 'category_id' nahi
        'is_active': True,
    }
    if low_stock_threshold is not None:
        payload['low_stock_threshold'] = low_stock_threshold

    preview = {'action': 'create_product', **{k: v for k, v in payload.items() if k != 'category'}, 'category_id': category_id}
    action_id = create_pending_action(session_key, 'create_product', payload, preview)
    return {'requires_confirmation': True, 'action_id': action_id, 'preview': preview}


def execute_create_product(user, payload: dict) -> dict:
    """Confirm hone ke baad POST /api/v1/products/ call karta hai."""
    result = call_internal_api(user, 'POST', '/api/v1/products/', json_body=payload)
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'product': result['data']}


def propose_update_product(session_key: str, product_id: int, fields: dict) -> dict:
    """
    Product update ka preview. 'fields' dict mein jo bhi keys hain wahi
    update hongi (jaise {'price': 5000} ya {'stock': 20, 'name': 'New Name'}).
    Agar 'category_id' diya ho to 'category' mein translate karte hain.
    """
    fields = dict(fields)  # copy — original mutate na karein
    if 'category_id' in fields:
        fields['category'] = fields.pop('category_id')

    preview = {'action': 'update_product', 'product_id': product_id, 'fields': fields}
    pending_kwargs = {'product_id': product_id, 'fields': fields}
    action_id = create_pending_action(session_key, 'update_product', pending_kwargs, preview)
    return {'requires_confirmation': True, 'action_id': action_id, 'preview': preview}


def execute_update_product(user, payload: dict) -> dict:
    """Confirm hone ke baad PATCH /api/v1/products/{id}/ call karta hai."""
    product_id = payload['product_id']
    fields = payload['fields']
    result = call_internal_api(user, 'PATCH', f'/api/v1/products/{product_id}/', json_body=fields)
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'product': result['data']}


def propose_delete_product(session_key: str, product_id: int) -> dict:
    """Product delete (soft delete — is_active=False) ka preview."""
    preview = {'action': 'delete_product', 'product_id': product_id}
    action_id = create_pending_action(session_key, 'delete_product', {'product_id': product_id}, preview)
    return {'requires_confirmation': True, 'action_id': action_id, 'preview': preview}


def execute_delete_product(user, payload: dict) -> dict:
    """Confirm hone ke baad DELETE /api/v1/products/{id}/ call karta hai (soft delete)."""
    product_id = payload['product_id']
    result = call_internal_api(user, 'DELETE', f'/api/v1/products/{product_id}/')
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'message': f'Product {product_id} deleted (soft delete — is_active=False).'}

def list_products(user, category_id: int = None, search: str = None, limit: int = 20) -> dict:
    """
    Read-only. Products list karta hai (optional category/search filter ke sath).
    GET /api/v1/products/search/ use karta hai — admin authenticated hone ki
    wajah se active + inactive dono products dikhega (get_queryset mein
    already yehi logic hai: sirf non-admin ke liye is_active filter lagta hai).
    """
    params = {}
    if category_id:
        params['category_id'] = category_id
    if search:
        params['q'] = search

    result = call_internal_api(user, 'GET', '/api/v1/products/search/', params=params)
    if not result['success']:
        return {'success': False, 'error': result['error'], 'products': []}

    data = result['data'] or {}
    results = data.get('results', data if isinstance(data, list) else [])

    products = [
        {
            'product_id': p.get('id'),
            'category_id': p.get('category', {}).get('id') if isinstance(p.get('category'), dict) else None,
            'name': p.get('name'),
            'price': p.get('price'),
            'stock': p.get('stock'),
            'image': p.get('primary_image'),
        }
        for p in results[:limit]
    ]

    return {'success': True, 'products': products, 'total_found': len(products)}