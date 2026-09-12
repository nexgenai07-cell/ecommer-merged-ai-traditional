# PATH: apps/orders/customer_stats_views.py

from decimal import Decimal

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Customer, Order


class MyOrderStatsView(APIView):
    """
    GET /api/v1/orders/stats/

    Customer-facing "Total Spent" / "Total Orders" for their own account
    dashboard.

    NEW (Sep 2026 — customer dashboard "Total Spent" bug): no backend
    endpoint existed for this before — the frontend's own dashboard was
    presumably summing every order returned by GET /api/v1/orders/
    (which has no status filtering at all), so pending_payment/on_hold/
    cancelled orders were all being counted as "spent". This gives the
    frontend one authoritative number instead, using the exact same
    Order.REVENUE_STATUSES rule (confirmed / shipped / out_for_delivery
    / delivered) as the admin customer list and admin revenue dashboard,
    so all three always agree.

    A user can have more than one Customer profile (one per store, see
    Customer.unique_together) — this sums across all of that user's own
    profiles, not just one store.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        customer_ids = Customer.objects.filter(
            user=request.user
        ).values_list("id", flat=True)

        orders = Order.objects.filter(
            customer_id__in=customer_ids,
            status__in=Order.REVENUE_STATUSES,
        )

        total_spent = sum(
            (order.total_amount for order in orders),
            Decimal("0.00"),
        )

        return Response(
            {
                "total_orders": orders.count(),
                "total_spent": total_spent,
            }
        )