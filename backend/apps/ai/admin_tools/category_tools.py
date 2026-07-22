# PATH: apps/ai/admin_tools/category_tools.py

# FLOW: registry.py se yahan aata hai. Product tools jaisa hi pattern:
# propose_* = sirf preview banata hai, execute_* = confirm hone ke baad
# asal HTTP call karta hai apni khud ki Django API ko.

from apps.ai.admin_tools.api_client import call_internal_api     # FLOW → api_client.py (asal HTTP request yahan se jati hai)
from apps.ai.admin_tools.pending_actions import create_pending_action       # FLOW → pending_actions.py (Redis mein preview store hoti hai)


def propose_create_category(session_key: str, name: str, description: str = "") -> dict:

    """FLOW: registry.py ke create_category tool se call hota hai.
    Yahan koi DB/API change NAHI hota — sirf Redis mein pending action save hoti hai."""

    """
    Category creation ka preview. Note: CategorySerializer mein 'slug' ya
    'parent_category' fields nahi hain (jo PDF ne mention kiye thay) —
    actual model 'description' aur 'image' rakhta hai. Isi mutabiq bana rahe hain.
    """
    payload = {'name': name, 'description': description or ''}
    preview = {'action': 'create_category', **payload}
    action_id = create_pending_action(session_key, 'create_category', payload, preview)

    # FLOW: ye {action_id, preview} wapis Agent ko jata hai, jo admin ko dikha kar confirm maangta hai

    return {'requires_confirmation': True, 'action_id': action_id, 'preview': preview}


def execute_create_category(user, payload: dict) -> dict:

    """FLOW: registry.py ke confirm_pending_action tool se call hota hai
    (jab admin 'haan' bole). YAHAN ASAL CATEGORY BANTI HAI."""

    # FLOW → api_client.py → POST /api/v1/categories/ → apps/categories/views.py CategoryViewSet.create()

    result = call_internal_api(user, 'POST', '/api/v1/categories/', json_body=payload)
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'category': result['data']}


def propose_update_category(session_key: str, category_id: int, fields: dict) -> dict:
    preview = {'action': 'update_category', 'category_id': category_id, 'fields': fields}
    pending_kwargs = {'category_id': category_id, 'fields': fields}
    action_id = create_pending_action(session_key, 'update_category', pending_kwargs, preview)
    return {'requires_confirmation': True, 'action_id': action_id, 'preview': preview}


def execute_update_category(user, payload: dict) -> dict:
    category_id = payload['category_id']
    fields = payload['fields']

    # FLOW → api_client.py → PATCH /api/v1/categories/{id}/
    
    result = call_internal_api(user, 'PATCH', f'/api/v1/categories/{category_id}/', json_body=fields)
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'category': result['data']}


def propose_delete_category(session_key: str, category_id: int) -> dict:
    preview = {'action': 'delete_category', 'category_id': category_id}
    action_id = create_pending_action(session_key, 'delete_category', {'category_id': category_id}, preview)
    return {'requires_confirmation': True, 'action_id': action_id, 'preview': preview}


def execute_delete_category(user, payload: dict) -> dict:
    category_id = payload['category_id']

    # FLOW → api_client.py → DELETE /api/v1/categories/{id}/

    result = call_internal_api(user, 'DELETE', f'/api/v1/categories/{category_id}/')
    if not result['success']:
        return {'success': False, 'error': result['error']}
    return {'success': True, 'message': f'Category {category_id} deleted.'}


def get_categories(user) -> dict:
    """FLOW: Read-only tool — koi confirmation nahi chahiye, seedha call ho jata hai."""
    """Read-only — confirmation ki zaroorat nahi. GET /api/v1/categories/ (koi pagination nahi)."""
    # FLOW → api_client.py → GET /api/v1/categories/
    result = call_internal_api(user, 'GET', '/api/v1/categories/')
    if not result['success']:
        return {'success': False, 'error': result['error'], 'categories': []}
    return {'success': True, 'categories': result['data']}