# PATH: apps/ai/admin_tools/api_client.py

# FLOW: product_tools.py/category_tools.py/inventory_tools.py/order_tools.py
# ke execute_* functions se yahan aata hai. Ye file ek REAL HTTP request
# banati hai project ki apni Django REST API ko — jaise koi browser
# request bhej raha ho.

import requests
from django.conf import settings
from rest_framework_simplejwt.tokens import AccessToken


def _get_base_url():
    return getattr(settings, 'INTERNAL_API_URL', 'http://localhost:8000')


def _mint_token_for_user(user):
    """Admin user ke liye server-side ek fresh short-lived JWT banata hai."""
    return str(AccessToken.for_user(user))


def call_internal_api(user, method: str, path: str, json_body: dict = None, params: dict = None) -> dict:
    """
    Django REST API ko authenticated HTTP request bhejta hai, jaise wo
    request khud admin user ne bheji ho (JWT ke sath).

    Args:
        user:      Django User object (admin) — jiski taraf se request jayegi
        method:    'GET', 'POST', 'PATCH', 'DELETE'
        path:      Endpoint path, jaise '/api/v1/products/'
        json_body: POST/PATCH ke liye request body
        params:    GET ke liye query params

    Returns:
        dict: {'success': bool, 'status_code': int, 'data': ..., 'error': ...}

    Ye function har real admin tool istemal karega — taake HTTP-calling
    logic, error handling, aur auth ek hi jagah likha ho.
    """

    # FLOW: admin ke liye ek FRESH JWT yahan generate hota hai (login
    # dobara karne ki zaroorat nahi — hum already jaante hain ye
    # authenticated admin session hai)

    token = _mint_token_for_user(user)
    url = f"{_get_base_url()}{path}"
    headers = {'Authorization': f'Bearer {token}'}

    # FLOW: YAHAN ASAL REQUEST JAATI HAI — project ki apni Django REST
    # API ko (jaise POST /api/v1/products/) — matlab ProductViewSet
    # (apps/products/views.py) tak jaata hai, bilkul normal DRF request
    # ki tarah, permission checks (IsAdmin) bhi wahan lagte hain

    try:
        response = requests.request(
            method=method.upper(),
            url=url,
            json=json_body,
            params=params,
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as e:
        return {'success': False, 'status_code': None, 'data': None, 'error': f'Request failed: {e}'}

    try:
        data = response.json() if response.content else None
    except ValueError:
        data = None

    if response.status_code >= 400:
        error_msg = data.get('error') if isinstance(data, dict) and 'error' in data else (data or response.text)
        return {'success': False, 'status_code': response.status_code, 'data': data, 'error': error_msg}

    # ... response parse karke wapis product_tools.py ko deta hai
    return {'success': True, 'status_code': response.status_code, 'data': data, 'error': None}