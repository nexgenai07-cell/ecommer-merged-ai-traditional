# PATH: apps/users/urls.py

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .google_auth_views import GoogleLoginView
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
    RevokeSessionView,
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
    # NEW (Sep 2026 — checkout phone re-verification)
    SendPhoneVerificationView,
    VerifyPhoneView,
)

# NEW (Sep 2026 — Profile: editable email with OTP verification)
from .email_change_views import (
    RequestEmailChangeView,
    ConfirmEmailChangeView,
)

# NEW (Sep 2026 — Profile: editable phone with OTP code-entry verification)
from .phone_change_views import (
    RequestPhoneChangeView,
    ConfirmPhoneChangeView,
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
    "google/",
    GoogleLoginView.as_view(),
    name="google-login"
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

    # NEW: single-device sign-out — pick one session from the list
    # returned by GET /sessions/ and sign just that one out.
    path(
        "sessions/<int:session_id>/revoke/",
        RevokeSessionView.as_view(),
        name="revoke_session",
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

    # ==========================================================
    # NEW: Phone Re-Verification — used when a customer changes their
    # phone number away from the one verified at registration (e.g. from
    # the checkout page). Same link-based pattern as email verification.
    # ==========================================================

    path(
        "send-phone-verification/",
        SendPhoneVerificationView.as_view(),
        name="send_phone_verification",
    ),

    path(
        "verify-phone/",
        VerifyPhoneView.as_view(),
        name="verify_phone",
    ),

    # ==========================================================
    # NEW: Email Change (OTP-verified) — profile page "edit email"
    # ==========================================================

    path(
        "me/email/change/",
        RequestEmailChangeView.as_view(),
        name="me_email_change",
    ),

    path(
        "me/email/confirm/",
        ConfirmEmailChangeView.as_view(),
        name="me_email_confirm",
    ),

    # ==========================================================
    # NEW: Phone Change (OTP code-entry verified) — profile page
    # "edit phone number". Distinct from send-phone-verification/ &
    # verify-phone/ above, which are the LINK-based checkout re-verify
    # flow; this is the CODE-based flow used from the Profile page.
    # ==========================================================

    path(
        "me/phone/change/",
        RequestPhoneChangeView.as_view(),
        name="me_phone_change",
    ),

    path(
        "me/phone/confirm/",
        ConfirmPhoneChangeView.as_view(),
        name="me_phone_confirm",
    ),
]