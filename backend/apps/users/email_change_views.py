# PATH: apps/users/email_change_views.py
# NEW (Sep 2026 — Profile: editable email with OTP verification).
# Two-step flow so a plain PUT to /me/ can never move the account to an
# email the user doesn't actually control:
#   1. POST /me/email/change/  — pass current password + new_email.
#      Sends a 6-digit code to the NEW address, and a heads-up notice
#      to the OLD address.
#   2. POST /me/email/confirm/ — pass the code. Only then does
#      user.email actually change.

import random
from datetime import timedelta

from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import User, EmailChangeRequest
from .serializers import UserProfileSerializer
from .email_service import send_email_change_code, send_email_change_notice


def generate_otp():
    return f"{random.randint(0, 999999):06d}"


class RequestEmailChangeView(APIView):
    """
    POST /api/v1/auth/me/email/change/

    Request:
    {
        "password": "current account password",
        "new_email": "new@example.com"
    }

    Requires the current password (same pattern as ChangePasswordView /
    DeleteAccountView elsewhere in this app) so a briefly-unlocked
    device can't be used to hijack the account's email. Overwrites any
    still-pending change request for this user — only the most recently
    requested code is ever valid.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        password = request.data.get('password')
        new_email = request.data.get('new_email')

        if not password or not new_email:
            return Response(
                {'error': 'password and new_email are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.check_password(password):
            return Response(
                {'error': 'Incorrect password.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_email = new_email.strip().lower()

        if new_email == user.email:
            return Response(
                {'error': 'That is already your current email address.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if User.objects.exclude(pk=user.pk).filter(email=new_email).exists():
            return Response(
                {'error': 'A user with this email already exists.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        code = generate_otp()
        EmailChangeRequest.objects.update_or_create(
            user=user,
            defaults={
                'new_email': new_email,
                'otp_code': code,
                'otp_expires_at': timezone.now() + timedelta(minutes=10),
            },
        )

        send_email_change_code(new_email, code)
        # Best-effort security notice — never blocks the flow if it fails.
        send_email_change_notice(user.email, new_email)

        return Response(
            {
                'message': (
                    f'A verification code has been sent to {new_email}. '
                    'Enter it to confirm the change.'
                ),
            },
            status=status.HTTP_200_OK,
        )


class ConfirmEmailChangeView(APIView):
    """
    POST /api/v1/auth/me/email/confirm/
    Request: { "otp": "123456" }

    Only on a valid, unexpired code does user.email actually change.
    email_verified is set True since receiving and re-entering this code
    already proves the new address is reachable — no separate
    send-verification-email/ step is needed afterwards.
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
            change_request = EmailChangeRequest.objects.get(user=user)
        except EmailChangeRequest.DoesNotExist:
            return Response(
                {'error': 'No pending email change found. Call /me/email/change/ first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if change_request.otp_code != otp:
            return Response({'error': 'Invalid code.'}, status=status.HTTP_400_BAD_REQUEST)

        if not change_request.is_valid():
            return Response(
                {'error': 'Code has expired. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Guard against a collision that may have appeared between the
        # request and confirm steps (e.g. someone else registered that
        # email in the meantime).
        if User.objects.exclude(pk=user.pk).filter(email=change_request.new_email).exists():
            change_request.delete()
            return Response(
                {'error': 'That email is no longer available.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.email = change_request.new_email
        user.email_verified = True
        user.save(update_fields=['email', 'email_verified'])

        change_request.delete()

        return Response(
            {
                'message': 'Email address updated successfully.',
                'user': UserProfileSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )