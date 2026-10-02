# PATH: apps/products/review_views.py

from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db import IntegrityError
from django.db.models import Avg

from core.pagination import StandardResultsPagination
from apps.users.permissions import IsAdmin
from .models import Product, Review
from .review_serializers import (
    ReviewSerializer,
    ReviewCreateSerializer,
    AdminReviewSerializer,
    ReviewModerateSerializer,
    get_profile_picture_error,
)


def _get_rating_summary(product):
    """
    Powers the "4.8, Based on 2,450 reviews" block and the star-by-star
    percentage bar chart (5-star down to 1-star) on the product detail
    page.

    UPDATED (Sep 2026 — review moderation queue): only counts reviews
    with status='approved' — a pending or rejected review no longer
    moves the average or the review count, since it isn't visible to
    customers at all yet.
    """
    reviews = Review.objects.filter(
        product=product, status="approved", is_delete=False
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


def get_review_eligibility(user, product):
    """
    NEW (Oct 2026 - can_review): the ONE place that decides whether a user
    may review a product. Used by BOTH GET (the can_review flag) and POST
    (the 403), so the flag the frontend shows and the server's real answer
    can never differ.

    Returns (can_review, is_verified_purchase):
      - guest                          -> (False, False)
      - admin                          -> (True, <has a delivered order?>)
                                          admins are exempt from the
                                          purchase rule (existing behaviour)
      - customer with a DELIVERED order
        containing this product        -> (True, True)
      - anyone else (never bought it,
        or not delivered yet)          -> (False, False)
    """
    if not user or not user.is_authenticated:
        return False, False

    # Imported inline (not at module level) to avoid a circular
    # import between apps.products and apps.orders at startup.
    from apps.orders.models import OrderItem

    is_verified = OrderItem.objects.filter(
        product=product,
        order__customer__user=user,
        order__status="delivered",
    ).exists()

    is_admin = getattr(user, "role", None) == "admin"
    return (is_admin or is_verified), is_verified


class ProductReviewListCreateView(APIView):
    """
    GET  /api/v1/products/{product_id}/reviews/  -> paginated review list
                                                      + rating summary
                                                      (approved reviews only)
    POST /api/v1/products/{product_id}/reviews/  -> create a review
                                                      (logged-in only)

    One review per user per product — enforced at the DB level (see
    Review.Meta.constraints). Posting a second review for the same
    product returns a 400 telling the customer to edit their existing
    one instead, via PUT /api/v1/reviews/{id}/.

    is_verified_purchase is computed here from the customer's own order
    history — never accepted from the request body — based on whether
    they have at least one DELIVERED order containing this product.

    NEW (Sep 2026 — review moderation queue): every review a regular
    customer submits now starts status='pending' and is invisible on
    the product page until an admin approves it via
    AdminReviewModerateView below — it does NOT show up in GET
    immediately anymore. An admin's own review is auto-approved (no
    self-moderation needed).

    NEW (Sep 2026 — reviewer profile picture): POST accepts an optional
    multipart field `profile_picture` (a photo of the reviewer). Fully
    optional — omit it and the review is created with no picture, same
    as before this change. Accepts either multipart/form-data (needed
    to actually attach a file) or plain JSON (when no picture is being
    attached) — see parser_classes below.
    """
    pagination_class = StandardResultsPagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]

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

        # UPDATED (Sep 2026 — review moderation queue): status='approved'
        # replaces the old is_active=True filter — see Review.status.
        reviews_qs = Review.objects.filter(
            product=product, status="approved", is_delete=False
        ).select_related("user").order_by("-created_at")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(reviews_qs, request)
        serializer = ReviewSerializer(page, many=True)

        response = paginator.get_paginated_response(serializer.data)
        response.data["summary"] = _get_rating_summary(product)

        # NEW (Oct 2026): can_review - true only when POST would not
        # return the 403 (same helper as POST). Plain boolean, top level.
        # The response now depends on who is logged in, so it must never be
        # served from a shared cache.
        response.data["can_review"], _ = get_review_eligibility(request.user, product)
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

        # UPDATED (Oct 2026): eligibility now comes from
        # get_review_eligibility(), shared with GET's can_review flag.
        can_review, is_verified = get_review_eligibility(request.user, product)

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

        if not can_review:
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

        # NEW (Sep 2026 — review moderation queue): a customer's review
        # goes to 'pending' and needs an admin to approve it before it's
        # visible anywhere on the site. An admin's own review is
        # auto-approved — there's no one else who needs to moderate it,
        # and this matches the existing "admin is exempt from the
        # verified-purchase check" exemption right above.
        review_status = "approved" if is_admin else "pending"

        # NEW (Sep 2026 — reviewer profile picture): fully optional —
        # request.FILES.get() returns None when the field wasn't sent,
        # and CloudinaryField happily stores None (blank=True, null=True).
        profile_picture = request.FILES.get("profile_picture")

        # NEW (Sep 2026 — reviewer profile picture): reject a wrong-type
        # or oversized file with a clear 400 before touching Cloudinary.
        picture_error = get_profile_picture_error(profile_picture)
        if picture_error:
            return Response(
                {"error": picture_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # NEW (Sep 2026 — one review per customer per product): the
        # exists() check near the top of this method catches the normal
        # case, but two requests arriving at the same instant can both
        # pass it. The DB-level unique constraint (Review.Meta.
        # constraints) then rejects the second one — caught here so the
        # customer gets the same clear 400 instead of a 500 error.
        try:
            review = Review.objects.create(
                product=product,
                user=request.user,
                rating=serializer.validated_data["rating"],
                comment=serializer.validated_data.get("comment", ""),
                is_verified_purchase=is_verified,
                status=review_status,
                profile_picture=profile_picture,
            )
        except IntegrityError:
            return Response(
                {
                    "error": (
                        "You have already reviewed this product. "
                        "Edit your existing review instead."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_data = ReviewSerializer(review).data
        if review_status == "pending":
            response_data["message"] = (
                "Your review has been submitted and is awaiting admin "
                "approval before it appears on the product page."
            )

        return Response(response_data, status=status.HTTP_201_CREATED)


class ReviewDetailView(APIView):
    """
    PUT/PATCH /api/v1/reviews/{id}/  -> edit own review (rating/comment)
    DELETE    /api/v1/reviews/{id}/  -> soft-delete own review

    Ownership enforced: a customer can only edit/delete their own
    review. Admin moderation (approve/reject) is a separate, admin-only
    concern — see AdminReviewListView / AdminReviewModerateView below.

    NEW (Sep 2026 — review moderation queue): editing a review that was
    already approved sends it back to status='pending' — otherwise a
    customer could get one review approved, then silently edit the text
    into something an admin never actually saw, bypassing moderation
    entirely. An admin editing their own (auto-approved) review is
    exempt, same as at creation time.

    NEW (Sep 2026 — reviewer profile picture): PUT/PATCH also accepts an
    optional multipart field `profile_picture` to add or replace the
    reviewer's photo. Omit it and the existing picture (or lack of one)
    is left untouched.
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

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

        # NEW (Sep 2026 — reviewer profile picture): validate the new
        # file BEFORE saving anything, so a bad picture doesn't leave
        # the rating/comment half-updated.
        picture_error = get_profile_picture_error(
            request.FILES.get("profile_picture")
        )
        if picture_error:
            return Response(
                {"error": picture_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer.save()

        # NEW (Sep 2026 — reviewer profile picture): only touched when
        # the caller actually sent a new file — omitting it leaves
        # whatever picture (or lack of one) the review already had.
        if "profile_picture" in request.FILES:
            review.profile_picture = request.FILES["profile_picture"]
            review.save(update_fields=["profile_picture", "updated_at"])

        # NEW (Sep 2026 — review moderation queue): re-queue for review
        # unless this is the admin's own (always-approved) review.
        is_admin = request.user.is_authenticated and request.user.role == "admin"
        if not is_admin and review.status != "pending":
            review.status = "pending"
            review.save(update_fields=["status", "updated_at"])

        response_data = ReviewSerializer(review).data
        if review.status == "pending":
            response_data["message"] = (
                "Your changes have been submitted and are awaiting admin "
                "re-approval before they appear on the product page."
            )
        return Response(response_data)

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


# ============================================================
# NEW (Sep 2026 — review moderation queue): admin-only endpoints.
# Everything below is new; nothing above this point changes the
# public-facing review contract except the pending/approved/rejected
# status behaviour already described in the classes above.
# ============================================================

class AdminReviewListView(APIView):
    """
    GET /api/v1/admin/reviews/?status=pending|approved|rejected&product_id=&page=

    Admin's review moderation queue. Defaults to status=pending (the
    "needs my attention" queue an admin dashboard would show first);
    pass ?status=all to see every review regardless of status, or an
    exact status to filter to just that bucket.

    Shows the real reviewer name (unlike the public ReviewSerializer,
    which shows "Anonymous" for an admin-authored review) — the admin
    moderating needs to know who actually wrote it.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = StandardResultsPagination

    def get(self, request):
        qs = Review.objects.filter(is_delete=False).select_related(
            "user", "product"
        ).order_by("-created_at")

        status_param = (request.query_params.get("status") or "pending").lower()
        valid_statuses = {choice[0] for choice in Review.STATUS_CHOICES}
        if status_param in valid_statuses:
            qs = qs.filter(status=status_param)
        elif status_param != "all":
            qs = qs.filter(status="pending")

        product_id = request.query_params.get("product_id")
        if product_id:
            qs = qs.filter(product_id=product_id)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = AdminReviewSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminReviewModerateView(APIView):
    """
    PATCH /api/v1/admin/reviews/{review_id}/moderate/
    Body: {"action": "approve"} or {"action": "reject"}

    Sets Review.status accordingly. Approving makes the review
    immediately visible on the product page (next GET
    /products/{id}/reviews/); rejecting keeps it permanently hidden
    from customers (it stays in the DB, distinguishable from 'pending'
    for the admin's own records, but never shown on the site).
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def patch(self, request, review_id):
        try:
            review = Review.objects.get(id=review_id, is_delete=False)
        except Review.DoesNotExist:
            return Response(
                {"error": "Review not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ReviewModerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]

        review.status = "approved" if action == "approve" else "rejected"
        review.save(update_fields=["status", "updated_at"])

        return Response(AdminReviewSerializer(review).data)