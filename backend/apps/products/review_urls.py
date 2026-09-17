# PATH: apps/products/review_urls.py

from django.urls import path
from .review_views import ProductReviewListCreateView, ReviewDetailView

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
]