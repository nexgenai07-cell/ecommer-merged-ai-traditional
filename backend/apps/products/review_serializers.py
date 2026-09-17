# PATH: apps/products/review_serializers.py

from rest_framework import serializers
from .models import Review


# Used for GET — listing reviews on a product's detail page.
class ReviewSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.name", read_only=True)

    class Meta:
        model = Review
        fields = [
            "id",
            "user_name",
            "rating",
            "comment",
            "is_verified_purchase",
            "created_at",
        ]


# Used for POST (create) and PUT/PATCH (edit) — only rating/comment are
# ever writable by the customer. is_verified_purchase, user, and product
# are all set server-side in review_views.py and never accepted here.
class ReviewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["rating", "comment"]

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError("Rating must be between 1 and 5.")
        return value