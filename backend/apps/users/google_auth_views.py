from datetime import timedelta

from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework_simplejwt.tokens import RefreshToken
from django.conf import settings

from .models import User
from .serializers import GoogleLoginSerializer, UserProfileSerializer
from .views import create_session_record
from apps.cart.views import merge_guest_cart_into_user_cart


class GoogleLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        id_token = serializer.validated_data["id_token"]

        # Verify Google ID token directly with Google's servers.
        # google.oauth2.id_token.verify_oauth2_token checks the token's
        # signature, expiry, and that the "aud" (audience) claim matches
        # our own GOOGLE_CLIENT_ID — this last check is what stops someone
        # from replaying an ID token that Google issued for a *different*
        # app.
        try:
            decoded_token = google_id_token.verify_oauth2_token(
                id_token,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        except ValueError:
            return Response(
                {
                    "error": "Invalid Google ID token."
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        email = decoded_token.get("email")
        name = decoded_token.get("name", "")

        if not email:
            return Response(
                {
                    "error": "Google account does not contain an email."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Google's own flag for whether this email has actually been
        # verified on Google's side — extra safety check.
        if not decoded_token.get("email_verified", False):
            return Response(
                {
                    "error": "Google account email is not verified."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = email.lower().strip()

        # Find existing account
        user = User.objects.filter(email=email).first()

        # Create customer account if it doesn't exist
        if not user:
            user = User.objects.create_user(
                email=email,
                name=name or email.split("@")[0],
                password=None,
                role="customer",
            )

        # Don't allow deactivated accounts
        if not user.is_active:
            return Response(
                {
                    "account_deactivated": True,
                    "email": user.email,
                    "message": "This account is deactivated.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Google-authenticated email is trusted as verified
        if not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified"])

        # Generate JWT tokens
        refresh = RefreshToken.for_user(user)

        # Google login behaves like Remember Me = True
        refresh.set_exp(
            lifetime=timedelta(days=30)
        )

        access = refresh.access_token

        # Create login session
        create_session_record(
            request,
            user,
            refresh,
            access_token=access,
        )
        merge_guest_cart_into_user_cart(request, user)

        return Response(
            {
                "message": "Google login successful.",
                "user": UserProfileSerializer(user).data,
                "tokens": {
                    "access": str(access),
                    "refresh": str(refresh),
                },
                "remember_me": True,
            },
            status=status.HTTP_200_OK,
        )