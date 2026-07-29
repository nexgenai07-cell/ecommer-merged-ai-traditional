# PATH: apps/users/urls.py
# This REPLACES your entire urls.py — has everything: original + sessions +
# change password + delete account + 2FA + email verification
# Defines all authentication, account management, session,
# 2FA, and email verification API endpoints.
# Maps each URL to its corresponding View.
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    ReactivateAccountView,
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
)
from .twofactor_views import Enable2FAView, Verify2FAView, Disable2FAView, TwoFactorLoginVerifyView
from .email_verification_views import SendVerificationEmailView, VerifyEmailView

urlpatterns = [
    # Core auth (Module 1)
    path('register/', RegisterView.as_view(), name='register'),
    # Calls RegisterView when a new user creates an account.
    path('login/', LoginView.as_view(), name='login'),
    # Authenticates user and returns JWT access & refresh tokens.
    path('logout/', LogoutView.as_view(), name='logout'),
    # Logs out the current session by blacklisting the refresh token.
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    # Generates a new access token using a valid refresh token.
    path('password-reset/', PasswordResetRequestView.as_view(), name='password_reset'),
    # Starts password reset process by sending a reset email.
    path('password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('me/', MeView.as_view(), name='me'),
    # Verifies reset token and saves the user's new password.
    path('me/update/', MeView.as_view(), name='me_update'),
    # Returns the logged-in user's profile.
    #reactive account
    path( "reactivate-account/", ReactivateAccountView.as_view(), name="reactivate-account"), 
    # Account security (this document)
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    # Changes the current user's password after verifying the old password.
    path('me/delete/', DeleteAccountView.as_view(), name='delete_account'),
    # Deletes or deactivates the user's own account after password confirmation.
    path('sessions/', SessionListView.as_view(), name='session_list'),
    # Returns all active login sessions for the current user.
    path('sessions/revoke-all/', RevokeAllSessionsView.as_view(), name='revoke_all_sessions'),
    # Logs the user out from all devices except the current session.
    
    
    
    # Two-Factor Authentication
    path('2fa/enable/', Enable2FAView.as_view(), name='2fa_enable'),
    # Generates QR code and secret key for enabling Two-Factor Authentication.
    path('2fa/verify/', Verify2FAView.as_view(), name='2fa_verify'),
    # Verifies the OTP entered after scanning the QR code.
    path('2fa/disable/', Disable2FAView.as_view(), name='2fa_disable'),
    # Disables Two-Factor Authentication for the user.
    
    
    # FIX (NEW): completes the login flow when 2FA is enabled — see
    # TwoFactorLoginVerifyView docstring for the full flow explanation.
    path('2fa/login-verify/', TwoFactorLoginVerifyView.as_view(), name='2fa_login_verify'),
    # Completes login after validating the user's OTP code.



    # Email Verification
    path('send-verification-email/', SendVerificationEmailView.as_view(), name='send_verification_email'),
    # Sends a verification email to confirm the user's email address.
    path('verify-email/', VerifyEmailView.as_view(), name='verify_email'),
    # Verifies the email using the token received in the email link.
]