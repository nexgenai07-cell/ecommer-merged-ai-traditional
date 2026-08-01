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

from django.contrib.auth import get_user_model   # NEW — FIX: propose_update_product ke andar current value fetch karne ke liye

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


def propose_create_product(session_key: str, user_id: int, name: str, price: float, stock: int,
                            category_id: int = None, description: str = "",
                            original_price: float = None, sku: str = None,
                            low_stock_threshold: int = None) -> dict:
    if not sku:
        sku = _generate_sku(name)

    payload = {
        'name': name, 'description': description or '', 'price': price,
        'original_price': original_price, 'stock': stock, 'sku': sku,
        'category': category_id, 'is_active': True,
    }
    if low_stock_threshold is not None:
        payload['low_stock_threshold'] = low_stock_threshold

    preview = {**{k: v for k, v in payload.items() if k != 'category'}, 'category_id': category_id}
    # NOTE: is function mein pehle se hi user_id sahi tarah pass ho raha tha —
    # yehi wo "sahi" pattern hai jo neeche baaki propose_* functions mein bhi apply kiya gaya hai.
    result = create_pending_action(session_key, user_id, 'create_product', payload, preview)
    return {
        'requires_confirmation': True,
        'action_id': result['action_id'],
        'action_type': 'create_product',
        'preview': preview,
        'expires_at': result['expires_at'],
    }


def execute_create_product(user, payload: dict) -> dict:
    """Confirm hone ke baad POST /api/v1/products/ call karta hai."""
    result = call_internal_api(user, 'POST', '/api/v1/products/', json_body=payload)
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'product': result['data']}


def propose_update_product(session_key: str, user_id: int, product_id: int, fields: dict) -> dict:
    """
    Product update ka preview. 'fields' dict mein jo bhi keys hain wahi
    update hongi (jaise {'price': 5000} ya {'stock': 20, 'name': 'New Name'}).
    Agar 'category_id' diya ho to 'category' mein translate karte hain.
    """
    fields = dict(fields)  # copy — original mutate na karein
    if 'category_id' in fields:
        fields['category'] = fields.pop('category_id')

    # NEW — FIX: pehle preview mein sirf NAYI values hoti thin, "purani
    # value" AI khud (apni memory se, kabhi galat) bana kar bolta tha —
    # isi wajah se "Rs. 30,000 -> Rs. 40,000" jaisa galat before-value
    # dikha tha jabke asal purani price 2,500 thi. Ab yahan current
    # product live fetch karke asal "from -> to" preview banate hain,
    # taake AI ko guess na karna pade.
    current_data = {}
    try:
        User = get_user_model()
        acting_user = User.objects.get(id=user_id)
        current_result = call_internal_api(acting_user, 'GET', f'/api/v1/products/{product_id}/')
        if current_result['success']:
            current_data = current_result['data'] or {}
    except Exception:
        current_data = {}  # fail-safe — agar fetch na ho paye to bhi preview banta rahe, sirf 'from' None dikhega

    changes = {}
    for key, new_value in fields.items():
        old_value = current_data.get(key)
        if key == 'category' and isinstance(old_value, dict):
            old_value = old_value.get('id')
        changes[key] = {'from': old_value, 'to': new_value}

    preview = {
        'action': 'update_product',
        'product_id': product_id,
        'fields': fields,       # backward-compat — purana shape bhi rakha
        'changes': changes,     # NEW — asal "from -> to" comparison, AI isi se sahi jawab likhega
    }
    pending_kwargs = {'product_id': product_id, 'fields': fields}
    # FIX — pehle yahan "create_pending_action(session_key, 'update_product', pending_kwargs, preview)"
    # likha tha, yani 'user_id' argument hi missing tha. Isse Python
    # arguments ko shift kar deta tha (user_id slot mein tool-name string
    # chali jati thi) aur akhri 'preview' argument bilkul missing reh
    # jata tha -> TypeError: "create_pending_action() missing 1 required
    # positional argument: 'preview'". Ab user_id sahi tarah pass ho raha hai.
    result = create_pending_action(session_key, user_id, 'update_product', pending_kwargs, preview)
    # FIX — pehle "action_id = create_pending_action(...)" likha tha, jo
    # poora {'action_id':..., 'expires_at':...} dict 'action_id' field mein
    # daal deta tha (string ki jagah). Ab 'result' se sahi field nikal rahe hain.
    return {
        'requires_confirmation': True,
        'action_id': result['action_id'],
        'action_type': 'update_product',
        'preview': preview,
        'expires_at': result['expires_at'],
    }


def execute_update_product(user, payload: dict) -> dict:
    """Confirm hone ke baad PATCH /api/v1/products/{id}/ call karta hai."""
    product_id = payload['product_id']
    fields = payload['fields']
    result = call_internal_api(user, 'PATCH', f'/api/v1/products/{product_id}/', json_body=fields)
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'product': result['data']}


def propose_delete_product(session_key: str, user_id: int, product_id: int) -> dict:
    """Product delete (soft delete — is_active=False) ka preview."""
    preview = {'action': 'delete_product', 'product_id': product_id}
    # FIX — yehi wo exact bug tha jo "test 2 aur test 3 delete karo" wale
    # request pe error de raha tha. Pehle 'user_id' argument missing tha.
    result = create_pending_action(session_key, user_id, 'delete_product', {'product_id': product_id}, preview)
    return {
        'requires_confirmation': True,
        'action_id': result['action_id'],
        'action_type': 'delete_product',
        'preview': preview,
        'expires_at': result['expires_at'],
    }


def execute_delete_product(user, payload: dict) -> dict:
    """Confirm hone ke baad DELETE /api/v1/products/{id}/ call karta hai (soft delete)."""
    product_id = payload['product_id']
    result = call_internal_api(user, 'DELETE', f'/api/v1/products/{product_id}/')
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'message': f'Product {product_id} deleted (soft delete — is_active=False).'}

# NEW — FIX: list_products/search sirf limited fields deta hai
# (product_id, name, price, stock, image, category_id) — description,
# original_price, sku, low_stock_threshold kabhi is se nahi aate. Isi
# wajah se AI in fields ko "not set" bol deta tha chahe DB mein value
# maujood ho — kyunke usay wo value kabhi tool se mili hi nahi thi.
# Ye alag, dedicated tool poori detail live fetch karta hai.
def get_product_details(user, product_id: int) -> dict:
    """
    Read-only. Ek product ki POORI detail Django API se fetch karta hai —
    description, original_price, sku, low_stock_threshold sab shamil.
    Admin ko product ki detail dikhane se pehle, ya update propose karne
    se pehle, ye tool call karna chahiye — list_products/search kaafi
    nahi hai.
    """
    result = call_internal_api(user, 'GET', f'/api/v1/products/{product_id}/')
    if not result['success']:
        return {'success': False, 'error': result['error']}

    p = result['data'] or {}
    category = p.get('category')
    category_id = category.get('id') if isinstance(category, dict) else category

    # FIX — ProductDetailSerializer (jo GET /products/{id}/ use karta
    # hai) 'primary_image' ya 'image' key deta hi nahi — ye sirf
    # ProductListSerializer mein hai. Detail endpoint 'images' (list of
    # {id, image_url, is_primary}) deta hai. Pehle 'primary_image'/'image'
    # dhoondte the jo hamesha None milta tha, isi liye admin ko image
    # hamesha "not set" dikhti thi chahe DB mein maujood ho.
    images = p.get('images') or []
    primary = next((img for img in images if img.get('is_primary')), None) or (images[0] if images else None)
    image_url = primary.get('image_url') if primary else None

    return {
        'success': True,
        'product': {
            'product_id': p.get('id'),
            'name': p.get('name'),
            'category_id': category_id,
            'price': p.get('price'),
            'original_price': p.get('original_price'),
            'stock': p.get('stock'),
            'sku': p.get('sku'),
            'description': p.get('description'),
            'low_stock_threshold': p.get('low_stock_threshold'),
            'image': image_url,
            'is_active': p.get('is_active'),
        },
    }


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