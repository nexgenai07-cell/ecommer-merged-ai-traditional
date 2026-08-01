# PATH: apps/ai/admin_tools/analytics_tools.py

# FLOW: registry.py ke get_analytics_tools() se yahan aata hai. Sab
# READ-ONLY hain — koi confirmation-gating nahi (koi mutation nahi hoti).

import re
from datetime import date, timedelta

from apps.ai.admin_tools.api_client import call_internal_api     # FLOW → api_client.py (yahan se apps/analytics/views.py tak jata hai)

_SUPPORTED_RANGES = (
    'today', 'yesterday', 'last_7_days', 'last_30_days', 'last_90_days',
    'this_week', 'last_week', 'this_month', 'last_month',
    'this_year', 'last_year', 'all_time',
)


# FIX — Railway logs se confirm hua ke primary model (openai/gpt-oss-120b)
# bhi date_range ko reliably map nahi kar pa raha tha — "last year" pucha
# gaya aur model ne tool ko {'date_range': 'last_week'} bheja. Sirf
# system-prompt instructions is model ke liye kaafi nahi thin. Isliye
# ab CURRENT user message (chat_history NAHI) pe ek deterministic keyword
# scan chalate hain — agar confident match milta hai, wo value seedha
# admin_agent.py se prompt mein explicit hint ke tor par inject hoti hai,
# taake model ko khud "figure out" na karna pade.
_RANGE_PATTERNS = [
    (re.compile(r'\btoday\b|\baaj\b', re.IGNORECASE), 'today'),
    (re.compile(r'\byesterday\b', re.IGNORECASE), 'yesterday'),
    (re.compile(r'\blast\s*7\s*days?\b|\bpast\s*7\s*days?\b|\bpichl[ae]\s*7\s*din\b', re.IGNORECASE), 'last_7_days'),
    (re.compile(r'\blast\s*week\b|\bpichl[ae]\s*haft[ae]\b', re.IGNORECASE), 'last_week'),
    (re.compile(r'\bthis\s*week\b|\bis\s*haft[ae]\b', re.IGNORECASE), 'this_week'),
    (re.compile(r'\blast\s*90\s*days?\b|\bpichl[ae]\s*90\s*din\b', re.IGNORECASE), 'last_90_days'),
    (re.compile(r'\blast\s*30\s*days?\b|\bpichl[ae]\s*30\s*din\b', re.IGNORECASE), 'last_30_days'),
    (re.compile(r'\blast\s*month\b|\bpichl[ae]\s*mahin[ae]\b', re.IGNORECASE), 'last_month'),
    (re.compile(r'\bthis\s*month\b|\bis\s*mahin[ae]\b', re.IGNORECASE), 'this_month'),
    (re.compile(r'\blast\s*year\b|\bpichl[ae]\s*sa+l\b', re.IGNORECASE), 'last_year'),
    (re.compile(r'\bthis\s*year\b|\bis\s*sa+l\b', re.IGNORECASE), 'this_year'),
    (re.compile(r'\ball[\s-]*time\b|\bshuru\s*se\b|\boverall\b', re.IGNORECASE), 'all_time'),
]


def detect_date_range_hint(text: str) -> str | None:
    """
    Deterministic keyword scan (English + common Roman Urdu phrasing) of
    the CURRENT admin message only. Returns one of _SUPPORTED_RANGES if a
    confident match is found, else None (model falls back to its own
    judgement / the prompt's 'last_30_days' default).
    """
    if not text:
        return None
    for pattern, value in _RANGE_PATTERNS:
        if pattern.search(text):
            return value
    return None


def _resolve_date_range(date_range: str):
    """FLOW: Ye helper sab 4 tools ke andar call hota hai —
    'last_30_days' jaisa keyword ko actual start_date/end_date mein convert karta hai."""

    """
    'date_range' keyword ko (start_date, end_date) ISO strings mein
    convert karta hai. Na-pehchana-gaya keyword bhi silently
    'last_30_days' pe fallback ho jata hai (agent ko crash na kare).
    Returns (start_date_str_or_None, end_date_str_or_None).

    NOTE: 'last_week' aur 'last_year' pehle yahan missing thay — model
    inhe guess kar leta tha (e.g. jab admin "pichle saal" ya "pichle
    hafte" ki sales poochta), lekin ye keywords yahan unrecognized the
    aur silently 'last_30_days' (ya kabhi model ki apni ghalat guess se
    'last_7_days') pe chale jaate thay — isi wajah se har period ka
    jawab same aa raha tha.
    """
    today = date.today()
    key = (date_range or 'last_30_days').strip().lower()

    if key == 'today':
        start = end = today
    elif key == 'yesterday':
        start = end = today - timedelta(days=1)
    elif key == 'last_7_days':
        start, end = today - timedelta(days=7), today
    elif key == 'last_90_days':
        start, end = today - timedelta(days=90), today
    elif key == 'this_week':
        start, end = today - timedelta(days=today.weekday()), today
    elif key == 'last_week':
        this_week_start = today - timedelta(days=today.weekday())
        start = this_week_start - timedelta(days=7)
        end = this_week_start - timedelta(days=1)
    elif key == 'this_month':
        start, end = today.replace(day=1), today
    elif key == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        start, end = last_month_end.replace(day=1), last_month_end
    elif key == 'this_year':
        start, end = today.replace(month=1, day=1), today
    elif key == 'last_year':
        start = today.replace(year=today.year - 1, month=1, day=1)
        end = today.replace(year=today.year - 1, month=12, day=31)
    elif key == 'all_time':
        return None, None
    else:
        # covers 'last_30_days' aur koi bhi unrecognized value
        start, end = today - timedelta(days=30), today

    return start.isoformat(), end.isoformat()


def sales_report_tool(user, date_range: str = "last_30_days") -> dict:
    """FLOW: registry.py se call hota hai → _resolve_date_range() se dates banti hain
    → api_client.py se GET /api/v1/analytics/sales/ hit hota hai
    → apps/analytics/views.py tak request jati hai → response yahan wapis aata hai"""

    """GET /api/v1/analytics/sales/ — order count + revenue, daily grouped."""
    start_date, end_date = _resolve_date_range(date_range)
    params = {'period': 'daily'}
    if start_date:
        params['start_date'] = start_date
    if end_date:
        params['end_date'] = end_date

    result = call_internal_api(user, 'GET', '/api/v1/analytics/sales/', params=params)
    if not result['success']:
        return {'success': False, 'error': result['error']}

    rows = (result['data'] or {}).get('data', [])
    total_orders = sum(r.get('total_orders', 0) or 0 for r in rows)
    total_revenue = sum(r.get('total_revenue', 0) or 0 for r in rows)
    total_units = sum(r.get('total_units', 0) or 0 for r in rows)  # FIX — ab SalesReportView 'total_units' bhejta hai

    return {
        'success': True,
        'date_range': date_range,
        'period': {'start': start_date, 'end': end_date},
        'summary': {'days_with_data': len(rows), 'daily_breakdown': rows},
        'totals': {
            'total_orders': int(total_orders), 
            'total_revenue': float(total_revenue),
            'total_units_sold': int(total_units),  # FIX
        },
    }


def revenue_report_tool(user, date_range: str = "last_30_days") -> dict:
    """FLOW: sales_report_tool() jaisa hi pattern, /api/v1/analytics/revenue/ hit karta hai"""

    """GET /api/v1/analytics/revenue/ — revenue grouped by period (cancelled orders excluded)."""
    start_date, end_date = _resolve_date_range(date_range)
    params = {'period': 'daily'}
    if start_date:
        params['start_date'] = start_date
    if end_date:
        params['end_date'] = end_date

    result = call_internal_api(user, 'GET', '/api/v1/analytics/revenue/', params=params)
    if not result['success']:
        return {'success': False, 'error': result['error']}

    rows = (result['data'] or {}).get('data', [])
    total_revenue = sum(r.get('revenue', 0) or 0 for r in rows)

    return {
        'success': True,
        'date_range': date_range,
        'period': {'start': start_date, 'end': end_date},
        'revenue_breakdown': {
            'by_period': rows, 
            'total_revenue': float(total_revenue)
        },
    }


def best_sellers_tool(user, date_range: str = "last_30_days", limit: int = 5) -> dict:
    """FLOW: /api/v1/analytics/products/best-sellers/ hit karta hai"""
    """GET /api/v1/analytics/products/best-sellers/ — top products by units sold."""
    start_date, end_date = _resolve_date_range(date_range)
    params = {'limit': limit}
    if start_date:
        params['start_date'] = start_date
    if end_date:
        params['end_date'] = end_date

    result = call_internal_api(user, 'GET', '/api/v1/analytics/products/best-sellers/', params=params)
    if not result['success']:
        return {'success': False, 'error': result['error'], 'best_sellers': []}

    rows = result['data'] or []
    best_sellers = [
        {
            'product_id': r.get('product_id'),
            'name': r.get('product_name'),
            'units_sold': int(r.get('total_sold', 0) or 0),
            'revenue': float(r.get('total_revenue', 0.0) or 0.0),
        }
        for r in rows
    ]

    return {
        'success': True,
        'date_range': date_range,
        'period': {'start': start_date, 'end': end_date},
        'best_sellers': best_sellers,
    }


def customer_growth_tool(user, date_range: str = "last_30_days") -> dict:
    """FLOW: /api/v1/analytics/customers/growth/ hit karta hai."""
    
    """
    GET /api/v1/analytics/customers/growth/ — new customer count per period.
    """
    start_date, end_date = _resolve_date_range(date_range)
    params = {'period': 'daily'}
    if start_date:
        params['start_date'] = start_date
    if end_date:
        params['end_date'] = end_date

    result = call_internal_api(user, 'GET', '/api/v1/analytics/customers/growth/', params=params)
    if not result['success']:
        return {'success': False, 'error': result['error']}

    rows = result['data'] or []
    total_new_customers = sum(r.get('new_customers', 0) or 0 for r in rows)

    return {
        'success': True,
        'date_range': date_range,
        'period': {'start': start_date, 'end': end_date},
        'new_customers': int(total_new_customers),
        'by_period': rows,
        'retention': None,
        'note': 'Retention is not tracked in the database yet — only new-customer counts are available.',
    }