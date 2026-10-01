# PATH: apps/users/phone_change_views.py
# NEW (Sep 2026 — Profile: phone number change with OTP code-entry
# verification). Mirrors email_change_views.py exactly, except the code is
# emailed to the account's ALREADY-registered email (there is no SMS
# gateway in this project — same reasoning as PhoneVerification /
# EmailChangeRequest in models.py) instead of to the new phone number.
#
# Two-step flow so a plain PUT to /me/update/ can never change the phone
# number without it being verified:
#   1. POST /me/phone/change/  — pass the new phone number.
#      Sends a 6-digit code to the CURRENT registered email.
#      The account's phone is NOT changed at this point.
#   2. POST /me/phone/confirm/ — pass the code. Only then does
#      user.phone actually change, and phone_verified is set True.

import random
import re
from datetime import timedelta

from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import PhoneChangeRequest
from .serializers import UserProfileSerializer
from .email_service import send_phone_change_code
from .phone_validation import validate_pk_phone, same_phone


def generate_otp():
    return f"{random.randint(0, 999999):06d}"


def _validate_phone_format(value):
    """
    UPDATED (Oct 2026): the Profile page now only accepts a Pakistani
    mobile number - 03XXXXXXXXX or +923XXXXXXXXX (see phone_validation.py).
    Returns (cleaned_phone, error_string_or_None).
    """
    return validate_pk_phone(value)


class RequestPhoneChangeView(APIView):
    """
    POST /api/v1/auth/me/phone/change/

    Request:
    { "phone": "03001234567" }

    Response (200):
    { "message": "string" }

    Sends a 6-digit code to the account's CURRENT registered email
    address. Does NOT change user.phone — that only happens after the
    code is confirmed via ConfirmPhoneChangeView. Overwrites any still-
    pending phone change request for this user — only the most recently
    requested code is ever valid.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        phone = request.data.get('phone')

        if not phone:
            return Response(
                {'error': 'phone is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        phone, format_error = _validate_phone_format(phone)
        if format_error:
            return Response({'error': format_error}, status=status.HTTP_400_BAD_REQUEST)

        # Same number in the other form (03001234567 / +923001234567)
        # also counts as "already your number".
        if same_phone(phone, user.phone):
            return Response(
                {'error': 'That is already your current phone number.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        code = generate_otp()
        PhoneChangeRequest.objects.update_or_create(
            user=user,
            defaults={
                'new_phone': phone,
                'otp_code': code,
                'otp_expires_at': timezone.now() + timedelta(minutes=10),
            },
        )

        send_phone_change_code(user, phone, code)

        return Response(
            {
                'message': (
                    f'A verification code has been sent to {user.email}. '
                    'Enter it to confirm your new phone number.'
                ),
            },
            status=status.HTTP_200_OK,
        )


class ConfirmPhoneChangeView(APIView):
    """
    POST /api/v1/auth/me/phone/confirm/
    Request: { "otp": "123456" }

    Only on a valid, unexpired code does user.phone actually change.
    phone_verified is set True since receiving and re-entering this code
    (sent to the account's own verified email) already proves the
    change was made by the real account owner.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        otp = request.data.get('otp')

        if not otp:
            return Response(
                {'error': 'otp is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            change_request = PhoneChangeRequest.objects.get(user=user)
        except PhoneChangeRequest.DoesNotExist:
            return Response(
                {'error': 'No pending phone change found. Call /me/phone/change/ first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if change_request.otp_code != otp:
            return Response({'error': 'Invalid code.'}, status=status.HTTP_400_BAD_REQUEST)

        if not change_request.is_valid():
            return Response(
                {'error': 'Code has expired. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_phone = user.phone
        user.phone = change_request.new_phone
        user.phone_verified = True
        user.save(update_fields=['phone', 'phone_verified'])

        # NEW: keep the store-scoped Customer copy(ies), saved addresses
        # and orders that carried the old number in step with the new
        # one (see apps/users/customer_sync.py)
        from .customer_sync import sync_customer_profiles
        sync_customer_profiles(user, ["phone"], old_phone=old_phone)

        change_request.delete()

        return Response(
            {
                'message': 'Phone number updated successfully.',
                'user': UserProfileSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )