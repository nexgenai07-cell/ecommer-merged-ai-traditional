# PATH: apps/ai/tools/trending_tools.py
#
# FLOW: registry.py se yahan aata hai (get_trending_products tool). Customer
# ko kabhi bhi sales figures / revenue / order-counts jaisa business data
# nahi dikhana chahiye (wo sirf admin_tools/analytics_tools.py ke through,
# sirf logged-in ADMIN ke liye hai). Lekin jab customer "aaj kitni sales
# hui" jaisa sawal poochta hai, uska ASAL intent aksar ye hota hai ke
# "abhi kya trending/popular hai" — is liye seedha "mere paas data nahi"
# kehne ke bajaye, hum use TOP-SELLING PRODUCTS dikhate hain (jo bilkul
# safe, customer-facing info hai) taake wo dead-end pe na ruke.
#
# Ye Qdrant use NAHI karta (semantic search nahi chahiye — "best-selling"
# ek exact, deterministic ranking hai) — seedha OrderItem ORM se aggregate
# karta hai, phir har product ki poori detail (price/image/stock)
# get_product_details_tool() se leta hai (wahi function jo compare_products
# bhi reuse karta hai — proven, consistent field shape).

from django.db.models import Sum

from apps.orders.models import OrderItem
from .product_tools import get_product_details_tool   # FLOW → product_tools.py


def get_trending_products_tool(limit: int = 5) -> dict:
    """
    FLOW: registry.py ke get_trending_products tool se call hota hai.

    Cancelled orders ko chhod kar, OrderItem.quantity ka sum le kar
    top-selling product_ids nikaalta hai, phir har ek ke liye
    get_product_details_tool() se real-time price/stock/image leta hai.

    Returns:
        dict with 'products' list (customer-safe fields only — koi
        revenue/units_sold jaisa business number nahi) aur 'total_found'.
    """
    try:
        top_rows = (
            OrderItem.objects
            .exclude(order__status='cancelled')
            .values('product_id')
            .annotate(total_units=Sum('quantity'))
            .order_by('-total_units')[:max(limit, 1)]
        )

        products = []
        for row in top_rows:
            pid = row['product_id']
            if not pid:
                continue
            detail = get_product_details_tool(pid)
            if detail.get('success'):
                products.append(detail['product'])

        if not products:
            return {
                'success': True,
                'products': [],
                'total_found': 0,
                'message': 'No sales data available yet to determine trending products.',
            }

        return {
            'success': True,
            'products': products,
            'total_found': len(products),
        }

    except Exception as e:
        return {'success': False, 'error': str(e), 'products': [], 'total_found': 0}