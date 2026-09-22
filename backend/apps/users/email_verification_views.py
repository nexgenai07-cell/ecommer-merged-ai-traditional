# PATH: apps/users/email_verification_views.py
# New file — separate from views.py for organization.
# Handles email verification by sending verification links,
# validating verification tokens, and marking the user's
# email as verified after successful confirmation.

import re

from django.core.mail import send_mail
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from django.conf import settings

from .models import User, EmailVerification, PhoneVerification
from .email_service import send_verification_with_resend
# Generates a verification token, creates a verification link,
# sends it to the user's email, and allows users to request
# a new verification email if needed.
class SendVerificationEmailView(APIView):
    """
    POST /api/v1/auth/send-verification-email/

    Authentication is NOT required.

    Request body:
    {
        "email": "user@example.com"
    }
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get("email")

        if not email:
            return Response(
                {
                    "error": "Email is required."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {
                    "error": "No account found with this email."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if user.email_verified:
            return Response(
                {
                    "message": "Email is already verified."
                },
                status=status.HTTP_200_OK,
            )

        verification = EmailVerification.create_for_user(user)

        verify_link = (
            f"{settings.FRONTEND_URL}/verify-email/"
            f"{verification.token}/"
        )

        try:
            send_verification_with_resend(
                user.email,
                verify_link,
            )
        except Exception as exc:
            print("Resend verification email failed:", exc)

            return Response(
                {
                    "error": "Unable to send verification email."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "message": "Verification email has been sent."
            },
            status=status.HTTP_200_OK,
        )
# Verifies the email by checking the token received in the
# verification link and activates the user's email if valid.
class VerifyEmailView(APIView):
    """
    GET /api/v1/auth/verify-email/?token=xxx

    No authentication required — the token itself IS the proof of identity
    here (it was emailed to the user's own inbox). This matches the
    password-reset-confirm pattern already used elsewhere in this project.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        token = request.query_params.get('token')
        if not token:
            return Response({'error': 'token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            verification = EmailVerification.objects.get(token=token)
        except EmailVerification.DoesNotExist:
            return Response({'error': 'Invalid verification link.'}, status=status.HTTP_400_BAD_REQUEST)

        # UPDATED (v4.0): made this endpoint idempotent. Some frontends
        # (e.g. React StrictMode in development) call this API twice on
        # the same page load — the first call succeeds and marks the
        # token used, the second then hit the old "expired or already
        # used" error even though the account was actually verified
        # successfully by the first call. If the token is already used
        # but its owner is already verified, that's not a real failure —
        # it's a harmless duplicate call, so we return the same success
        # response instead of an error.
        if verification.is_used and verification.user.email_verified:
            # NEW (Sep 2026 — combined verification): make sure phone_verified
            # is also set here for a duplicate call, in case the phone
            # somehow never got marked on the first (successful) call.
            user = verification.user
            if user.phone and not user.phone_verified:
                user.phone_verified = True
                user.save(update_fields=['phone_verified'])
            return Response({'message': 'Email and phone number verified successfully.'}, status=status.HTTP_200_OK)

        # Checks whether the verification token is still valid,
        # has not expired, and has not already been used.
        if not verification.is_valid():
            return Response({'error': 'This verification link has expired or already been used.'}, status=status.HTTP_400_BAD_REQUEST)
       
        user = verification.user
        user.email_verified = True

        # NEW (Sep 2026 — combined email+phone verification): this single
        # link now verifies BOTH the email and whatever phone number was on
        # the account at the time it was sent — there's no separate phone
        # link at registration. If the account has no phone on file at all,
        # phone_verified simply stays False (nothing to verify yet).
        if user.phone:
            user.phone_verified = True

        user.save()

        verification.is_used = True
        verification.save()

        return Response({'message': 'Email and phone number verified successfully.'}, status=status.HTTP_200_OK)


# NEW (Sep 2026 — checkout phone re-verification): sends a fresh
# verification LINK when a logged-in customer wants to use a phone number
# different from the one already verified on their account (e.g. edited on
# the checkout page). No SMS gateway exists yet, so — same as every other
# link/OTP flow in this project — the link goes to the account's EMAIL
# address, not an SMS to the new number.
class SendPhoneVerificationView(APIView):
    """
    POST /api/v1/auth/send-phone-verification/

    Authentication required (the logged-in customer changing their number).

    Request body:
    {
        "phone": "03001234567"
    }
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        phone = (request.data.get("phone") or "").strip()

        if not phone:
            return Response(
                {"error": "phone is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Same validation rules as RegisterSerializer.validate_phone /
        # CheckoutSerializer.validate_phone.
        digits = phone[1:] if phone.startswith('+') else phone
        if not digits or not digits.isdigit():
            return Response(
                {"error": "Phone number must contain digits only, optionally starting with '+'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(digits) < 10:
            return Response(
                {"error": "Phone number must be at least 10 digits."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(digits) > 15:
            return Response(
                {"error": "Phone number must not exceed 15 digits."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user

        # Already this exact number, and already verified — nothing to do.
        new_digits = re.sub(r'\D', '', phone)
        current_digits = re.sub(r'\D', '', user.phone or '')
        if user.phone_verified and new_digits == current_digits:
            return Response(
                {
                    "message": "This phone number is already verified.",
                    "already_verified": True,
                },
                status=status.HTTP_200_OK,
            )

        verification = PhoneVerification.create_for_user(user, phone)

        verify_link = (
            f"{settings.FRONTEND_URL}/verify-phone/"
            f"{verification.token}/"
        )

        try:
            send_verification_with_resend(
                user.email,
                verify_link,
                subject="Verify your phone number",
                message=(
                    "A new phone number was added to your account. "
                    "Please verify it by clicking the link below:\n\n"
                    f"{verify_link}\n\n"
                    "This verification link is valid for 24 hours."
                ),
                button_text="Verify My Phone Number",
                html_intro="Click the button below to verify your new phone number.",
            )
        except Exception as exc:
            print("Phone verification email failed:", exc)

            return Response(
                {"error": "Unable to send verification email."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "message": (
                    "A verification link has been sent to your email to "
                    "confirm this phone number."
                )
            },
            status=status.HTTP_200_OK,
        )


# NEW (Sep 2026 — checkout phone re-verification): confirms the link sent
# by SendPhoneVerificationView above.
class VerifyPhoneView(APIView):
    """
    GET /api/v1/auth/verify-phone/?token=xxx

    No authentication required — same pattern as VerifyEmailView, the
    token itself (emailed to the account's own inbox) is the proof of
    identity.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        token = request.query_params.get('token')
        if not token:
            return Response({'error': 'token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            verification = PhoneVerification.objects.get(token=token)
        except PhoneVerification.DoesNotExist:
            return Response({'error': 'Invalid verification link.'}, status=status.HTTP_400_BAD_REQUEST)

        user = verification.user

        # Idempotent, same reasoning as VerifyEmailView above.
        if verification.is_used and user.phone == verification.phone and user.phone_verified:
            return Response({'message': 'Phone number verified successfully.'}, status=status.HTTP_200_OK)

        if not verification.is_valid():
            return Response({'error': 'This verification link has expired or already been used.'}, status=status.HTTP_400_BAD_REQUEST)

        user.phone = verification.phone
        user.phone_verified = True
        user.save()

        verification.is_used = True
        verification.save()

        return Response({'message': 'Phone number verified successfully.'}, status=status.HTTP_200_OK)