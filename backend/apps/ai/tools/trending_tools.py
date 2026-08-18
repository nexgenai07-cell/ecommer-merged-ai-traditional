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
            if not detail.get('success'):
                continue

            raw = detail['product']

            # NEW — CRITICAL FIX: get_product_details_tool() apni khud ki
            # Django REST API ka RAW response wapis karta hai — jiski shape
            # search_products_tool ke normalized products se BILKUL ALAG
            # hai ('id' na ke 'product_id', 'images' list na ke ek 'image'
            # URL, 'category' dict na ke flat 'category_id'). Isi wajah se
            # response_metadata.py ka _add_product() is product ka
            # product_id() dhoondta tha, na milta, aur PORA product
            # SILENTLY drop ho jata tha — customer ko text mein "top-
            # selling products" ki poori list dikhti thi lekin metadata
            # (image cards) HAMESHA khali rehta tha. Ab yahan hi shape ko
            # normalize kar dete hain, taake ye baaki tools jaisa hi
            # consistent format wapis kare.
            cat = raw.get('category')
            category_name = cat.get('name') if isinstance(cat, dict) else None
            category_id = cat.get('id') if isinstance(cat, dict) else cat

            images = raw.get('images') or []
            primary = next((img for img in images if isinstance(img, dict) and img.get('is_primary')), None) or (images[0] if images else None)
            image_url = (primary.get('image_url') if isinstance(primary, dict) else None) or raw.get('image') or raw.get('primary_image')

            stock = raw.get('stock', 0) or 0

            # NEW — FIX: out-of-stock product dikhana band — customer
            # khareed nahi sakta, aur "Add to Cart" tap karne pe silently
            # fail ho jata tha.
            if raw.get('is_active') is False or stock <= 0:
                continue

            # NEW — price/original_price DRF se string ("20000.00") ke
            # roop mein aati hain — float mein convert taake baaki tools
            # (search_products_tool) jaisa hi consistent numeric type ho
            def _to_float(v):
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            products.append({
                'product_id':      raw.get('id'),
                'name':             raw.get('name'),
                'category':         category_name,
                'category_id':      category_id,
                'price':            _to_float(raw.get('price')),
                'original_price':   _to_float(raw.get('original_price')),
                'in_stock':         stock > 0,
                'stock':            stock,
                'description':      raw.get('description', ''),
                'image':            image_url,
            })

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