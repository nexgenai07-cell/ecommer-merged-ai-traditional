# PATH: apps/products/review_serializers.py

from rest_framework import serializers
from .models import Review


# NEW (Sep 2026 — reviewer profile picture validation): this is a
# customer-facing upload (unlike ProductImage, which only admins can
# upload), so it's checked before anything is sent to Cloudinary.
PROFILE_PICTURE_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
PROFILE_PICTURE_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


def get_profile_picture_error(file):
    """
    Returns an error message string if `file` isn't an acceptable
    profile picture, or None if it's fine (or if no file was sent at
    all — the picture is optional).
    """
    if file is None:
        return None
    if file.size > PROFILE_PICTURE_MAX_BYTES:
        return "Profile picture must be 2 MB or smaller."
    if (getattr(file, "content_type", "") or "").lower() not in PROFILE_PICTURE_ALLOWED_TYPES:
        return "Profile picture must be a JPG, PNG or WEBP image."
    return None


# Used for GET — listing reviews on a product's detail page.
class ReviewSerializer(serializers.ModelSerializer):
    # UPDATED (Sep 2026 — admin reviews show as "Anonymous"): was a
    # plain `source="user.name"` CharField before, which leaked the
    # admin's real name whenever the review was admin-authored. Now a
    # SerializerMethodField so an admin-authored review always displays
    # as "Anonymous" to customers, same as everywhere else in the app
    # an admin-authored review is described as anonymous.
    user_name = serializers.SerializerMethodField()

    # NEW (Sep 2026 — reviewer profile picture): optional, null when the
    # reviewer didn't attach one. Same url-building pattern as
    # ProductImageSerializer.get_image_url in serializers.py.
    profile_picture_url = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = [
            "id",
            "user_name",
            "profile_picture_url",
            "rating",
            "comment",
            "is_verified_purchase",
            "created_at",
        ]

    def get_user_name(self, obj):
        if obj.user and getattr(obj.user, "role", None) == "admin":
            return "Anonymous"
        return obj.user.name if obj.user else "Anonymous"

    def get_profile_picture_url(self, obj):
        # An admin-authored review is shown to customers as "Anonymous"
        # (see get_user_name above), so its picture is hidden too —
        # otherwise the photo would give away who the "Anonymous"
        # reviewer is.
        if obj.user and getattr(obj.user, "role", None) == "admin":
            return None
        if obj.profile_picture:
            return obj.profile_picture.url.replace("http://", "https://")
        return None


# Used for POST (create) and PUT/PATCH (edit) — only rating/comment are
# ever writable by the customer through this serializer. profile_picture
# is a file upload and is handled separately, straight from
# request.FILES in review_views.py — same convention this codebase
# already uses for ProductImage uploads (see ProductViewSet.upload_image
# in views.py) — rather than through a ModelSerializer field.
# is_verified_purchase, user, product, and status are all set
# server-side and never accepted here either.
class ReviewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["rating", "comment"]

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError("Rating must be between 1 and 5.")
        return value


# NEW (Sep 2026 — review moderation queue): admin-only listing
# serializer for AdminReviewListView. Unlike the public ReviewSerializer
# this shows the reviewer's REAL name (an admin moderating needs to
# know who wrote it — anonymising it here would defeat the point of a
# moderation queue) plus the product it's for and its current status.
class AdminReviewSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.name", read_only=True)
    user_email = serializers.CharField(source="user.email", read_only=True)
    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    profile_picture_url = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = [
            "id",
            "product_id",
            "product_name",
            "user_name",
            "user_email",
            "profile_picture_url",
            "rating",
            "comment",
            "is_verified_purchase",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_profile_picture_url(self, obj):
        if obj.profile_picture:
            return obj.profile_picture.url.replace("http://", "https://")
        return None


# NEW (Sep 2026 — review moderation queue): input validation for
# PATCH /api/v1/admin/reviews/{id}/moderate/.
class ReviewModerateSerializer(serializers.Serializer):
    ACTION_CHOICES = ["approve", "reject"]

    action = serializers.ChoiceField(
        choices=ACTION_CHOICES,
        error_messages={
            "required": "action is required.",
            "invalid_choice": "action must be either 'approve' or 'reject'.",
        },
    )