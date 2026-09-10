# PATH: apps/users/twofactor_views.py
# Handles email-based Two-Factor Authentication (2FA): enabling,
# verifying setup, disabling, and completing login with an emailed OTP.

import random
from datetime import timedelta

from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .models import TwoFactorAuth, User
from .serializers import UserProfileSerializer
from .email_service import send_2fa_code_email


def generate_otp():
    return f"{random.randint(0, 999999):06d}"


# Step 1 of 2FA setup: generates a 6-digit code, emails it to the user,
# and stores it (unconfirmed) until the user verifies it.
class Enable2FAView(APIView):
    """
    POST /api/v1/auth/2fa/enable/

    Sends a 6-digit code to the user's email. is_enabled stays False
    until the code is confirmed via Verify2FAView — this prevents
    2FA from being turned on if the email never actually arrives.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user

        code = generate_otp()
        two_fa, _ = TwoFactorAuth.objects.update_or_create(
            user=user,
            defaults={
                'otp_code': code,
                'otp_expires_at': timezone.now() + timedelta(minutes=10),
                'is_enabled': False,
            }
        )

        send_2fa_code_email(user, code)

        return Response({
            'message': 'A verification code has been sent to your email. Enter it to enable 2FA.',
        }, status=status.HTTP_200_OK)


# Step 2 of setup: verifies the code the user received by email.
class Verify2FAView(APIView):
    """
    POST /api/v1/auth/2fa/verify/
    Request: { "otp": "123456" }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        otp = request.data.get('otp')
        if not otp:
            return Response({'error': 'otp is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            two_fa = TwoFactorAuth.objects.get(user=request.user)
        except TwoFactorAuth.DoesNotExist:
            return Response(
                {'error': 'No 2FA setup found. Call /2fa/enable/ first.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not two_fa.otp_code or two_fa.otp_code != otp:
            return Response({'error': 'Invalid code.'}, status=status.HTTP_400_BAD_REQUEST)

        if not two_fa.otp_expires_at or timezone.now() > two_fa.otp_expires_at:
            return Response({'error': 'Code has expired. Please request a new one.'}, status=status.HTTP_400_BAD_REQUEST)

        two_fa.is_enabled = True
        two_fa.otp_code = None
        two_fa.otp_expires_at = None
        two_fa.save()

        return Response({'message': 'Two-factor authentication enabled successfully.'}, status=status.HTTP_200_OK)


# Disables 2FA after confirming the user's password.
class Disable2FAView(APIView):
    """
    POST /api/v1/auth/2fa/disable/
    Request: { "password": "string" }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        password = request.data.get('password')
        if not request.user.check_password(password or ''):
            return Response({'error': 'Incorrect password.'}, status=status.HTTP_400_BAD_REQUEST)

        TwoFactorAuth.objects.filter(user=request.user).delete()

        return Response({'message': 'Two-factor authentication disabled.'}, status=status.HTTP_200_OK)


# Completes login by verifying the OTP that was emailed during the
# login attempt (see the 2FA branch in views.LoginView).
class TwoFactorLoginVerifyView(APIView):
    """
    POST /api/v1/auth/2fa/login-verify/

    Request:
    {
        "user_id": 1,
        "otp": "123456",
        "remember_me": true
    }
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from .views import create_session_record

        user_id = request.data.get("user_id")
        otp = request.data.get("otp")

        remember_me = request.data.get("remember_me", False)
        if isinstance(remember_me, str):
            remember_me = remember_me.lower() == "true"

        if not user_id or not otp:
            return Response(
                {"error": "user_id and otp are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            return Response({"error": "Invalid user."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            two_fa = TwoFactorAuth.objects.get(user=user, is_enabled=True)
        except TwoFactorAuth.DoesNotExist:
            return Response(
                {"error": "2FA is not enabled for this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not two_fa.otp_code or two_fa.otp_code != otp:
            return Response({"error": "Invalid code."}, status=status.HTTP_400_BAD_REQUEST)

        if not two_fa.otp_expires_at or timezone.now() > two_fa.otp_expires_at:
            return Response({"error": "Code has expired. Please try logging in again."}, status=status.HTTP_400_BAD_REQUEST)

        # Clear the used code so it can't be replayed
        two_fa.otp_code = None
        two_fa.otp_expires_at = None
        two_fa.save()

        refresh = RefreshToken.for_user(user)
        if remember_me:
            refresh.set_exp(lifetime=timedelta(days=30))
        else:
            refresh.set_exp(lifetime=timedelta(days=7))

        access = refresh.access_token

        create_session_record(request, user, refresh, access_token=access)

        return Response(
            {
                "message": "Login successful.",
                "user": UserProfileSerializer(user).data,
                "tokens": {"access": str(access), "refresh": str(refresh)},
                "remember_me": remember_me,
            },
            status=status.HTTP_200_OK,
        )