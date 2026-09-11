# PATH: ecommerce/apps/categories/models.py

from django.db import models
from cloudinary.models import CloudinaryField


class Category(models.Model):
    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    # Store category image in Cloudinary
    image = CloudinaryField(
        "category_image",
        blank=True,
        null=True,
    )

    is_active = models.BooleanField(default=True)
    is_delete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "categories"
        ordering = ["name"]
        verbose_name_plural = "categories"
        # FIX (Soft-delete name reuse bug report, Sep 2026): same issue
        # as Product.name — "name" was globally unique=True, so a
        # soft-deleted category's name stayed permanently blocked even
        # though the category itself is invisible everywhere. Replaced
        # with a partial unique constraint scoped to non-deleted rows
        # only, matching the check the serializer's validate_name
        # already does.
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(is_delete=False),
                name="unique_active_category_name",
            ),
        ]

    def __str__(self):
        return self.name