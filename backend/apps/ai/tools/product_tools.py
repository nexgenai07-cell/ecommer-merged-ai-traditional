# PATH: apps/ai/tools/product_tools.py

# FLOW: apps/ai/tools/registry.py se yahan aata hai (search_products,
# get_product_details, compare_products in tools ko call karte hain).
# Ye file DB nahi, Qdrant (vector database) aur Gemini embedding API
# use karti hai — semantic search ke liye.

import logging   # NEW — DIAGNOSTIC: bar-bar "product available nahi" ki asal wajah pinpoint karne ke liye
import requests
from django.conf import settings
from qdrant_client import QdrantClient

from apps.ai.gemini_utils import gemini_keys, call_with_fallback   # FLOW → gemini_utils.py (embedding call ke liye bhi fallback)
from apps.products.models import Product   # NEW — FIX: staleness-check ab EK single DB query se hoti hai, N alag-alag HTTP calls se nahi (neeche dekhein)

logger = logging.getLogger("ai.tools.product_tools")   # NEW


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


def search_products_tool(query: str, max_price: float = None, category: str = None, limit: int = 5) -> dict:
    """
    FLOW: registry.py ke search_products tool se call hota hai.
    Poora flow: query → get_query_embedding() (upar wala function) →
    vector milta hai → Qdrant ko bheja jata hai → Qdrant se milte-julte
    products wapis aate hain → filter (price/category) → clean dict
    banta hai → wapis Agent ko jata hai (jo phir user ko natural
    language mein jawab deta hai).

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

        search_response = qdrant.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_vector,
            limit=limit * 3,  # zyada fetch karo — filters ke baad bhi enough milein
            score_threshold=0.3,
            with_payload=True,
        )
        search_results = search_response.points

        # NEW — DIAGNOSTIC: agar "product available nahi" wala masla phir
        # se aaye, ye log lines Railway pe exact wajah dikha dengi —
        # (a) Qdrant ne is query ke liye kitne hits diye (0 ho sakte hain
        # agar Qdrant index is product ke liye stale/outdated hai — jaise
        # product baad mein add hua ho aur index_products dobara na chala
        # ho), (b) un hits mein se kitne DB mein live/active mile.
        logger.warning(
            "[search_products_tool] query=%r category=%r max_price=%r qdrant_hits=%d",
            query, category, max_price, len(search_results),
        )

        products = []

        # NEW — FIX (v2): Pichli baar har Qdrant candidate ko apni hi API
        # par ek ALAG HTTP call se verify kiya ja raha tha (limit*3 tak =
        # 15 sequential network round-trips PER SEARCH) — isse response
        # itna slow ho gaya ke poora search_products call hi timeout/fail
        # hone laga, aur customer ko koi bhi metadata/product cards milna
        # bilkul band ho gaya tha (sirf text reply aata tha). Ab isi
        # staleness-check ko EK SINGLE, fast DB query se karte hain (hum
        # khud Django process ke andar hain, HTTP round-trip ki zaroorat
        # nahi) — field names cart_order_tools.py se confirmed hain
        # (id/is_active/stock/price/category/name/original_price/in_stock).
        candidate_ids = [r.payload.get('product_id') for r in search_results if r.payload.get('product_id') is not None]
        live_map = {
            p.id: p
            for p in Product.objects.select_related('category').filter(id__in=candidate_ids, is_active=True)
        }

        # NEW — DIAGNOSTIC
        logger.warning(
            "[search_products_tool] candidate_ids=%s matched_in_db=%s",
            candidate_ids, list(live_map.keys()),
        )

        products = []
        products_without_category_filter = []   # NEW — fallback ke liye

        for result in search_results:
            payload = result.payload
            pid = payload.get('product_id')
            if pid is None:
                continue

            # NEW — CRITICAL FIX: Qdrant index kabhi STALE ho jata hai —
            # product delete/deactivate/update ho chuka hota hai asal
            # database mein, lekin Qdrant re-index nahi hota (jab tak
            # index_products command dobara na chale) — isi wajah se
            # purana/ghost data (jo ab DB mein exist hi nahi karta)
            # customer ko dikh jata tha, aur us par "Add to Cart" hamesha
            # fail hota tha. Agar ye product live_map mein nahi hai, matlab
            # DB mein exist hi nahi karta (ya inactive hai) — skip karo.
            live = live_map.get(pid)
            if live is None:
                continue

            stock = live.stock or 0
            if stock <= 0:
                continue

            price = float(live.price)

            # Price filter — REAL/live DB price se, Qdrant payload se nahi
            if max_price and price > max_price:
                continue

            category_name = live.category.name if live.category else None

            product_dict = {
                'product_id':      live.id,
                'name':             live.name,
                'category':         category_name,
                'category_id':      live.category_id,
                'price':            price,
                'original_price':   float(live.original_price) if getattr(live, 'original_price', None) else None,
                'in_stock':         getattr(live, 'in_stock', stock > 0),
                'stock':            stock,
                'description':      getattr(live, 'description', '') or '',
                # NEW — image abhi bhi Qdrant payload se (DB field ka naam
                # yahan confirm nahi tha) — kabhi image kabhi thodi purani
                # ho sakti hai, lekin ye poore product ke stale/ghost hone
                # (jo asal bug tha) se bohot chhota risk hai.
                'image':            payload.get('image'),
                'relevance_score':  round(result.score, 3),
            }

            if len(products_without_category_filter) < limit:
                products_without_category_filter.append(product_dict)

            # NEW — FIX: pehle EXACT string match tha (category_name.lower()
            # != category.lower()) — agar LLM ne "Kitchen" bheja lekin DB
            # mein category ka asal naam "Kitchen & Dining" ya "Home &
            # Kitchen" hai, to ye EXACT match fail ho jata aur sab results
            # (jo Qdrant ki semantic search ke hisab se already relevant
            # thay) silently ud jate thay — is se genuinely available
            # product bhi "not available" dikhta tha. Ab substring-based
            # (dono taraf) match karte hain — zyada forgiving, kam false-negatives.
            if category:
                cat_l = category.lower().strip()
                name_l = (category_name or '').lower().strip()
                if not name_l or (cat_l not in name_l and name_l not in cat_l):
                    continue

            products.append(product_dict)

            if len(products) >= limit:
                break

        # NEW — FIX: agar category filter ki wajah se list bilkul KHALI ho
        # gayi ho (jaise LLM ne category="shoes"/"footwear" bheja jo
        # hamari DB mein exist hi nahi karti, is liye koi bhi category
        # match nahi hui), lekin semantic search ne otherwise RELEVANT
        # products diye thay (jaise "shoes" ke liye "joggers") — to
        # bilkul khali list dene ke bajaye un unfiltered-but-relevant
        # results wapis karte hain. Agent phir khud decide karega ke ye
        # exact match nahi lekin ek reasonable alternative hai (system
        # prompt rule 1 isay explicitly is tarah handle karne ko kehta hai).
        if category and not products and products_without_category_filter:
            logger.warning(
                "[search_products_tool] category=%r filtered everything out — falling back to %d unfiltered relevant result(s)",
                category, len(products_without_category_filter),
            )
            products = products_without_category_filter

        # FLOW: ye poora dict wapis registry.py ke tool function ko jata
        # hai, phir LangChain Agent ko, jo isay dekh kar natural jawab likhta hai

        # NEW — DIAGNOSTIC
        logger.warning("[search_products_tool] final products_returned=%d", len(products))

        return {
            'success': True,
            'products': products,
            'total_found': len(products),
            'query': query,
        }

    except Exception as e:
        # NEW — DIAGNOSTIC: pehle exception silently swallow ho kar sirf
        # str(e) agent ko chali jati thi (jo aksar samajh nahi aata) — ab
        # poori traceback Railway logs mein bhi jayegi, taake exact wajah
        # (jaise field-name mismatch, Qdrant connection error, etc.) turant
        # dikh jaye.
        logger.exception("[search_products_tool] EXCEPTION for query=%r category=%r", query, category)
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

    return comparison