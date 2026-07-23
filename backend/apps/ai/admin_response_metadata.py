# PATH: apps/ai/admin_response_metadata.py

def _normalize_product(p):
    if not isinstance(p, dict):
        return None

    product_id = p.get('product_id', p.get('id'))
    if product_id is None:
        return None

    category_id = p.get('category_id')
    if category_id is None and isinstance(p.get('category'), dict):
        category_id = p['category'].get('id')

    return {
        'product_id': product_id,
        'category_id': category_id,
        'name': p.get('name'),
        'price': p.get('price'),
        'image': p.get('image', p.get('primary_image')),
    }


def extract_admin_metadata(intermediate_steps):
    products, seen_product_ids = [], set()
    categories, seen_category_ids = [], set()
    customers = []
    pending_action = None   # <-- ZAROORI: loop se pehle initialize

    def _add_product(p):
        norm = _normalize_product(p)
        if norm and norm['product_id'] not in seen_product_ids:
            seen_product_ids.add(norm['product_id'])
            products.append(norm)

    def _add_category(c):
        if not isinstance(c, dict):
            return
        cid = c.get('id') or c.get('category_id')
        if cid is not None and cid not in seen_category_ids:
            seen_category_ids.add(cid)
            categories.append({'category_id': cid, 'name': c.get('name')})

    for _, tool_output in intermediate_steps:
        if not isinstance(tool_output, dict):
            continue

        if isinstance(tool_output.get('products'), list):
            for p in tool_output['products']:
                _add_product(p)

        if isinstance(tool_output.get('product'), dict):
            _add_product(tool_output['product'])
        elif 'product_id' in tool_output:
            _add_product(tool_output)

        if isinstance(tool_output.get('preview'), dict) and not tool_output.get('requires_confirmation'):
            _add_product(tool_output['preview'])

        if isinstance(tool_output.get('categories'), list):
            for c in tool_output['categories']:
                _add_category(c)

        if isinstance(tool_output.get('category'), dict):
            _add_category(tool_output['category'])

        if isinstance(tool_output.get('customers'), list):
            for c in tool_output['customers']:
                if isinstance(c, dict) and c.get('customer_id') is not None:
                    customers.append(c)

        if tool_output.get('requires_confirmation'):
            pending_action = {
                'action_id': tool_output.get('action_id'),
                'action_type': tool_output.get('action_type'),
                'preview': tool_output.get('preview'),
                'expires_at': tool_output.get('expires_at'),
            }

    result = {'products': products, 'categories': categories, 'customers': customers}
    if pending_action:
        result['pending_action'] = pending_action
    return result