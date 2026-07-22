# PATH: apps/ai/admin_tools/customer_tools.py
#
# Read-only tool — customer records unke real ID, order count, aur
# total spending ke sath deta hai. GET /api/v1/admin/customers/ (jo
# already CustomerAdminSerializer se id/total_orders/total_spent deta
# hai) use karta hai.

from apps.ai.admin_tools.api_client import call_internal_api


def list_customers(user, search: str = None) -> dict:
    """
    GET /api/v1/admin/customers/?search=... — plain array return karta hai
    (pagination_class = None), har entry mein id, name, phone, email,
    total_orders, total_spent, created_at hota hai.
    """
    params = {}
    if search:
        params['search'] = search

    result = call_internal_api(user, 'GET', '/api/v1/admin/customers/', params=params)
    if not result['success']:
        return {'success': False, 'error': result['error'], 'customers': []}

    data = result['data'] or []
    customers = [
        {
            'customer_id': c.get('id'),
            'name': c.get('name'),
            'phone': c.get('phone'),
            'email': c.get('email'),
            'total_orders': c.get('total_orders'),
            'total_spent': c.get('total_spent'),
            'created_at': c.get('created_at'),
        }
        for c in data
    ]

    return {'success': True, 'customers': customers, 'total_found': len(customers)}