# PATH: ecommerce/apps/categories/models.py

from django.db import models
from cloudinary.models import CloudinaryField


class Category(models.Model):
    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)

    # Store category image in Cloudinary
    image = CloudinaryField(
        "category_image",
        blank=True,
        null=True,
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "categories"
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name