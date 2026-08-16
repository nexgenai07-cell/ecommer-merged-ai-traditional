# PATH: apps/ai/tools/product_tools.py

# FLOW: apps/ai/tools/registry.py se yahan aata hai (search_products,
# get_product_details, compare_products in tools ko call karte hain).
# Ye file DB nahi, Qdrant (vector database) aur Gemini embedding API
# use karti hai — semantic search ke liye.
#
# FIX (stale/soft-deleted product bug): search_products_tool() pehle
# Qdrant se aane wale results ko seedha customer ko bhej deta tha, bina
# ye check kiye ke wo product ab bhi DB mein active/maujood hai ya nahi.
# Agar koi product baad mein soft-delete (is_delete=True) ho jaye, ya
# uska price/stock update ho jaye, Qdrant ko iska pata hi nahi chalta —
# jab tak koi re-indexing na chale, wo purana point Qdrant mein hamesha
# ke liye reh jata hai. Is se customer ko:
#   1. Deleted products dikhte the (jo store se hata diye gaye the).
#   2. Purane/galat price, stock, ya stock-status dikhte the.
# Ab har Qdrant hit ko return karne se PEHLE live DB se verify + refresh
# karte hain (_filter_and_refresh_from_db neeche). Ye sirf ek safety net
# hai — asal fix ye bhi hai ke jab product update/soft-delete ho, uska
# Qdrant point bhi turant update/remove ho (wo indexing pipeline mein
# handle hona chahiye, is file mein nahi).

import requests
from django.conf import settings
from qdrant_client import QdrantClient

from apps.ai.gemini_utils import gemini_keys, call_with_fallback   # FLOW → gemini_utils.py (embedding call ke liye bhi fallback)


def get_qdrant_client():

    # FLOW: Qdrant Cloud se connection banata hai — har search/embed call yahi client use karta hai

    """Qdrant client — har tool use karta hai ise"""
    return QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
    )


def get_query_embedding(text):

    """
    FLOW: search_products_tool() se call hota hai. User ki query
    (jaise "sasta phone") ko Gemini embedding API se ek number-list
    (vector) mein convert karta hai.
    → Yahan se: wapis search_products_tool() ko vector milta hai,
      jo phir Qdrant ko search karne ke liye use hota hai.
    """

    from apps.ai.gemini_utils import call_with_fallback

    def attempt():
        response = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={gemini_keys.current_key}',
            json={
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": text}]},
                "taskType": "RETRIEVAL_QUERY"
            }
        )
        result = response.json()
        if 'error' in result:
            raise Exception(f"Embedding error: {result['error']['message']}")
        return result['embedding']['values']

    return call_with_fallback(attempt)      # FLOW → gemini_utils.py (sirf Gemini key rotation, Groq fallback nahi — Groq embeddings nahi deta)


def _filter_and_refresh_from_db(products: list) -> list:
    """
    NEW — FIX (stale/soft-deleted product bug).

    Takes the raw candidate list built from Qdrant payloads and:
      1. Drops any product that is soft-deleted OR inactive in the real DB
         (Qdrant has no concept of soft-delete — it just keeps stale points).
      2. Overwrites the fields that change over time (name, price,
         original_price, stock, in_stock, category/category_id) with the
         live DB values, so pricing/stock shown to the customer is never
         older than "right now" — only image/relevance_score/description
         are kept from the Qdrant payload since those don't need to be
         real-time-accurate the same way price/stock do.

    Local import of Product avoids a circular/heavy import at module load
    time for a file that's otherwise Qdrant/HTTP-only.
    """
    if not products:
        return products

    from apps.products.models import Product

    ids = [p['product_id'] for p in products if p.get('product_id') is not None]
    if not ids:
        return []

    live = {
        p.id: p
        for p in Product.objects.filter(
            id__in=ids,
            is_delete=False,
            is_active=True,
        ).select_related('category')
    }

    refreshed = []
    for candidate in products:
        db_product = live.get(candidate.get('product_id'))
        if db_product is None:
            # Soft-deleted, deactivated, or otherwise gone — never show this to the customer.
            continue

        candidate['name'] = db_product.name
        candidate['price'] = db_product.price
        candidate['original_price'] = db_product.original_price
        candidate['stock'] = db_product.stock
        candidate['in_stock'] = db_product.stock > 0
        if db_product.category:
            candidate['category'] = db_product.category.name
            candidate['category_id'] = db_product.category_id
        refreshed.append(candidate)

    return refreshed


def search_products_tool(query: str, max_price: float = None, category: str = None, limit: int = 5) -> dict:
    """
    FLOW: registry.py ke search_products tool se call hota hai.
    Poora flow: query → get_query_embedding() (upar wala function) →
    vector milta hai → Qdrant ko bheja jata hai → Qdrant se milte-julte
    products wapis aate hain → filter (price/category) → LIVE DB SE
    VERIFY + REFRESH (FIX) → clean dict banta hai → wapis Agent ko
    jata hai (jo phir user ko natural language mein jawab deta hai).

    Args:
        query:     User ki search query — jaise "Samsung phone under 50000"
        max_price: Maximum price filter (optional) — jaise 50000.0
        category:  Category filter (optional) — jaise "Electronics"
        limit:     Kitne results chahiye (default 5)

    Returns:
        dict with 'products' list and 'total_found' count
    """
    try:
        qdrant = get_qdrant_client()

        # FLOW: yahan upar wala get_query_embedding() call hota hai

        query_vector = get_query_embedding(query)

        # FLOW: yahan ASAL QDRANT SEARCH hoti hai — ye "products" Qdrant
        # collection (index_products management command se pehle se filled)
        # ko search karta hai
        #
        # NOTE: limit * 5 (pehle * 3 tha) — kyunke ab kuch results DB
        # verification step mein drop ho sakte hain (soft-deleted/inactive),
        # is liye Qdrant se thoda zyada fetch karte hain taake filter ke
        # baad bhi caller ka requested `limit` pura mil sake jahan mumkin ho.

        search_response = qdrant.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_vector,
            limit=limit * 5,
            score_threshold=0.3,
            with_payload=True,
        )
        search_results = search_response.points

        products = []
        for result in search_results:
            payload = result.payload

            # Price filter
            if max_price and payload.get('price', 0) > max_price:
                continue

            # Category filter (case-insensitive)
            if category and payload.get('category', '').lower() != category.lower():
                continue

            products.append({
                'product_id':     payload['product_id'],
                'name':           payload['name'],
                'category':       payload.get('category'),
                'category_id':    payload.get('category_id'),
                'price':          payload['price'],
                'original_price': payload.get('original_price'),
                'in_stock':       payload.get('in_stock', True),
                'stock':          payload.get('stock', 0),
                'description':    payload.get('description', ''),
                'image':          payload.get('image'),
                'relevance_score': round(result.score, 3),
            })

        # FIX: drop soft-deleted/inactive products, refresh price/stock/name
        # from the live DB before this ever reaches the customer.
        products = _filter_and_refresh_from_db(products)

        # Trim to the originally requested limit AFTER DB verification,
        # since verification may have removed some candidates.
        products = products[:limit]

        # FLOW: ye poora dict wapis registry.py ke tool function ko jata
        # hai, phir LangChain Agent ko, jo isay dekh kar natural jawab likhta hai

        return {
            'success': True,
            'products': products,
            'total_found': len(products),
            'query': query,
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'products': [],
            'total_found': 0,
        }


def get_product_details_tool(product_id: int) -> dict:

    """
    FLOW: registry.py se call hota hai. Ye Qdrant NAHI use karta —
    seedha apni khud ki Django REST API ko HTTP call karta hai
    (real-time price/stock lene ke liye, kyunke Qdrant payload stale ho sakta hai).
    """

    """
    TOOL: get_product_details_tool

    Ek specific product ki poori detail deta hai.
    Django API se real-time data fetch karta hai (price/stock live hoga).

    Args:
        product_id: Product ka ID (Qdrant search results mein milta hai)

    Returns:
        dict with full product details
    """
    try:
        base_url = getattr(settings, 'INTERNAL_API_URL', 'http://localhost:8000')
        # FLOW: yahan apps/products/views.py ka ProductViewSet.retrieve() hit hota hai
        response = requests.get(f'{base_url}/api/v1/products/{product_id}/')

        if response.status_code == 200:
            product = response.json()
            return {
                'success': True,
                'product': product,
            }
        elif response.status_code == 404:
            return {'success': False, 'error': 'Product not found.'}
        else:
            return {'success': False, 'error': f'API error: {response.status_code}'}

    except Exception as e:
        return {'success': False, 'error': str(e)}


def compare_products_tool(product_ids: list) -> dict:

    """FLOW: registry.py se call hota hai. Andar se get_product_details_tool()
    (upar wala) ko baar-baar call karta hai, har product_id ke liye."""

    """
    TOOL: compare_products_tool

    2 ya zyada products ki side-by-side comparison karta hai.
    AI is data ko use karke user-friendly comparison generate karta hai.

    Args:
        product_ids: List of product IDs — jaise [1, 2] ya [1, 2, 3]

    Returns:
        dict with comparison data for each product
    """
    if len(product_ids) < 2:
        return {'success': False, 'error': 'At least 2 product IDs required for comparison.'}

    if len(product_ids) > 4:
        return {'success': False, 'error': 'Maximum 4 products can be compared at once.'}

    products = []
    errors = []

    for pid in product_ids:
        result = get_product_details_tool(pid)      # FLOW: upar wala function reuse hota hai
        if result['success']:
            products.append(result['product'])
        else:
            errors.append(f'Product {pid}: {result["error"]}')

    if not products:
        return {'success': False, 'error': 'No products found.', 'errors': errors}

    comparison = {
        'success': True,
        'products': products,
        'comparison_fields': ['name', 'price', 'original_price', 'stock', 'category'],
        'errors': errors if errors else None,
    }
#spmething
    return comparison


#again pusing code