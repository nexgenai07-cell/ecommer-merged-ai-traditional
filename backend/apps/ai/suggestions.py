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


def get_customer_followup_suggestions(intermediate_steps) -> list:
    """Requirement 4 — customer side. Based ONLY on this turn's tool calls."""
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
        return ["Log In"]
    if 'add_to_cart' in tool_names_called:
        return ["Go to Cart", "Continue Shopping", "See Similar Products"]
    if 'create_order' in tool_names_called:
        return ["Track Order", "Continue Shopping"]
    if 'search_products' in tool_names_called:
        return ["Compare These", "Filter by Price", "Show More"]
    return []


def get_admin_followup_suggestions(pending_action) -> list:
    """Requirement 4 — admin side.

    NEW — FIX (duplicate Confirm/Cancel UI bug): pehle jab bhi ek
    pending_action hota tha, ye function ["Confirm", "Cancel"] return
    kar deta tha — jo structured pending_action card (jo already
    admin_response_metadata.py se metadata.pending_action ke roop mein
    frontend ko jata hai, aur real confirm/cancel action endpoints call
    karta hai) ke UPAR ek DOOSRA, alag "Confirm"/"Cancel" pill-button
    pair render kar deta tha. Wo dusra pair sirf plain text
    "Confirm"/"Cancel" message wapis chat mein bhejta tha — real
    confirm/cancel endpoint ko call NAHI karta — is liye admin ko do
    button pair dikhte thay jo ek jaisi cheez karte lag rahe thay lekin
    asal mein alag raste se chalte thay (confusing + risky UX).

    FIX: jab pending_action already present hai, structured card hi
    confirm/cancel ka SIRF ek raasta honi chahiye — is liye ab hum
    yahan koi generic "Confirm"/"Cancel" (ya "Haan"/"Nahi" jaisa)
    suggestion return NAHI karte. Sirf pending_action na hone par khali
    list return hoti hai — waisi hi jaisi pehle thi.
    """
    return []