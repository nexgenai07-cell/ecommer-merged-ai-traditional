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