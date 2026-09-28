# PATH: apps/products/review_urls.py

from django.urls import path
from .review_views import (
    ProductReviewListCreateView,
    ReviewDetailView,
    AdminReviewListView,
    AdminReviewModerateView,
)

urlpatterns = [
    path(
        'products/<int:product_id>/reviews/',
        ProductReviewListCreateView.as_view(),
        name='product-reviews',
    ),
    path(
        'reviews/<int:review_id>/',
        ReviewDetailView.as_view(),
        name='review-detail',
    ),
    # NEW (Sep 2026 — review moderation queue): admin-only endpoints.
    path(
        'admin/reviews/',
        AdminReviewListView.as_view(),
        name='admin-review-list',
    ),
    path(
        'admin/reviews/<int:review_id>/moderate/',
        AdminReviewModerateView.as_view(),
        name='admin-review-moderate',
    ),
]