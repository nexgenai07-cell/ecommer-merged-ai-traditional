# PATH: apps/ai/admin_tools/registry.py

# FLOW: admin_agent.py se yahan aata hai. Customer side ke registry.py
# jaisa hi role hai, FARQ: mutating tools yahan seedha DB/API change
# nahi karti — pehle preview banati hain (confirmation-gating).

from typing import Optional
from langchain_core.tools import tool

from apps.ai.admin_tools.pending_actions import get_pending_action, clear_pending_action    # FLOW → pending_actions.py

from apps.ai.admin_tools.product_tools import (
    propose_create_product, execute_create_product,
    propose_update_product, execute_update_product,
    propose_delete_product, execute_delete_product,
    list_products as _list_products,
)
from apps.ai.admin_tools.category_tools import (
    propose_create_category, execute_create_category,
    propose_update_category, execute_update_category,
    propose_delete_category, execute_delete_category,
    get_categories as _get_categories,
)
from apps.ai.admin_tools.inventory_tools import (
    check_inventory as _check_inventory,
    propose_update_inventory, execute_update_inventory,
    low_stock as _low_stock,
)
from apps.ai.admin_tools.order_tools import (
    get_order_details as _get_order_details,
    propose_update_order, execute_update_order,
    propose_cancel_order, execute_cancel_order,
    track_order as _track_order,
)
from apps.ai.admin_tools.analytics_tools import (
    sales_report_tool as _sales_report,
    revenue_report_tool as _revenue_report,
    best_sellers_tool as _best_sellers,
    customer_growth_tool as _customer_growth,
)
from apps.ai.audit import log_admin_action      # FLOW → apps/ai/audit.py
from apps.ai.admin_tools.customer_tools import list_customers as _list_customers

EXECUTORS = {
    'create_product':   execute_create_product,
    'update_product':   execute_update_product,
    'delete_product':   execute_delete_product,
    'create_category':  execute_create_category,
    'update_category':  execute_update_category,
    'delete_category':  execute_delete_category,
    'update_inventory': execute_update_inventory,
    'update_order':     execute_update_order,
    'cancel_order':      execute_cancel_order,
}


def get_admin_operations_tools(session_key: str, user):
    """Admin Operations Agent ke tools — session_key (confirmation-gating) aur user (HTTP calls) se bound."""

    @tool
    def create_product(name: str, price: float, stock: int, category_id: Optional[int] = None,
                        description: Optional[str] = "", original_price: Optional[float] = None,
                        sku: Optional[str] = None, low_stock_threshold: Optional[int] = None) -> dict:
        """Create a new product. MUTATING — only creates a PREVIEW and
        returns requires_confirmation=True with an action_id. Show the
        preview to the admin and ask them to confirm before it's actually
        created. If sku is not given, one will be auto-generated."""
        if description is None:
            description = ""

        # FLOW → apps/ai/admin_tools/product_tools.py ka propose_create_product()
        # Ye SEEDHA product nahi banata — sirf Redis mein pending action save karta hai

        return propose_create_product(session_key, name, price, stock, category_id,
                                       description, original_price, sku, low_stock_threshold)

    @tool
    def update_product(product_id: int, fields: dict) -> dict:
        """Update an existing product's fields (price, stock, name, description,
        category_id, is_active, etc). MUTATING — requires confirmation.
        'fields' is a dict of field_name: new_value, e.g. {"price": 5000}."""
        return propose_update_product(session_key, product_id, fields)

    @tool
    def delete_product(product_id: int) -> dict:
        """Delete (soft-delete) a product. MUTATING and destructive —
        always requires explicit confirmation."""
        return propose_delete_product(session_key, product_id)

    @tool
    def create_category(name: str, description: Optional[str] = "") -> dict:
        """Create a new product category. MUTATING — requires confirmation."""
        if description is None:
            description = ""
        return propose_create_category(session_key, name, description)

    @tool
    def update_category(category_id: int, fields: dict) -> dict:
        """Update a category's fields (name, description). MUTATING — requires confirmation."""
        return propose_update_category(session_key, category_id, fields)

    @tool
    def delete_category(category_id: int) -> dict:
        """Delete a category. MUTATING and destructive — requires confirmation."""
        return propose_delete_category(session_key, category_id)

    @tool
    def get_categories() -> dict:
        """List all product categories with their product counts. Read-only."""
        return _get_categories(user)

    @tool
    def list_products(category_id: Optional[int] = None, search: Optional[str] = None, limit: Optional[int] = 20) -> dict:
        """List products, optionally filtered by category_id or a search
        keyword. Read-only. Use this whenever the admin asks to see/list
        products — do not say you lack this capability."""
        if limit is None:
            limit = 20
        return _list_products(user, category_id, search, limit)
    
    @tool
    def list_customers(search: Optional[str] = None) -> dict:
        """List store customers with their real customer_id, name, phone,
        email, total_orders, and total_spent. Use this whenever the admin
        asks about customers, their order counts, spending, or wants
        customer-level detail — this gives real IDs and per-customer data,
        unlike customer_growth which only gives aggregate daily counts.
        Optionally filter by a search keyword (name/phone/email)."""
        return _list_customers(user, search)

    @tool
    def check_inventory(product_id: int) -> dict:
        """Check the current stock quantity of a product. Read-only."""
        return _check_inventory(user, product_id)

    @tool
    def update_inventory(product_id: int, quantity: int) -> dict:
        """Set a product's stock quantity to a new value. MUTATING —
        requires confirmation before actually applying."""
        return propose_update_inventory(session_key, product_id, quantity)

    @tool
    def low_stock(threshold: Optional[int] = None) -> dict:
        """List products that are low on stock (at or below their own
        configured threshold). If 'threshold' is given, results are
        further narrowed to that value. Read-only."""
        return _low_stock(user, threshold)

    @tool
    def get_order_details(order_id: str) -> dict:
        """Get order details by order number. Read-only."""
        return _get_order_details(user, order_id)

    @tool
    def update_order(order_id: str, fields: dict) -> dict:
        """Update an order's status and/or tracking_number. MUTATING —
        requires confirmation."""
        return propose_update_order(session_key, order_id, fields)

    @tool
    def cancel_order(order_id: str, reason: Optional[str] = "") -> dict:
        """Cancel an order, with an optional reason. MUTATING and
        semi-irreversible — requires confirmation."""
        if reason is None:
            reason = ""
        return propose_cancel_order(session_key, order_id, reason)

    @tool
    def track_order(order_id: str) -> dict:
        """Get the current status of an order. Read-only."""
        return _track_order(user, order_id)

    @tool
    def confirm_pending_action(action_id: str) -> dict:
        """Execute a previously proposed mutating action after the admin has
        explicitly confirmed it. Only call this AFTER the admin has clearly
        said yes/confirm for that specific action_id — never call it
        proactively or guess an action_id."""

        # FLOW: Admin ke "haan/confirm" bolne pe YE tool call hota hai

        pending = get_pending_action(session_key, action_id)    # FLOW → pending_actions.py se Redis se nikalta hai
        if pending is None:
            return {'success': False, 'error': 'This confirmation has expired or was not found. Please propose the action again.'}

        tool_name = pending['tool_name']
        payload = pending['kwargs']

        executor = EXECUTORS.get(tool_name)

        # FLOW: EXECUTORS dict se sahi execute_* function milta hai
        # (jaise execute_create_product) — TABHI asal HTTP call jaati hai

        if executor is None:
            return {'success': False, 'error': f'No executor implemented yet for "{tool_name}".'}

        result = executor(user, payload)     # FLOW → product_tools.py ka execute_create_product() → api_client.py
        clear_pending_action(session_key, action_id)

        log_admin_action(user, tool_name, payload, result)      # FLOW → apps/ai/audit.py — AuditLog mein save

        return result

    return [
        create_product, update_product, delete_product,
        create_category, update_category, delete_category, get_categories,
        list_products, list_customers,
        check_inventory, update_inventory, low_stock,
        get_order_details, update_order, cancel_order, track_order,
        confirm_pending_action,
    ]


def get_analytics_tools(user=None):
    """Analytics Agent ke tools — sab read-only hain."""

    @tool
    def sales_report(date_range: Optional[str] = "last_30_days") -> dict:
        """Get a sales summary report for a given date range (e.g. 'last_7_days',
        'last_30_days', 'this_month'). Read-only."""
        if date_range is None:
            date_range = "last_30_days"
        return _sales_report(user, date_range)

    @tool
    def revenue_report(date_range: Optional[str] = "last_30_days") -> dict:
        """Get a revenue breakdown report for a given date range. Read-only."""
        if date_range is None:
            date_range = "last_30_days"
        return _revenue_report(user, date_range)

    @tool
    def best_sellers(date_range: Optional[str] = "last_30_days", limit: Optional[int] = 5) -> dict:
        """Get the top-selling products for a given date range. Read-only."""
        if date_range is None:
            date_range = "last_30_days"
        if limit is None:
            limit = 5
        return _best_sellers(user, date_range, limit)

    @tool
    def customer_growth(date_range: Optional[str] = "last_30_days") -> dict:
        """Get new customer count and retention info for a given date range. Read-only."""
        if date_range is None:
            date_range = "last_30_days"
        return _customer_growth(user, date_range)

    return [sales_report, revenue_report, best_sellers, customer_growth]

def get_admin_agent_tools(session_key: str, user):
    """
    MERGED — Operations + Analytics tools ek hi list mein. Ye customer
    side ke pattern jaisa hai: ek hi Agent ko sab tools milte hain, aur
    khud LLM (uske docstrings dekh kar) decide karta hai kaunsa tool
    call karna hai — koi fragile keyword-based pre-routing nahi.

    Ye is liye kiya gaya kyunke Coordinator ka keyword-matching
    (route_intent) real queries pe fail ho raha tha (jaise "customer
    accounts" query "growth" na likhne ki wajah se Operations Agent ko
    chali jati thi, jahan customer_growth tool exist hi nahi karta).
    """
    return get_admin_operations_tools(session_key, user) + get_analytics_tools(user)