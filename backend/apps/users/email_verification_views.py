# PATH: apps/users/email_verification_views.py
# New file — separate from views.py for organization.
# Handles email verification by sending verification links,
# validating verification tokens, and marking the user's
# email as verified after successful confirmation.

from django.core.mail import send_mail
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from django.conf import settings

from .models import User, EmailVerification
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
        # Checks whether the verification token is still valid,
        # has not expired, and has not already been used.
        if not verification.is_valid():
            return Response({'error': 'This verification link has expired or already been used.'}, status=status.HTTP_400_BAD_REQUEST)
       
        user = verification.user
        user.email_verified = True
        user.save()

        verification.is_used = True
        verification.save()

        return Response({'message': 'Email verified successfully.'}, status=status.HTTP_200_OK)