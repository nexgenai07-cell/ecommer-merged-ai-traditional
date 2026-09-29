# PATH: apps/users/password_change_views.py
# NEW (Sep 2026 — Profile: password change with email OTP verification).
# Mirrors phone_change_views.py / email_change_views.py.
#
# Two-step flow so the password can NEVER change without the OTP:
#   1. POST /change-password/          — current_password + new_password.
#      Validates both, then emails a 6-digit code to the account's
#      registered email. The password is NOT changed at this point.
#   2. POST /change-password/confirm/  — pass the code. Only then does the
#      password actually change.
#
# The old one-step ChangePasswordView (which changed the password right
# after checking only current_password) was removed from views.py, so
# there is no way around the OTP.

import secrets
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import PasswordChangeRequest
from .serializers import ChangePasswordSerializer
from .email_service import send_password_change_code

OTP_VALID_MINUTES = 10
MAX_OTP_ATTEMPTS = 5


def generate_otp():
    # secrets (not random) — this code protects an account password.
    return f"{secrets.randbelow(1_000_000):06d}"


class ChangePasswordView(APIView):
    """
    POST /api/v1/auth/change-password/

    Request:
    { "current_password": "old", "new_password": "new" }

    Response (200):
    { "message": "string" }

    Checks the current password and the new password's strength exactly
    like before (ChangePasswordSerializer), then sends a 6-digit code to
    the account's registered email. Does NOT change the password — that
    only happens after the code is confirmed via ConfirmPasswordChangeView.
    Overwrites any still-pending request for this user — only the most
    recently requested code is ever valid.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        user = request.user
        code = generate_otp()

        PasswordChangeRequest.objects.update_or_create(
            user=user,
            defaults={
                # stored hashed — never in plain text
                "new_password_hash": make_password(serializer.validated_data["new_password"]),
                "otp_code": code,
                "otp_expires_at": timezone.now() + timedelta(minutes=OTP_VALID_MINUTES),
                "attempts": 0,
            },
        )

        sent = send_password_change_code(user, code)

        if not sent:
            PasswordChangeRequest.objects.filter(user=user).delete()
            return Response(
                {"error": "Could not send the verification email. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "message": (
                    f"A verification code has been sent to {user.email}. "
                    "Enter it to confirm your password change."
                ),
            },
            status=status.HTTP_200_OK,
        )


class ConfirmPasswordChangeView(APIView):
    """
    POST /api/v1/auth/change-password/confirm/
    Request: { "otp": "123456" }

    Only on a valid, unexpired code does the password actually change.
    After MAX_OTP_ATTEMPTS wrong codes the pending request is deleted and
    the user has to start again from /change-password/.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        otp = request.data.get("otp")

        if not otp:
            return Response(
                {"error": "otp is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        otp = str(otp).strip()

        try:
            change_request = PasswordChangeRequest.objects.get(user=user)
        except PasswordChangeRequest.DoesNotExist:
            return Response(
                {"error": "No pending password change found. Call /change-password/ first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not change_request.is_valid():
            change_request.delete()
            return Response(
                {"error": "Code has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not secrets.compare_digest(change_request.otp_code, otp):
            change_request.attempts += 1

            if change_request.attempts >= MAX_OTP_ATTEMPTS:
                change_request.delete()
                return Response(
                    {"error": "Too many wrong attempts. Please request a new code."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            change_request.save(update_fields=["attempts"])
            remaining = MAX_OTP_ATTEMPTS - change_request.attempts
            return Response(
                {"error": f"Invalid code. {remaining} attempt(s) left."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Correct code -> apply the already-hashed password.
        user.password = change_request.new_password_hash
        user.save(update_fields=["password"])

        change_request.delete()

        return Response(
            {"message": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )