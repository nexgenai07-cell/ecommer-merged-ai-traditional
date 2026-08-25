from datetime import timedelta

from firebase_admin import auth as firebase_auth

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User
from .serializers import GoogleLoginSerializer, UserProfileSerializer
from .views import create_session_record


class GoogleLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        id_token = serializer.validated_data["id_token"]

        # Verify Firebase ID token
        try:
            decoded_token = firebase_auth.verify_id_token(id_token)
        except Exception:
            return Response(
                {
                    "error": "Invalid Firebase ID token."
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        firebase_uid = decoded_token.get("uid")
        email = decoded_token.get("email")
        name = decoded_token.get("name", "")

        if not email:
            return Response(
                {
                    "error": "Google account does not contain an email."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

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

        # Don't allow deleted/deactivated accounts
        if user.is_delete or not user.is_active:
            return Response(
                {
                    "account_deactivated": True,
                    "email": user.email,
                    "message": (
                        "This account has been deleted or deactivated."
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Google has already verified the user's email
        if not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified"])

        # Generate JWT
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