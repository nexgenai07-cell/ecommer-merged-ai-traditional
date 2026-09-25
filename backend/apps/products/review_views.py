# PATH: apps/products/review_views.py

from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Avg

from core.pagination import StandardResultsPagination
from .models import Product, Review
from .review_serializers import ReviewSerializer, ReviewCreateSerializer


def _get_rating_summary(product):
    """
    Powers the "4.8, Based on 2,450 reviews" block and the star-by-star
    percentage bar chart (5-star down to 1-star) on the product detail
    page. Only counts reviews that are active and not deleted.
    """
    reviews = Review.objects.filter(
        product=product, is_active=True, is_delete=False
    )
    total = reviews.count()

    average = reviews.aggregate(avg=Avg("rating"))["avg"] or 0

    breakdown = {}
    for star in range(5, 0, -1):
        count = reviews.filter(rating=star).count()
        breakdown[str(star)] = round((count / total) * 100, 1) if total else 0.0

    return {
        "average_rating": round(average, 1),
        "review_count": total,
        "breakdown": breakdown,
    }


class ProductReviewListCreateView(APIView):
    """
    GET  /api/v1/products/{product_id}/reviews/  -> paginated review list
                                                      + rating summary
    POST /api/v1/products/{product_id}/reviews/  -> create a review
                                                      (logged-in only)

    One review per user per product — enforced at the DB level (see
    Review.Meta.constraints). Posting a second review for the same
    product returns a 400 telling the customer to edit their existing
    one instead, via PUT /api/v1/reviews/{id}/.

    is_verified_purchase is computed here from the customer's own order
    history — never accepted from the request body — based on whether
    they have at least one DELIVERED order containing this product.
    """
    pagination_class = StandardResultsPagination

    def get_permissions(self):
        if self.request.method == "POST":
            return [permissions.IsAuthenticated()]
        return [permissions.AllowAny()]

    def get(self, request, product_id):
        try:
            product = Product.objects.get(id=product_id, is_delete=False)
        except Product.DoesNotExist:
            return Response(
                {"error": "Product not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        reviews_qs = Review.objects.filter(
            product=product, is_active=True, is_delete=False
        ).select_related("user").order_by("-created_at")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(reviews_qs, request)
        serializer = ReviewSerializer(page, many=True)

        response = paginator.get_paginated_response(serializer.data)
        response.data["summary"] = _get_rating_summary(product)
        return response

    def post(self, request, product_id):
        try:
            product = Product.objects.get(id=product_id, is_delete=False)
        except Product.DoesNotExist:
            return Response(
                {"error": "Product not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if Review.objects.filter(
            product=product, user=request.user, is_delete=False
        ).exists():
            return Response(
                {
                    "error": (
                        "You have already reviewed this product. "
                        "Edit your existing review instead."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Imported inline (not at module level) to avoid a circular
        # import between apps.products and apps.orders at startup.
        from apps.orders.models import OrderItem

        is_verified = OrderItem.objects.filter(
            product=product,
            order__customer__user=request.user,
            order__status="delivered",
        ).exists()

        # NEW (Sep 2026 — review eligibility bug): a review used to be
        # accepted from ANY logged-in user regardless of whether they had
        # ever bought this exact product — is_verified_purchase was only
        # ever a cosmetic "Verified Purchase" badge on the response, it
        # was never actually enforced as a requirement to post a review
        # in the first place. Now a non-admin customer must have at
        # least one DELIVERED order containing this specific product
        # (the same check already used for is_verified above) before
        # they're allowed to review it at all — buying a different
        # product, or this same product but not yet delivered, no
        # longer qualifies. Admins are exempt from this check: they were
        # already able to post a review that shows up anonymously
        # (never expected to have bought the product themselves), and
        # that existing behaviour is unchanged here.
        is_admin = (
            request.user.is_authenticated and request.user.role == "admin"
        )

        if not is_admin and not is_verified:
            return Response(
                {
                    "error": (
                        "You can only review products you have "
                        "purchased and received. This product needs to "
                        "be delivered to your account before you can "
                        "leave a review."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        review = Review.objects.create(
            product=product,
            user=request.user,
            rating=serializer.validated_data["rating"],
            comment=serializer.validated_data.get("comment", ""),
            is_verified_purchase=is_verified,
        )

        return Response(
            ReviewSerializer(review).data,
            status=status.HTTP_201_CREATED,
        )


class ReviewDetailView(APIView):
    """
    PUT/PATCH /api/v1/reviews/{id}/  -> edit own review (rating/comment)
    DELETE    /api/v1/reviews/{id}/  -> soft-delete own review

    Ownership enforced: a customer can only edit/delete their own
    review. Admin moderation (hiding a review via is_active) is a
    separate, admin-only concern and is not exposed here.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, review_id, user):
        try:
            return Review.objects.get(id=review_id, user=user, is_delete=False)
        except Review.DoesNotExist:
            return None

    def put(self, request, review_id):
        review = self.get_object(review_id, request.user)
        if not review:
            return Response(
                {"error": "Review not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ReviewCreateSerializer(
            review, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(ReviewSerializer(review).data)

    def patch(self, request, review_id):
        return self.put(request, review_id)

    def delete(self, request, review_id):
        review = self.get_object(review_id, request.user)
        if not review:
            return Response(
                {"error": "Review not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        review.is_delete = True
        review.save(update_fields=["is_delete", "updated_at"])

        return Response(status=status.HTTP_204_NO_CONTENT)