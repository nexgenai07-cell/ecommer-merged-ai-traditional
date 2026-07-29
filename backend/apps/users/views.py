# PATH: apps/users/views.py

from rest_framework import status, generics, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from django.contrib.auth.tokens import default_token_generator
from django.utils.http import (
    urlsafe_base64_encode,
    urlsafe_base64_decode,
)
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.conf import settings

from user_agents import parse as parse_user_agent

from .models import (
    User,
    UserSession,
    EmailVerification,
    TwoFactorAuth,
)

from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    UserProfileSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    ChangePasswordSerializer,
    DeleteAccountSerializer,
    UserSessionSerializer,
)

# Generates JWT access and refresh tokens for an authenticated user.
def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }

# Retrieves the client's IP address from the incoming request.
def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")

# Stores information about the user's current login session,
# including device, browser, IP address, and JWT token IDs.
def create_session_record(request, user, refresh_token, access_token=None):
    ua_string = request.META.get("HTTP_USER_AGENT", "")
    ua = parse_user_agent(ua_string)

    device = (
        ua.device.family
        if ua.device.family != "Other"
        else f"{ua.os.family} Device"
    )

    browser = f"{ua.browser.family} {ua.browser.version_string}"

    UserSession.objects.create(
        user=user,
        refresh_jti=str(refresh_token["jti"]),
        access_jti=(
            str(access_token["jti"])
            if access_token is not None
            else ""
        ),
        device=device,
        browser=browser,
        ip_address=get_client_ip(request),
        location="Unknown",
    )

# Generates an email verification token and sends
# a verification link to the user's email.
def send_verification_email(user):
    verification = EmailVerification.create_for_user(user)

    verify_link = (
        f"{settings.FRONTEND_URL}/verify-email/"
        f"{verification.token}/"
    )

    send_mail(
        subject="Verify your email address",
        message=(
            f"Click the link to verify your email:\n\n"
            f"{verify_link}\n\n"
            "This link is valid for 24 hours."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )

# Handles new user registration and sends an email
# verification link after creating the account.
class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.save()
        tokens = get_tokens_for_user(user)

        try:
            send_verification_email(user)
        except Exception:
            pass

        return Response(
            {
                "message": (
                    "Registration successful. "
                    "Please check your email to verify your account."
                ),
                "user": UserProfileSerializer(user).data,
                "tokens": tokens,
            },
            status=status.HTTP_201_CREATED,
        )

# Authenticates users, verifies email status,
# checks two-factor authentication, and returns JWT tokens.
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]

# Do not allow login until email is verified
        if not user.email_verified:
           return Response(
        {
            "email_not_verified": True,
            "email": user.email,
            "message": "Please verify your email before logging in."
        },
        status=status.HTTP_403_FORBIDDEN,
    )

# NEW: Do not allow login if account is soft deleted
        if user.is_delete:
            return Response(
        {
            "account_deactivated": True,
            "email": user.email,
            "message": "This account has been deleted. Would you like to reactivate it?"
        },
        status=status.HTTP_403_FORBIDDEN,
    )

        two_fa = TwoFactorAuth.objects.filter(
                 user=user,
                 is_enabled=True,
        ).first()

        if two_fa:
            return Response(
                {
                    "message": "Two-factor authentication code required.",
                    "require_2fa": True,
                    "user_id": user.id,
                },
                status=status.HTTP_200_OK,
            )

        refresh = RefreshToken.for_user(user)
        access = refresh.access_token

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
                "tokens": {
                    "access": str(access),
                    "refresh": str(refresh),
                },
            },
            status=status.HTTP_200_OK,
        )
# Logs out the current user by blacklisting
# the provided refresh token.
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")

        if not refresh_token:
            return Response(
                {"error": "Refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return Response(
                {"error": "Invalid or expired refresh token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"message": "Logged out successfully."},
            status=status.HTTP_200_OK,
        )

# Sends a password reset link to the user's
# registered email address.
class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        user = User.objects.get(email=email)

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        reset_link = (
            f"{settings.FRONTEND_URL}/reset-password/"
            f"{uid}/{token}/"
        )

        send_mail(
            subject="Password Reset Request",
            message=(
                f"Click the link to reset your password:\n\n"
                f"{reset_link}\n\n"
                "This link is valid for 24 hours."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=True,
        )

        return Response(
            {
                "message": (
                    "Password reset link has been sent to your email."
                )
            },
            status=status.HTTP_200_OK,
        )

# Verifies the password reset token and
# updates the user's password.
class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uid = serializer.validated_data["uid"]
        token = serializer.validated_data["token"]
        new_password = serializer.validated_data["new_password"]

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except (
            TypeError,
            ValueError,
            OverflowError,
            User.DoesNotExist,
        ):
            return Response(
                {"error": "Invalid reset link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, token):
            return Response(
                {
                    "error": (
                        "Reset link is invalid or has expired."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save()

        return Response(
            {"message": "Password has been reset successfully."},
            status=status.HTTP_200_OK,
        )

# Returns and updates the authenticated user's profile.
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user
    
# Allows authenticated users to securely
# change their password.
class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save()

        return Response(
            {"message": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )

# Soft deletes the user account and revokes
# all active JWT sessions.
class DeleteAccountView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request):
        serializer = DeleteAccountSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.is_delete = True
        user.is_active = False
        user.save(update_fields=["is_delete", "is_active"])

        from rest_framework_simplejwt.token_blacklist.models import (
            OutstandingToken,
            BlacklistedToken,
        )

        tokens = OutstandingToken.objects.filter(user=user)

        for token in tokens:
            BlacklistedToken.objects.get_or_create(token=token)

        return Response(
            {"message": "Account deleted successfully."},
            status=status.HTTP_200_OK,
        )
        
# Returns all active login sessions
# belonging to the authenticated user.
class SessionListView(generics.ListAPIView):
    serializer_class = UserSessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return UserSession.objects.filter(user=self.request.user)

# Signs the user out from every device by
# blacklisting all tokens and removing session records.
class RevokeAllSessionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from rest_framework_simplejwt.token_blacklist.models import (
            OutstandingToken,
            BlacklistedToken,
        )

        user = request.user

        tokens = OutstandingToken.objects.filter(user=user)

        for token in tokens:
            BlacklistedToken.objects.get_or_create(token=token)

        UserSession.objects.filter(user=user).delete()

        return Response(
            {"message": "All sessions have been signed out."},
            status=status.HTTP_200_OK,
        )
        
class ReactivateAccountView(APIView):

    permission_classes = [
        permissions.AllowAny
    ]

    def post(self, request):

        email = request.data.get("email")

        if not email:
            return Response(
                {
                    "message": "Email is required."
                },
                status=status.HTTP_400_BAD_REQUEST
            )


        user = User.objects.filter(
            email=email,
            is_delete=True
        ).first()


        if not user:
            return Response(
                {
                    "message": "Deleted account not found."
                },
                status=status.HTTP_404_NOT_FOUND
            )


        user.is_delete = False
        user.is_active = True

        user.save(
            update_fields=[
                "is_delete",
                "is_active"
            ]
        )


        return Response(
            {
                "message": "Account reactivated successfully.",
                "email": user.email
            },
            status=status.HTTP_200_OK
        )