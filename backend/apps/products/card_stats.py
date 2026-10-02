# PATH: apps/products/card_stats.py
#
# NEW (Oct 2026 - storefront product cards): average_rating, review_count
# and total_sold for product LISTS (Home, Shop, search, related products,
# wishlist, cart). One place, same rules as Get Single Product (API 30,
# ProductDetailSerializer):
#
#   average_rating : mean rating of APPROVED, non-deleted reviews, rounded
#                    to 1 decimal, 0.0 when there are none
#   review_count   : number of those same reviews, 0 when none
#   total_sold     : sum of OrderItem.quantity over orders whose status is
#                    in Order.REVENUE_STATUSES (payment confirmed) - the
#                    exact rule get_total_sold uses on the detail page
#
# NOTE: the request mentioned ProductStats as the source for total_sold,
# but the detail page no longer reads it (nothing ever wrote to that table,
# so it was always 0) - it counts real orders. The list uses the SAME
# rule so the card and the detail page can never show different numbers.
#
# No N+1: lists get the three numbers as ONE annotated query
# (annotate_card_stats); nested product objects (wishlist / cart) get them
# from ONE bulk lookup per response (card_stats_for_products).

from django.db.models import Avg, Count, FloatField, IntegerField, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce


def annotate_card_stats(queryset):
    """Adds _card_avg_rating / _card_review_count / _card_total_sold to a
    Product queryset as correlated subqueries (no join, so filters,
    ordering, distinct() and pagination counts are unaffected)."""
    from apps.orders.models import Order, OrderItem
    from .models import Review

    approved = Review.objects.filter(
        product=OuterRef("pk"), status="approved", is_delete=False
    ).order_by().values("product")

    avg_sq = approved.annotate(v=Avg("rating")).values("v")
    count_sq = approved.annotate(v=Count("pk")).values("v")

    sold_sq = (
        OrderItem.objects.filter(
            product=OuterRef("pk"),
            order__status__in=Order.REVENUE_STATUSES,
        )
        .order_by()
        .values("product")
        .annotate(v=Sum("quantity"))
        .values("v")
    )

    return queryset.annotate(
        _card_avg_rating=Subquery(avg_sq, output_field=FloatField()),
        _card_review_count=Coalesce(Subquery(count_sq, output_field=IntegerField()), 0),
        _card_total_sold=Coalesce(Subquery(sold_sq, output_field=IntegerField()), 0),
    )


def card_stats_for_products(product_ids):
    """{product_id: {"average_rating", "review_count", "total_sold"}} for
    the given ids using 2 grouped queries (used by wishlist / cart)."""
    from apps.orders.models import Order, OrderItem
    from .models import Review

    ids = list({pid for pid in product_ids if pid is not None})
    stats = {
        pid: {"average_rating": 0.0, "review_count": 0, "total_sold": 0}
        for pid in ids
    }
    if not ids:
        return stats

    for row in (
        Review.objects.filter(product_id__in=ids, status="approved", is_delete=False)
        .values("product_id")
        .annotate(avg=Avg("rating"), n=Count("pk"))
    ):
        stats[row["product_id"]]["average_rating"] = (
            round(row["avg"], 1) if row["avg"] is not None else 0.0
        )
        stats[row["product_id"]]["review_count"] = row["n"]

    for row in (
        OrderItem.objects.filter(
            product_id__in=ids, order__status__in=Order.REVENUE_STATUSES
        )
        .values("product_id")
        .annotate(sold=Sum("quantity"))
    ):
        stats[row["product_id"]]["total_sold"] = row["sold"] or 0

    return stats


class CardStatsMixin:
    """Gives a product serializer the three card fields. Declare them on
    the serializer as SerializerMethodField() (average_rating, review_count,
    total_sold) and list them in Meta.fields.

    Value source, cheapest first:
      1. annotations on the object (annotate_card_stats) - list endpoints
      2. self.context["card_stats"] - filled once per response by a parent
         serializer (wishlist / cart)
      3. a single lookup for this one product (never hit on the normal
         paths above, only a safety net so the fields are never missing)
    """

    def _card_stats(self, obj):
        if hasattr(obj, "_card_avg_rating"):
            return {
                "average_rating": round(float(obj._card_avg_rating), 1)
                if obj._card_avg_rating is not None else 0.0,
                "review_count": int(obj._card_review_count or 0),
                "total_sold": int(obj._card_total_sold or 0),
            }
        bulk = (self.context or {}).get("card_stats")
        if bulk is not None and obj.pk in bulk:
            return bulk[obj.pk]
        return card_stats_for_products([obj.pk])[obj.pk]

    def get_average_rating(self, obj):
        return self._card_stats(obj)["average_rating"]

    def get_review_count(self, obj):
        return self._card_stats(obj)["review_count"]

    def get_total_sold(self, obj):
        return self._card_stats(obj)["total_sold"]