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
        'product_id': product_id, 'category_id': category_id,
        'name': p.get('name'), 'price': p.get('price'),
        'image': p.get('image', p.get('primary_image')),
    }


def _extract_label(row):
    """
    Different analytics endpoints ka date/period field ka naam thora
    alag ho sakta hai (jaisa hamein test se pata chala — sales report
    'date' use karta hai, customer_growth 'period' use karta hai).
    Isliye sab mumkin naam try karte hain.
    """
    return row.get('date') or row.get('period') or row.get('day') or row.get('label')


def _build_analytics_envelope(tool_name, tool_output):
    """Requirement 6 — standardizes all 4 report shapes into one envelope."""
    if not isinstance(tool_output, dict) or not tool_output.get('success'):
        return None

    period = tool_output.get('period') or {}
    envelope = {
        'report_type': tool_name,
        'period': {'start': period.get('start'), 'end': period.get('end')},
        'summary': {},
        'series': [],
    }

    if tool_name == 'sales_report':
        totals = tool_output.get('totals', {})
        rows = tool_output.get('summary', {}).get('daily_breakdown', [])
        envelope['series'] = [{'label': _extract_label(r), 'value': float(r.get('total_revenue', 0) or 0)} for r in rows]
        envelope['summary'] = {
            'total_orders': int(totals.get('total_orders', 0) or 0),
            'total_revenue': float(totals.get('total_revenue', 0) or 0),
            'total_units_sold': 0,
        }
    elif tool_name == 'revenue_report':
        breakdown = tool_output.get('revenue_breakdown', {})
        rows = breakdown.get('by_period', [])
        envelope['series'] = [{'label': _extract_label(r), 'value': float(r.get('revenue', 0) or 0)} for r in rows]
        envelope['summary'] = {'total_revenue': float(breakdown.get('total_revenue', 0) or 0)}
    elif tool_name == 'best_sellers':
        rows = tool_output.get('best_sellers', [])
        envelope['series'] = [{'label': r.get('name'), 'value': float(r.get('units_sold', 0) or 0)} for r in rows]
        envelope['summary'] = {
            'total_units_sold': sum(float(r.get('units_sold', 0) or 0) for r in rows),
            'total_revenue': sum(float(r.get('revenue', 0) or 0) for r in rows),
        }
    elif tool_name == 'customer_growth':
        rows = tool_output.get('by_period', [])
        envelope['series'] = [{'label': _extract_label(r), 'value': float(r.get('new_customers', 0) or 0)} for r in rows]
        envelope['summary'] = {'new_customers': int(tool_output.get('new_customers', 0) or 0)}
    else:
        return None

    return envelope


def extract_admin_metadata(intermediate_steps):
    products, seen_product_ids = [], set()
    categories, seen_category_ids = [], set()
    customers = []
    pending_action = None
    analytics = None   # NEW

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

    for action, tool_output in intermediate_steps:
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

        # NEW — Requirement 6
        tool_name = getattr(action, 'tool', None)
        if tool_name in ('sales_report', 'revenue_report', 'best_sellers', 'customer_growth'):
            built = _build_analytics_envelope(tool_name, tool_output)
            if built:
                analytics = built

    result = {'products': products, 'categories': categories, 'customers': customers}
    if pending_action:
        result['pending_action'] = pending_action
    result['analytics'] = analytics if analytics else None   # NEW — hamesha key present, null agar koi analytics tool nahi chala
    return result


# NEW — FIX (Requirement 5 confirm-button bug): jab admin text ("haan") likh
# kar confirm karta hai, LLM khud hi executor ka result dekh kar natural
# language mein jawab likh deta hai. REST button flow (admin_action_views.py)
# mein koi LLM call nahi hoti — is liye humein khud hi executor ke result se
# ek chota, readable confirmation message aur ChatConsumer jaisa hi metadata
# shape banana padta hai, taake dono flows (text vs button) se same tarah ka
# message chat mein dikhe.

def describe_executed_admin_action(tool_name: str, result: dict) -> str:
    """Executed pending-action ke result se ek chota Roman-Urdu confirmation
    message banata hai — REST confirm/cancel endpoints ke liye."""
    if not isinstance(result, dict):
        return "Action complete ho gaya hai."

    if not result.get('success'):
        error = result.get('error') or 'Wajah maloom nahi ho saki.'
        return f"Ye action fail ho gaya: {error}"

    if tool_name == 'create_product':
        p = result.get('product') or {}
        return f"Product '{p.get('name', '')}' create ho gaya hai ✅ (ID: {p.get('id', '')})"

    if tool_name == 'update_product':
        p = result.get('product') or {}
        return f"Product '{p.get('name', '')}' (ID: {p.get('id', '')}) update ho gaya hai ✅"

    if tool_name == 'delete_product':
        name = result.get('product_name')
        pid = result.get('product_id')
        if name:
            return f"Product '{name}' (ID: {pid}) successfully delete ho gaya hai ✅"
        return f"Product ID {pid} successfully delete ho gaya hai ✅"

    if tool_name == 'create_category':
        c = result.get('category') or {}
        return f"Category '{c.get('name', '')}' create ho gayi hai ✅ (ID: {c.get('id', '')})"

    if tool_name == 'update_category':
        c = result.get('category') or {}
        return f"Category '{c.get('name', '')}' (ID: {c.get('id', '')}) update ho gayi hai ✅"

    if tool_name == 'delete_category':
        name = result.get('category_name')
        cid = result.get('category_id')
        if name:
            return f"Category '{name}' (ID: {cid}) successfully delete ho gayi hai ✅"
        return f"Category ID {cid} successfully delete ho gayi hai ✅"

    if tool_name == 'update_inventory':
        return f"Product ID {result.get('product_id')} ka stock {result.get('quantity')} set ho gaya hai ✅"

    if tool_name == 'update_order':
        return f"Order {result.get('order_id')} update ho gaya hai ✅"

    if tool_name == 'cancel_order':
        return f"Order {result.get('order_id')} cancel ho gaya hai ✅"

    return "Action complete ho gaya hai ✅"


def build_executed_admin_metadata(tool_name: str, result: dict) -> dict:
    """extract_admin_metadata() jaisa hi shape banata hai ({'products': [...],
    'categories': [...], 'customers': [...], 'analytics': None}) — taake
    frontend ka existing metadata-rendering code REST confirm/cancel se aane
    wale message ke liye bhi kaam kare, bina kisi frontend change ke."""
    products, categories = [], []

    if isinstance(result, dict) and result.get('success'):
        if tool_name in ('create_product', 'update_product') and isinstance(result.get('product'), dict):
            p = result['product']
            category = p.get('category')
            products.append({
                'product_id': p.get('id'),
                'category_id': category.get('id') if isinstance(category, dict) else None,
                'name': p.get('name'),
                'price': p.get('price'),
                'image': p.get('primary_image', p.get('image')),
            })
        elif tool_name == 'delete_product':
            products.append({
                'product_id': result.get('product_id'),
                'category_id': None,
                'name': result.get('product_name'),
                'price': None,
                'image': None,
            })
        elif tool_name in ('create_category', 'update_category') and isinstance(result.get('category'), dict):
            c = result['category']
            categories.append({'category_id': c.get('id'), 'name': c.get('name')})
        elif tool_name == 'delete_category':
            categories.append({'category_id': result.get('category_id'), 'name': result.get('category_name')})

    return {'products': products, 'categories': categories, 'customers': [], 'analytics': None}