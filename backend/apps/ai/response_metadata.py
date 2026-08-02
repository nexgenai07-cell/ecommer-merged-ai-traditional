# PATH: apps/ai/response_metadata.py
#
# Agent ke tool calls (intermediate steps) se product/order data extract
# karta hai, taake AI ke text jawab ke SATH structured metadata bhi
# frontend ko bheja ja sake — frontend isi se product cards render karega.

def extract_product_metadata(intermediate_steps):
    """
    Args:
        intermediate_steps: AgentExecutor se milne wali list of
            (AgentAction, tool_output) tuples — tool_output hamesha
            hamare tools ka dict return value hota hai.

    Returns:
        List of dicts, har ek mein kam az kam product_id aur category_id
        (jahan available ho) — duplicates hataye gaye hain.

    FIX: shopping_agent.py ke system prompt mein CROSS-SELL rule hai —
    customer jab ek cheez maange, agent USI TURN mein 1-2 related items
    ke liye bhi search_products() call kar sakta hai (jaise "shoes dikhao"
    ke jawab mein shoes + saath mein bags/shawls bhi search kar lena).
    Pehle ye function har search_products call ke products ko ek hi flat
    list mein merge kar deta tha — is se customer ko unrelated categories
    (jo usne maangi hi nahi thi) ki images bhi dikh jaati thi, sirf uski
    ASAL request ki nahi.

    Ab: sirf PEHLI search_products call (jo hamesha customer ki direct,
    explicit request ka jawab hoti hai, kyunke system prompt mein cross-sell
    rule bolta hai "phle primary request pura karo, phir suggest karo") ke
    products image-card metadata mein jaate hain. Uske baad koi bhi
    search_products call (cross-sell/"aur ye bhi dekhein" type) is metadata
    mein IGNORE ho jaati hai — AI un items ko apne TEXT jawab mein naam se
    mention/suggest kar sakta hai ("agar chahein to X bhi dikha sakta hoon"),
    lekin unki image customer ki screen par khud-ba-khud nahi aayegi jab tak
    customer khud agle message mein explicitly na maange (jis se ek NAYA
    turn banega, jahan wahi search_products call ab "pehli" ban kar
    normally image card degi).

    compare_products is rule se ALAG rakha gaya hai — wo hamesha ek
    explicit customer action hoti hai (customer ne khud 2+ products
    compare karne ko kaha), is liye uske results hamesha shamil hote hain,
    chahe turn mein pehle koi search_products bhi ho chuki ho.
    """
    products = []
    seen_ids = set()
    primary_search_done = False   # NEW — sirf pehli search_products/get_trending_products call ke products lene hain

    # NEW — FIX: Qdrant ka search_products_tool score_threshold=0.3 pe filter
    # karta hai, jo customer ki ASAL request se bilkul unrelated products bhi
    # pass hone deta hai jab store mein exact match na ho (jaise "fans"
    # pucha aur Air Fryer/Handbag/Nerf-gun jaise unrelated items metadata
    # mein aa gaye — sirf isliye ke unka score 0.3 se thoda upar tha). Text
    # jawab mein agent phir bhi "similar" cheezein mention kar sakta hai,
    # lekin IMAGE CARDS (metadata) ke liye hum yahan ek sakht floor lagate
    # hain — sirf genuinely-relevant matches hi card ban kar dikhengi.
    MIN_RELEVANCE_SCORE_FOR_METADATA = 0.45

    def _add_product(p):
        pid = p.get('product_id')
        if pid is None or pid in seen_ids:
            return
        seen_ids.add(pid)
        products.append({
            'product_id':   pid,
            'category_id':  p.get('category_id'),
            'name':         p.get('name') or p.get('product_name'),
            'price':        p.get('price'),
            'image':        p.get('image'),
        })

    for action, tool_output in intermediate_steps:
        if not isinstance(tool_output, dict):
            continue

        tool_name = getattr(action, 'tool', None)   # NEW — pehle tool ka naam istemal hi nahi hota tha

        # search_products / get_trending_products — sirf PEHLI baar (customer
        # ki direct/explicit request ka jawab) ke products lo. Isi turn ke
        # baad wale calls (cross-sell suggestions) ko image metadata se skip
        # karte hain.
        #
        # FIX: pehle "primary_search_done = True" har pehli call ke baad set
        # ho jata tha, chahe us call mein 0 products hi kyun na milen. System
        # prompt ka rule hai ke agar exact product na mile to agent turant ek
        # BROADER search_products call karta hai (jaise "dress" na mile to
        # related clothing dhoondo) — wo dusri, asal mein products dene wali
        # call is wajah se yahan skip ho jaati thi aur metadata hamesha null
        # reh jata tha. Ab hum "primary" us call ko maante hain jisne SABSE
        # PEHLE kam az kam ek RELEVANT product diya ho (score filter neeche
        # dekhein) — empty/all-irrelevant calls ignore hoti hain.
        if tool_name in ('search_products', 'get_trending_products') and isinstance(tool_output.get('products'), list):
            if not primary_search_done:
                found_any = False
                for p in tool_output['products']:
                    if not isinstance(p, dict):
                        continue
                    # relevance_score sirf search_products (Qdrant) ke
                    # results mein hota hai — get_trending_products mein
                    # nahi (wo already-relevant, deterministic list hai),
                    # is liye wahan filter apply nahi hota (default pass).
                    score = p.get('relevance_score')
                    if score is not None and score < MIN_RELEVANCE_SCORE_FOR_METADATA:
                        continue
                    _add_product(p)
                    found_any = True
                if found_any:
                    primary_search_done = True
            continue

        # compare_products — hamesha ek explicit customer action hai,
        # is liye position/turn mein kahin bhi ho, hamesha shamil karte hain.
        if tool_name == 'compare_products' and isinstance(tool_output.get('products'), list):
            for p in tool_output['products']:
                if isinstance(p, dict):
                    _add_product(p)
            continue

        # add_to_cart — single product (explicit customer action, hamesha shamil)
        if 'product_id' in tool_output:
            _add_product(tool_output)

        # create_order — items list (product_id/category_id per item)
        if isinstance(tool_output.get('items'), list):
            for item in tool_output['items']:
                if isinstance(item, dict):
                    _add_product(item)

    return products