from datetime import timedelta

from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from google.auth.exceptions import TransportError
import requests as http_requests

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
        #
        # Two very different failure modes need two different HTTP status
        # codes:
        #   1. The token itself is bad (expired, wrong signature, wrong
        #      audience) — this is the CLIENT's fault → 401 Unauthorized.
        #   2. We couldn't even reach Google's servers to check (network
        #      issue, DNS failure, Google's cert endpoint down/unreachable
        #      from Railway) — this is NOT the client's fault, the token
        #      might be perfectly valid → 503 Service Unavailable, so the
        #      frontend knows to show "try again" instead of "log in again".
        try:
            decoded_token = google_id_token.verify_oauth2_token(
                id_token,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        except ValueError:
            # google-auth raises ValueError for anything token-related:
            # bad signature, expired, wrong audience, malformed token.
            return Response(
                {
                    "error": "Invalid Google ID token."
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except (TransportError, http_requests.exceptions.RequestException):
            # Could not reach Google's servers at all to verify the token.
            return Response(
                {
                    "error": "Google sign-in is temporarily unavailable."
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
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

        if not decoded_token.get("email_verified", False):
            return Response(
                {
                    "error": "Google account email is not verified."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = email.lower().strip()

        user = User.objects.filter(email=email).first()

        if not user:
            user = User.objects.create_user(
                email=email,
                name=name or email.split("@")[0],
                password=None,
                role="customer",
            )

        if not user.is_active:
            return Response(
                {
                    "account_deactivated": True,
                    "email": user.email,
                    "message": "This account is deactivated.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified"])

        refresh = RefreshToken.for_user(user)
        refresh.set_exp(lifetime=timedelta(days=30))
        access = refresh.access_token

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