# PATH: apps/ai/suggestions.py
#
# Requirement 3 — initial suggestion chips (connected message)
# Requirement 4 — follow-up suggestions (per AI turn)

from django.utils import timezone
from datetime import timedelta


def get_initial_suggestions(user) -> list:
    """Requirement 3. Returns 3-4 suggestions, each under 30 chars."""
    defaults = ["Find a product", "Track my order", "Show deals", "Talk to support"]

    if user is None or not getattr(user, 'is_authenticated', False):
        return defaults

    from apps.orders.models import Order

    suggestions = []

    # Rule: order in last 14 days, not yet delivered -> "Track order #..."
    recent_order = (
        Order.objects.filter(customer__user=user, created_at__gte=timezone.now() - timedelta(days=14))
        .exclude(status='delivered')
        .order_by('-created_at')
        .first()
    )
    if recent_order:
        suggestions.append(f"Track order #{recent_order.order_number}"[:30])

    remaining = [d for d in defaults if d != "Track my order"] if suggestions else defaults[:]

    # Rule: prior purchase -> replace one generic with a category-based suggestion
    last_order = Order.objects.filter(customer__user=user).exclude(status='cancelled').order_by('-created_at').first()
    if last_order:
        first_item = last_order.items.select_related('product__category').first()
        if first_item and first_item.product and first_item.product.category:
            category_name = first_item.product.category.name
            category_suggestion = f"See new {category_name.lower()}"[:30]
            if remaining:
                remaining[0] = category_suggestion

    suggestions.extend(remaining)
    return suggestions[:4]


def get_customer_followup_suggestions(intermediate_steps, session_key: str = None) -> list:
    """Requirement 4 — customer side. Based ONLY on this turn's tool calls,
    PLUS (NEW) language-switch chips at the end so the customer can always
    change what language the bot replies in."""
    tool_names_called = set()
    login_required = False

    for action, tool_output in (intermediate_steps or []):
        tool_name = getattr(action, 'tool', None)
        if tool_name:
            tool_names_called.add(tool_name)
        if isinstance(tool_output, dict):
            err = str(tool_output.get('error', '')).lower()
            if 'log in' in err or 'logged in' in err:
                login_required = True

    if login_required:
        base = ["Log In"]
    elif 'add_to_cart' in tool_names_called:
        base = ["Go to Cart", "Continue Shopping", "See Similar Products"]
    elif 'create_order' in tool_names_called:
        base = ["Track Order", "Continue Shopping"]
    elif 'search_products' in tool_names_called:
        base = ["Compare These", "Filter by Price", "Show More"]
    else:
        base = []

    return base + _get_language_chip_suggestions(session_key)


def _get_language_chip_suggestions(session_key: str = None) -> list:
    """
    NEW — always-available language-switch chips. Agar customer ne pehle se
    (kisi sticky preference se) ek language select ki hui hai, us ek ko
    list se hata dete hain (khud ko dobara select karne ka option dikhana
    unnecessary hai) — baaki do hamesha dikhte hain taake customer kabhi
    bhi switch kar sake.
    """
    from apps.ai.language_preference import LANGUAGE_CHIPS, get_language_preference

    active = get_language_preference(session_key) if session_key else None
    return [label for code, label in LANGUAGE_CHIPS.items() if code != active]


def get_admin_followup_suggestions(pending_action, intermediate_steps=None) -> list:
    """
    Requirement 4 — admin side.
    - Agar ek confirmation abhi pending hai -> Confirm/Cancel dikhao.
    - NEW — FIX: agar is turn mein confirm_pending_action successfully chal
      chuka hai (matlab koi mutation abhi complete hui hai), pehle yahan
      hamesha [] return hota tha — ab admin ko engaged rakhne ke liye
      relevant next-step suggestions dete hain, based on jo tool abhi chala.
    """
    if pending_action:
        return ["Confirm", "Cancel"]

    tool_names_called = set()
    for action, _ in (intermediate_steps or []):
        tool_name = getattr(action, 'tool', None)
        if tool_name:
            tool_names_called.add(tool_name)

    if 'confirm_pending_action' in tool_names_called:
        return ["View product", "Update another field", "View all products"]
    if tool_names_called & {'sales_report', 'revenue_report', 'best_sellers', 'customer_growth'}:
        return ["Compare with last period", "View best sellers", "Check low stock"]
    if 'list_products' in tool_names_called or 'get_product_details' in tool_names_called:
        return ["Update this product", "Check inventory"]
    if 'low_stock' in tool_names_called:
        return ["Update stock", "View all products"]

    return []