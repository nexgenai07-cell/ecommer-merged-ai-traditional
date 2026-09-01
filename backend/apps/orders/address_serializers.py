# PATH: apps/orders/address_serializers.py

import re

from rest_framework import serializers
from .models import Address

# Reused as-is from serializers.py (checkout) so address-book validation
# matches checkout validation exactly.
PHONE_RE = re.compile(r'^(\+92|0)\d{9,10}$')
POSTAL_CODE_RE = re.compile(r'^\d{4,6}$')


# GET /api/v1/addresses/ list items, and the response shape for
# create/update/set-default.
class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            "id",
            "label",
            "shipping_address",
            "city",
            "postal_code",
            "phone",
            "is_default",
        ]


# POST /api/v1/addresses/ (create) and PUT /api/v1/addresses/{id}/ (edit) —
# same fields for both, per the spec ("Same fields as create.").
class AddressWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            "label",
            "shipping_address",
            "city",
            "postal_code",
            "phone",
            "is_default",
        ]
        extra_kwargs = {
            "postal_code": {"required": False, "allow_blank": True},
            "phone": {"required": False, "allow_blank": True},
            "is_default": {"required": False, "default": False},
        }

    def validate_label(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Label cannot be blank.")
        return value

    def validate_shipping_address(self, value):
        value = value.strip()
        if len(value) < 8:
            raise serializers.ValidationError(
                "Shipping address looks too short — please enter a full address."
            )
        return value

    def validate_city(self, value):
        value = value.strip()
        if any(ch.isdigit() for ch in value):
            raise serializers.ValidationError(
                "City name should not contain numbers."
            )
        return value

    def validate_postal_code(self, value):
        value = (value or "").strip()
        if value and not POSTAL_CODE_RE.match(value):
            raise serializers.ValidationError(
                "Postal code should be 4-6 digits (leave blank if unknown)."
            )
        return value

    def validate_phone(self, value):
        cleaned = re.sub(r'[\s-]', '', value or "")
        if cleaned and not PHONE_RE.match(cleaned):
            raise serializers.ValidationError(
                "Enter a valid phone number, e.g. 03001234567 or +923001234567."
            )
        return cleaned