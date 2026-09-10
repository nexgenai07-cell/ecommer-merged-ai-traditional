# PATH: apps/stores/admin.py

from django.contrib import admin
from .models import Store


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'subdomain', 'plan', 'is_active', 'created_at']
    search_fields = ['name', 'subdomain']