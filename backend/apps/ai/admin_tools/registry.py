# PATH: apps/ai/admin_tools/registry.py

from typing import Optional
from langchain_core.tools import tool

from apps.ai.admin_tools.pending_actions import (
    get_pending_action, is_expired, mark_resolved,
)

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
from apps.ai.admin_tools.customer_tools import list_customers as _list_customers
from apps.ai.admin_tools.analytics_tools import (
    sales_report_tool as _sales_report,
    revenue_report_tool as _revenue_report,
    best_sellers_tool as _best_sellers,
    customer_growth_tool as _customer_growth,
)
from apps.ai.audit import log_admin_action


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


def execute_pending_action_by_id(action_id: str):
    """
    Shared executor — used by BOTH the text 'haan/confirm' flow (via the
    confirm_pending_action tool below) AND the new REST confirm endpoint.

    Returns: (status, data)
      status: 'not_found' | 'expired' | 'executed'
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    pending = get_pending_action(action_id)
    if pending is None or pending.get('resolved'):
        return 'not_found', None

    if is_expired(pending):
        return 'expired', None

    tool_name = pending['tool_name']
    payload = pending['kwargs']
    user = User.objects.get(id=pending['user_id'])

    executor = EXECUTORS.get(tool_name)
    if executor is None:
        return 'not_found', {'error': f'No executor implemented for "{tool_name}".'}

    result = executor(user, payload)
    mark_resolved(action_id)
    log_admin_action(user, tool_name, payload, result)

    return 'executed', result


def cancel_pending_action_by_id(action_id: str):
    """Returns: (status,) — 'not_found' | 'expired' | 'cancelled'"""
    pending = get_pending_action(action_id)
    if pending is None or pending.get('resolved'):
        return 'not_found'
    if is_expired(pending):
        return 'expired'

    mark_resolved(action_id)
    return 'cancelled'


def get_admin_operations_tools(session_key: str, user):

    @tool
    def create_product(name: str, price: float, stock: int, category_id: Optional[int] = None,
                        description: Optional[str] = "", original_price: Optional[float] = None,
                        sku: Optional[str] = None, low_stock_threshold: Optional[int] = None) -> dict:
        """Create a new product. MUTATING — only creates a PREVIEW and
        returns requires_confirmation=True with an action_id and expires_at
        (5 minutes). Show the preview to the admin and ask them to confirm
        before it's actually created."""
        if description is None:
            description = ""
        return propose_create_product(session_key, user.id, name, price, stock, category_id,
                                       description, original_price, sku, low_stock_threshold)

    @tool
    def update_product(product_id: int, fields: dict) -> dict:
        """Update an existing product's fields. MUTATING — requires confirmation."""
        return propose_update_product(session_key, user.id, product_id, fields)

    @tool
    def delete_product(product_id: int) -> dict:
        """Delete (soft-delete) a product. MUTATING and destructive."""
        return propose_delete_product(session_key, user.id, product_id)

    @tool
    def create_category(name: str, description: Optional[str] = "") -> dict:
        """Create a new product category. MUTATING — requires confirmation."""
        if description is None:
            description = ""
        return propose_create_category(session_key, user.id, name, description)

    @tool
    def update_category(category_id: int, fields: dict) -> dict:
        """Update a category's fields. MUTATING — requires confirmation."""
        return propose_update_category(session_key, user.id, category_id, fields)

    @tool
    def delete_category(category_id: int) -> dict:
        """Delete a category. MUTATING and destructive."""
        return propose_delete_category(session_key, user.id, category_id)

    @tool
    def get_categories() -> dict:
        """List all product categories with their product counts. Read-only."""
        return _get_categories(user)

    @tool
    def list_products(category_id: Optional[int] = None, search: Optional[str] = None, limit: Optional[int] = 20) -> dict:
        """List products, optionally filtered. Read-only."""
        if limit is None:
            limit = 20
        return _list_products(user, category_id, search, limit)

    @tool
    def list_customers(search: Optional[str] = None) -> dict:
        """List customers with real customer_id, total_orders, total_spent. Read-only."""
        return _list_customers(user, search)

    @tool
    def check_inventory(product_id: int) -> dict:
        """Check the current stock quantity of a product. Read-only."""
        return _check_inventory(user, product_id)

    @tool
    def update_inventory(product_id: int, quantity: int) -> dict:
        """Set a product's stock quantity. MUTATING — requires confirmation."""
        return propose_update_inventory(session_key, user.id, product_id, quantity)

    @tool
    def low_stock(threshold: Optional[int] = None) -> dict:
        """List low-stock products. Read-only."""
        return _low_stock(user, threshold)

    @tool
    def get_order_details(order_id: str) -> dict:
        """Get order details by order number. Read-only."""
        return _get_order_details(user, order_id)

    @tool
    def update_order(order_id: str, fields: dict) -> dict:
        """Update an order's status/tracking_number. MUTATING — requires confirmation."""
        return propose_update_order(session_key, user.id, order_id, fields)

    @tool
    def cancel_order(order_id: str, reason: Optional[str] = "") -> dict:
        """Cancel an order. MUTATING and semi-irreversible."""
        if reason is None:
            reason = ""
        return propose_cancel_order(session_key, user.id, order_id, reason)

    @tool
    def track_order(order_id: str) -> dict:
        """Get the current status of an order. Read-only."""
        return _track_order(user, order_id)

    @tool
    def confirm_pending_action(action_id: str) -> dict:
        """Execute a previously proposed mutating action after the admin has
        explicitly confirmed it via chat (text 'haan'/'confirm'). Only call
        this AFTER a clear confirmation for that specific action_id."""
        status, result = execute_pending_action_by_id(action_id)
        if status == 'not_found':
            return {'success': False, 'error': 'This confirmation was not found or was already resolved.'}
        if status == 'expired':
            return {'success': False, 'error': 'This confirmation has expired. Please repeat the request.'}
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
    @tool
    def sales_report(date_range: Optional[str] = "last_30_days") -> dict:
        """Get a sales summary report. Read-only."""
        if date_range is None:
            date_range = "last_30_days"
        return _sales_report(user, date_range)

    @tool
    def revenue_report(date_range: Optional[str] = "last_30_days") -> dict:
        """Get a revenue breakdown report. Read-only."""
        if date_range is None:
            date_range = "last_30_days"
        return _revenue_report(user, date_range)

    @tool
    def best_sellers(date_range: Optional[str] = "last_30_days", limit: Optional[int] = 5) -> dict:
        """Get top-selling products. Read-only."""
        if date_range is None:
            date_range = "last_30_days"
        if limit is None:
            limit = 5
        return _best_sellers(user, date_range, limit)

    @tool
    def customer_growth(date_range: Optional[str] = "last_30_days") -> dict:
        """Get new customer count. Read-only."""
        if date_range is None:
            date_range = "last_30_days"
        return _customer_growth(user, date_range)

    return [sales_report, revenue_report, best_sellers, customer_growth]


def get_admin_agent_tools(session_key: str, user):
    return get_admin_operations_tools(session_key, user) + get_analytics_tools(user)