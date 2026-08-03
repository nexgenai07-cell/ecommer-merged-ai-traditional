# PATH: apps/users/email_verification_views.py
# New file — separate from views.py for organization.
# Handles email verification by sending verification links,
# validating verification tokens, and marking the user's
# email as verified after successful confirmation.

from unittest import result

from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from django.core.mail import send_mail
from django.conf import settings

from .models import EmailVerification

# Generates a verification token, creates a verification link,
# sends it to the user's email, and allows users to request
# a new verification email if needed.
class SendVerificationEmailView(APIView):
    """
    POST /api/v1/auth/send-verification-email/

    Sends a verification link to the logged-in user's email. Can be called:
      - Automatically right after registration (see note in RegisterView below)
      - Manually if the user wants to resend (e.g. "Resend email" button)
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        
        if user.email_verified:
            return Response({'message': 'Email is already verified.'}, status=status.HTTP_200_OK)
        # Creates a unique verification token with an expiry time
        # and stores it in the EmailVerification table.
        verification = EmailVerification.create_for_user(user)

        # FIX: was hardcoded to 'http://localhost:5173/...' — now reads
        
        verify_link = f'{settings.FRONTEND_URL}/verify-email/{verification.token}/'
        # Sends the verification email containing the verification link
        # to the user's registered email address.
        #send_mail(
          #  subject='Verify your email address',
           # message=f'Click the link to verify your email: {verify_link}\n\nThis link is valid for 24 hours.',
            #from_email=settings.DEFAULT_FROM_EMAIL,
            #recipient_list=[user.email],
            #fail_silently=True,
        #)
        
        result = send_mail(
    subject="Verify your email address",
    message=(
        f"Click the link to verify your email:\n\n"
        f"{verify_link}\n\n"
        "This link is valid for 24 hours."
    ),
    from_email=settings.DEFAULT_FROM_EMAIL,
    recipient_list=[user.email],
    fail_silently=False,
)

        print("Verification email result:", result)
        return Response(
            {'message': 'Verification email has been sent.'},
            status=status.HTTP_200_OK
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