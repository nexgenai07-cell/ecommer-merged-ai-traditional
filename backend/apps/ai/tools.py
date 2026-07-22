# PATH: apps/ai/tools.py
#
# LangChain tool stubs — Day 1 deliverable.
# Har function ka input/output signature yahan fix kar diya hai
# taake Developer B agent setup shuru kar sake bina wait kiye.
# Real logic (Qdrant, Django API calls) Day 2 aur Day 3 mein aayegi.

from langchain.tools import tool


# ── Shopping Agent Tools ───────────────────────────────────────────────

@tool
def search_products_tool(query: str, min_price: float = None, max_price: float = None, category: str = None) -> list:
    """
    Semantic search products using Qdrant based on user query and filters.
    Returns a list of matching products.

    STUB — real Qdrant search logic will be added on Day 2.
    """
    return []  # dummy: [{"product_id": 1, "name": "", "price": 0, "stock": 0, "image": ""}]


@tool
def get_product_details_tool(product_id: int) -> dict:
    """
    Fetch full details of a single product by ID.

    STUB — real Django ORM lookup will be added on Day 2.
    """
    return {}  # dummy: {"product_id": 1, "name": "", "price": 0, "stock": 0, "description": "", "image": ""}


@tool
def compare_products_tool(product_ids: list) -> list:
    """
    Compare multiple products side by side (price, specs, stock).

    STUB — real comparison logic will be added on Day 2.
    """
    return []


@tool
def add_to_cart_tool(session_key: str, product_id: int, quantity: int = 1) -> dict:
    """
    Add a product to the customer's cart via Django Cart API.

    STUB — real Cart API call will be added on Day 3.
    """
    return {"success": False, "message": "Not implemented yet"}


@tool
def create_order_tool(session_key: str) -> dict:
    """
    Create an order from the current cart via Django Order API.

    STUB — real Order API call will be added on Day 3.
    """
    return {"order_id": None, "status": "not_implemented"}


@tool
def track_order_tool(order_id: int) -> dict:
    """
    Get live status of an existing order.

    STUB — real tracking logic will be added on Day 3.
    """
    return {"order_id": order_id, "status": "unknown"}


@tool
def cancel_order_tool(order_id: int) -> dict:
    """
    Cancel an existing order if still eligible.

    STUB — real cancel logic will be added on Day 3.
    """
    return {"order_id": order_id, "cancelled": False}


# ── Support Agent Tools (for reference — Developer B may own these) ────

@tool
def faq_answer_tool(question: str) -> str:
    """
    Answer general FAQs (return policy, shipping info etc).

    STUB — real logic will be added later.
    """
    return "Not implemented yet"


# Registry — Developer B ye list use kar k tools ko agent mein register karega
SHOPPING_AGENT_TOOLS = [
    search_products_tool,
    get_product_details_tool,
    compare_products_tool,
    add_to_cart_tool,
    create_order_tool,
    track_order_tool,
    cancel_order_tool,
]

SUPPORT_AGENT_TOOLS = [
    faq_answer_tool,
]