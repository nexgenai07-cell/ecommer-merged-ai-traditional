# PATH: ecommerce/apps/stores/models.py

from django.db import models
from django.conf import settings


class Store(models.Model):
    PLAN_CHOICES = [
        ('basic',      'Basic'),
        ('pro',        'Pro'),
        ('enterprise', 'Enterprise'),
    ]

    # UPDATED (v4.0): owner (single ForeignKey) removed — a store can now
    # have multiple, equal admins instead of one primary owner. Any user
    # with role='admin' already has full dashboard access (IsAdmin
    # permission is role-based, not tied to this field) — this field is
    # used purely to decide who receives store-level notifications (new
    # orders, payments, complaints, low stock, etc.).
    admins     = models.ManyToManyField(
                    settings.AUTH_USER_MODEL,
                    related_name='administered_stores',
                    blank=True,
                 )
    name       = models.CharField(max_length=255)
    logo = models.ImageField(
    upload_to='store_logos/',
    blank=True,
    null=True
                 )
    subdomain  = models.CharField(max_length=100, unique=True)
    phone      = models.CharField(max_length=20, blank=True, null=True)
    address    = models.TextField(blank=True, null=True)
    plan       = models.CharField(max_length=20, choices=PLAN_CHOICES, default='basic')
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stores'

    def __str__(self):
        return self.name