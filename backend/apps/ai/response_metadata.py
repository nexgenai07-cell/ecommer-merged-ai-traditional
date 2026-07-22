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
    """
    products = []
    seen_ids = set()

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

    for _, tool_output in intermediate_steps:
        if not isinstance(tool_output, dict):
            continue

        # search_products / compare_products — list of products
        if isinstance(tool_output.get('products'), list):
            for p in tool_output['products']:
                if isinstance(p, dict):
                    _add_product(p)

        # add_to_cart — single product
        if 'product_id' in tool_output:
            _add_product(tool_output)

        # create_order — items list (product_id/category_id per item)
        if isinstance(tool_output.get('items'), list):
            for item in tool_output['items']:
                if isinstance(item, dict):
                    _add_product(item)

    return products