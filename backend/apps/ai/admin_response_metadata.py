# PATH: apps/ai/admin_response_metadata.py
#
# Admin tool calls (intermediate_steps) se product/category/customer data extract
# karta hai — customer side ke response_metadata.py jaisa hi pattern.

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
    customers, seen_customer_ids = [], set()   # NEW: Added customer tracking lists

    # Day 4 analytics tools ka output tracking
    customer_growth = None
    sales_report = None
    revenue_report = None
    best_sellers = None

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

        # Product extraction
        if isinstance(tool_output.get('products'), list):
            for p in tool_output['products']:
                _add_product(p)

        if isinstance(tool_output.get('product'), dict):
            _add_product(tool_output['product'])
        elif 'product_id' in tool_output:
            _add_product(tool_output)

        if isinstance(tool_output.get('preview'), dict):
            _add_product(tool_output['preview'])

        # Category extraction
        if isinstance(tool_output.get('categories'), list):
            for c in tool_output['categories']:
                _add_category(c)

        if isinstance(tool_output.get('category'), dict):
            _add_category(tool_output['category'])

        # NEW: Customer list extraction (list_customers)
        if isinstance(tool_output.get('customers'), list):
            for c in tool_output['customers']:
                if isinstance(c, dict):
                    # id ya customer_id dono patterns ko safely check karne k liye
                    cust_id = c.get('customer_id') or c.get('id')
                    if cust_id is not None and cust_id not in seen_customer_ids:
                        seen_customer_ids.add(cust_id)
                        customers.append(c)

        # customer_growth_tool output
        if 'new_customers' in tool_output:
            customer_growth = {
                'date_range': tool_output.get('date_range'),
                'new_customers': tool_output.get('new_customers'),
                'by_period': tool_output.get('by_period', []),
                'retention': tool_output.get('retention'),
            }

        # sales_report_tool output
        if 'totals' in tool_output and 'summary' in tool_output:
            sales_report = {
                'date_range': tool_output.get('date_range'),
                'summary': tool_output.get('summary'),
                'totals': tool_output.get('totals'),
            }

        # revenue_report_tool output
        if isinstance(tool_output.get('revenue_breakdown'), dict):
            revenue_report = {
                'date_range': tool_output.get('date_range'),
                **tool_output['revenue_breakdown'],
            }

        # best_sellers_tool output
        if isinstance(tool_output.get('best_sellers'), list):
            best_sellers = {
                'date_range': tool_output.get('date_range'),
                'items': tool_output['best_sellers'],
            }

    return {
        'products': products,
        'categories': categories,
        'customers': customers,   # NEW: Added to final return dict
        'customer_growth': customer_growth,
        'sales_report': sales_report,
        'revenue_report': revenue_report,
        'best_sellers': best_sellers,
    }