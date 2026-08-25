# PATH: apps/users/twofactor_views.py
# This is a NEW file. Keep it separate from views.py to stay organized
# since 2FA has its own dedicated logic.
#
# Required pip installs:
#   pip install pyotp qrcode pillow
# Handles the complete Two-Factor Authentication (2FA) process,
# including enabling 2FA, verifying OTPs, disabling 2FA,
# and completing secure login using OTP verification.

from datetime import timedelta

import pyotp
import qrcode
import io
import base64
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken


from .models import TwoFactorAuth, User
from .serializers import UserProfileSerializer

# Generates a unique secret key and QR code for the user to scan
# with an authenticator app. This starts the 2FA setup process
# but does not enable 2FA until OTP verification succeeds.
class Enable2FAView(APIView):
    """
    POST /api/v1/auth/2fa/enable/

    Step 1 of 2FA setup. Generates a new TOTP secret and returns it as a
    QR code (base64 image) the user scans with Google Authenticator /
    Authy / Microsoft Authenticator etc.

    IMPORTANT: This does NOT turn on 2FA yet — is_enabled stays False
    until the user proves they scanned it correctly via Verify2FAView.
    This prevents someone from accidentally locking themselves out if
    the QR code never actually got scanned.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user

        # Generate a new secret each time enable/ is called (overwrites any
        # previous un-confirmed attempt)
        secret = pyotp.random_base32()
        TwoFactorAuth.objects.update_or_create(
            user=user,
            defaults={'secret': secret, 'is_enabled': False}
        )

        # Build the otpauth:// URI that authenticator apps understand
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name=user.email,
            issuer_name='AI Commerce Platform'
        )

        # Render as a QR code image, encode as base64 so the frontend can
        # display it directly in an <img src="data:image/png;base64,..."> tag
        qr_img = qrcode.make(provisioning_uri)
        buffer = io.BytesIO()
        qr_img.save(buffer, format='PNG')
        qr_base64 = base64.b64encode(buffer.getvalue()).decode()

        return Response({
            'message': 'Scan this QR code with your authenticator app, then call /verify/ with a code.',
            'qr_code_base64': f'data:image/png;base64,{qr_base64}',
            'manual_entry_key': secret,  # shown as fallback if user can't scan
        }, status=status.HTTP_200_OK)


# Verifies the OTP entered by the user after scanning the QR code.
# If the OTP is valid, Two-Factor Authentication is officially enabled.
class Verify2FAView(APIView):
    """
    POST /api/v1/auth/2fa/verify/
    Request: { "otp": "123456" }

    Step 2 of setup — user enters the 6-digit code currently shown in their
    authenticator app. If it matches, 2FA is officially turned on.

    This same endpoint shape is also what LOGIN would call afterwards if
    2FA is enabled (check otp as a second step after password) — that
    login-flow wiring is a TODO once this base setup is confirmed working.
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

        totp = pyotp.TOTP(two_fa.secret)
        if not totp.verify(otp, valid_window=1):  # valid_window=1 allows ±30s clock drift
            return Response({'error': 'Invalid or expired code.'}, status=status.HTTP_400_BAD_REQUEST)

        two_fa.is_enabled = True
        two_fa.save()

        return Response({'message': 'Two-factor authentication enabled successfully.'}, status=status.HTTP_200_OK)


# Disables Two-Factor Authentication after confirming
# the user's password for security.
class Disable2FAView(APIView):
    """
    POST /api/v1/auth/2fa/disable/
    Request: { "password": "string" }  — password required as confirmation,
    same security pattern as delete account.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        password = request.data.get('password')
        if not request.user.check_password(password or ''):
            return Response({'error': 'Incorrect password.'}, status=status.HTTP_400_BAD_REQUEST)

        TwoFactorAuth.objects.filter(user=request.user).delete()

        return Response({'message': 'Two-factor authentication disabled.'}, status=status.HTTP_200_OK)


# Completes the login process by verifying the OTP.
# JWT tokens are generated only after successful OTP verification.
class TwoFactorLoginVerifyView(APIView):
    """
    POST /api/v1/auth/2fa/login-verify/

    Request:
    {
        "user_id": 1,
        "otp": "123456",
        "remember_me": true
    }

    The user_id and remember_me values come from the first login step.

    Tokens are issued only after successful OTP verification.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # Imported here to avoid a circular import between
        # users/views.py and users/twofactor_views.py.
        from .views import create_session_record

        user_id = request.data.get("user_id")
        otp = request.data.get("otp")

        # Remember Me comes from the frontend during the 2FA step.
        remember_me = request.data.get(
            "remember_me",
            False
        )

        # Normalize string values in case frontend sends
        # "true" / "false" instead of JSON boolean values.
        if isinstance(remember_me, str):
            remember_me = remember_me.lower() == "true"

        if not user_id or not otp:
            return Response(
                {
                    "error": "user_id and otp are required."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(
                id=user_id,
                is_active=True,
            )
        except User.DoesNotExist:
            return Response(
                {
                    "error": "Invalid user."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            two_fa = TwoFactorAuth.objects.get(
                user=user,
                is_enabled=True,
            )
        except TwoFactorAuth.DoesNotExist:
            return Response(
                {
                    "error": (
                        "2FA is not enabled for this account."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify OTP
        totp = pyotp.TOTP(two_fa.secret)

        if not totp.verify(
            otp,
            valid_window=1
        ):
            return Response(
                {
                    "error": "Invalid or expired code."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # OTP is valid, so now issue JWT tokens.
        refresh = RefreshToken.for_user(user)

        # Remember Me controls refresh token lifetime.
        if remember_me:
            refresh.set_exp(
                lifetime=timedelta(days=30)
            )
        else:
            refresh.set_exp(
                lifetime=timedelta(days=7)
            )

        access = refresh.access_token

        tokens = {
            "access": str(access),
            "refresh": str(refresh),
        }

        # Create login session only after successful 2FA.
        create_session_record(
            request,
            user,
            refresh,
            access_token=access,
        )

        return Response(
            {
                "message": "Login successful.",
                "user": UserProfileSerializer(user).data,
                "tokens": tokens,
                "remember_me": remember_me,
            },
            status=status.HTTP_200_OK,
        )