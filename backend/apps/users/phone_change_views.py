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


def generate_otp():
    return f"{random.randint(0, 999999):06d}"


def _validate_phone_format(value):
    """
    Same digit/length rules as RegisterSerializer.validate_phone, so a
    phone number is held to the same standard whether it's entered at
    registration or changed later from the Profile page.
    Returns an error string, or None if the value is valid.
    """
    digits = value[1:] if value.startswith('+') else value

    if not digits or not digits.isdigit():
        return "Phone number must contain digits only, optionally starting with '+'."

    if len(digits) < 10:
        return "Phone number must be at least 10 digits."

    if len(digits) > 15:
        return "Phone number must not exceed 15 digits."

    return None


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

        phone = phone.strip()

        format_error = _validate_phone_format(phone)
        if format_error:
            return Response({'error': format_error}, status=status.HTTP_400_BAD_REQUEST)

        if phone == user.phone:
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

        user.phone = change_request.new_phone
        user.phone_verified = True
        user.save(update_fields=['phone', 'phone_verified'])

        change_request.delete()

        return Response(
            {
                'message': 'Phone number updated successfully.',
                'user': UserProfileSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )