# PATH: apps/users/urls.py

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    RegisterView,
    LoginView,
    LogoutView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    MeView,
    ChangePasswordView,
    DeleteAccountView,
    SessionListView,
    RevokeAllSessionsView,
    # NEW
    ReactivateRequestView,
    ReactivateConfirmView,
)

from .twofactor_views import (
    Enable2FAView,
    Verify2FAView,
    Disable2FAView,
    TwoFactorLoginVerifyView,
)

from .email_verification_views import (
    SendVerificationEmailView,
    VerifyEmailView,
)

urlpatterns = [

    # ==========================================================
    # Core Authentication
    # ==========================================================

    path(
        "register/",
        RegisterView.as_view(),
        name="register",
    ),

    path(
        "login/",
        LoginView.as_view(),
        name="login",
    ),
    

    path(
        "logout/",
        LogoutView.as_view(),
        name="logout",
    ),

    path(
        "token/refresh/",
        TokenRefreshView.as_view(),
        name="token_refresh",
    ),

    # ==========================================================
    # Password Reset
    # ==========================================================

    path(
        "password-reset/",
        PasswordResetRequestView.as_view(),
        name="password_reset",
    ),

    path(
        "password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),

    # ==========================================================
    # Profile
    # ==========================================================

    path(
        "me/",
        MeView.as_view(),
        name="me",
    ),

    path(
        "me/update/",
        MeView.as_view(),
        name="me_update",
    ),

    # ==========================================================
    # Account Security
    # ==========================================================

    path(
        "change-password/",
        ChangePasswordView.as_view(),
        name="change_password",
    ),

    path(
        "me/delete/",
        DeleteAccountView.as_view(),
        name="delete_account",
    ),

    path(
        "sessions/",
        SessionListView.as_view(),
        name="session_list",
    ),

    path(
        "sessions/revoke-all/",
        RevokeAllSessionsView.as_view(),
        name="revoke_all_sessions",
    ),

    # ==========================================================
    # NEW Account Reactivation APIs
    # ==========================================================

    path(
        "reactivate/request/",
        ReactivateRequestView.as_view(),
        name="reactivate_request",
    ),

    path(
        "reactivate/confirm/",
        ReactivateConfirmView.as_view(),
        name="reactivate_confirm",
    ),

    # ==========================================================
    # Two Factor Authentication
    # ==========================================================

    path(
        "2fa/enable/",
        Enable2FAView.as_view(),
        name="2fa_enable",
    ),

    path(
        "2fa/verify/",
        Verify2FAView.as_view(),
        name="2fa_verify",
    ),

    path(
        "2fa/disable/",
        Disable2FAView.as_view(),
        name="2fa_disable",
    ),

    path(
        "2fa/login-verify/",
        TwoFactorLoginVerifyView.as_view(),
        name="2fa_login_verify",
    ),

    # ==========================================================
    # Email Verification
    # ==========================================================

    path(
        "send-verification-email/",
        SendVerificationEmailView.as_view(),
        name="send_verification_email",
    ),

    path(
        "verify-email/",
        VerifyEmailView.as_view(),
        name="verify_email",
    ),
]