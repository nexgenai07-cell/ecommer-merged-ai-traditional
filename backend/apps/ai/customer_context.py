# PATH: apps/ai/customer_context.py
#
# Logged-in customer ka purchase history summary banata hai, taake AI
# proactively purani purchases ke basis pe recommendations de sake
# (e.g. "aapne pehle X liya tha, aaj wapis dekhna chahenge?").
#
# Anonymous customers k liye ye kaam nahi karta — unki koi permanent
# identity nahi hoti (na account, na email), isliye unke liye sirf
# current session ki conversation history hi available hoti hai.

from apps.orders.models import Order


def get_customer_context(user) -> str:
    """
    Returns a short natural-language summary of the customer's past
    purchases, meant to be injected into the AI's system prompt as context.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return (
            "This is a guest (anonymous) customer with no account — no "
            "cross-session purchase history is available for them. You can "
            "only rely on what they've said earlier in THIS conversation."
        )

    orders = (
        Order.objects.filter(customer__user=user)
        .exclude(status='cancelled')
        .prefetch_related('items')
        .order_by('-created_at')[:10]  # recent orders only — keep context small
    )

    product_names = []
    for order in orders:
        for item in order.items.all():
            if item.product_name and item.product_name not in product_names:
                product_names.append(item.product_name)

    if not product_names:
        return "This customer is logged in but has no past purchases yet."

    shown = product_names[:10]
    return (
        "This customer has previously purchased: " + ", ".join(shown) + ". "
        "If it fits naturally in the conversation, you may proactively remind "
        "them and ask if they'd like to reorder, see similar items, or check "
        "out related new arrivals — but don't force this into every reply, "
        "only when genuinely relevant to what they're asking about now."
    )