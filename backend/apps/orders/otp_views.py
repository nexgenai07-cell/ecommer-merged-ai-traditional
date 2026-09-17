# PATH: apps/orders/otp_views.py
#
# NEW (Sep 2026 — Checkout OTP verification): a customer must request and
# verify a 6-digit code emailed to their account address before they are
# allowed to place an order. CheckoutView (views.py) checks
# CheckoutOTP.is_verification_usable() and blocks order creation if it
# isn't usable — that's the actual enforcement point. These two views are
# only how the customer requests + verifies that code.
#
# FIX (Bug report, Sep 2026): verification is a ONE-TIME thing per
# account now, not per order. SendCheckoutOTPView below now short-circuits
# (no email sent) if this user already has a permanently-usable
# verification, so the frontend can call it as normal on every checkout
# and simply get told "already verified" instead of a fresh code.
#
# SMS/phone delivery is a deliberate follow-up, not covered here — email
# is the only channel wired up for now, same pattern as the existing
# email-OTP flows in apps/users (2FA login code, email-change code).

import logging
import random
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.permissions import IsCustomer

from .models import CheckoutOTP

logger = logging.getLogger(__name__)


def generate_otp():
    return f"{random.randint(0, 999999):06d}"


def send_checkout_otp_email(email, code):
    """
    Emails the 6-digit checkout verification code. Same 6-digit /
    plain-text-plus-html pattern as send_2fa_code_email /
    send_email_change_code in apps/users/email_service.py.
    """
    subject = "Your order verification code"
    message = (
        f"Your order verification code is: {code}\n\n"
        f"Enter this code to confirm your order. This code is valid for "
        f"{CheckoutOTP.OTP_VALIDITY_MINUTES} minutes. If you did not "
        "request this, you can safely ignore this email."
    )
    html_message = f"""
        <html>
            <body>
                <h2>Your order verification code</h2>
                <p>Enter this code to confirm and place your order:</p>
                <h1 style="letter-spacing: 4px;">{code}</h1>
                <p>This code is valid for {CheckoutOTP.OTP_VALIDITY_MINUTES} minutes.</p>
                <p>If you did not request this, you can safely ignore this email.</p>
            </body>
        </html>
    """

    try:
        email_msg = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
        )
        email_msg.attach_alternative(html_message, "text/html")
        email_msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("send_checkout_otp_email: failed to send code to %s", email)
        return False


class SendCheckoutOTPView(APIView):
    """
    POST /api/v1/orders/checkout/send-otp/

    Sends (or resends) a 6-digit verification code to the logged-in
    customer's account email. Call this before /checkout/verify-otp/ and
    before POSTing to /checkout/ itself.

    FIX (Bug report, Sep 2026): verification is permanent per account —
    if this user already has a usable (previously verified) row, no new
    email is sent; the response just confirms they're already verified
    so the frontend can skip straight to placing the order.
    """
    permission_classes = [permissions.IsAuthenticated, IsCustomer]

    def post(self, request):
        user = request.user

        if not user.email:
            return Response(
                {"error": "Your account has no email on file to send a code to."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = CheckoutOTP.objects.filter(user=user).first()

        # FIX (Bug report, Sep 2026): already permanently verified —
        # nothing to send, nothing to re-verify.
        if existing and existing.is_verification_usable():
            return Response(
                {
                    "message": "Your account is already verified.",
                    "already_verified": True,
                },
                status=status.HTTP_200_OK,
            )

        # Resend cooldown — stop this endpoint being hammered.
        if existing and existing.updated_at:
            elapsed = (timezone.now() - existing.updated_at).total_seconds()
            remaining = CheckoutOTP.RESEND_COOLDOWN_SECONDS - elapsed
            if remaining > 0:
                return Response(
                    {
                        "error": (
                            "Please wait a moment before requesting another code."
                        ),
                        "retry_after_seconds": int(remaining) + 1,
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

        code = generate_otp()

        checkout_otp, _ = CheckoutOTP.objects.update_or_create(
            user=user,
            defaults={
                "email": user.email,
                "otp_code": code,
                "otp_expires_at": timezone.now() + timedelta(
                    minutes=CheckoutOTP.OTP_VALIDITY_MINUTES
                ),
                "is_verified": False,
                "verified_at": None,
                "consumed_at": None,
            },
        )

        sent = send_checkout_otp_email(user.email, code)
        if not sent:
            return Response(
                {"error": "Could not send the verification email. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Mask the email a bit for the response (e.g. ab***@gmail.com).
        local, _, domain = user.email.partition("@")
        masked = (local[:2] + "***") if len(local) > 2 else "***"
        masked_email = f"{masked}@{domain}" if domain else user.email

        return Response(
            {
                "message": f"A verification code has been sent to {masked_email}.",
                "expires_in_minutes": CheckoutOTP.OTP_VALIDITY_MINUTES,
            },
            status=status.HTTP_200_OK,
        )


class VerifyCheckoutOTPView(APIView):
    """
    POST /api/v1/orders/checkout/verify-otp/
    Request: { "otp": "123456" }

    Marks the pending code as verified. The order itself is still only
    created by POST /orders/checkout/ — that endpoint checks
    CheckoutOTP.is_verification_usable() and 400s if this step was
    skipped.

    FIX (Bug report, Sep 2026): this verification is permanent — once
    successful, this customer never has to send/verify a code again for
    any future order on this account.
    """
    permission_classes = [permissions.IsAuthenticated, IsCustomer]

    def post(self, request):
        otp = request.data.get("otp")
        if not otp:
            return Response(
                {"error": "otp is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            checkout_otp = CheckoutOTP.objects.get(user=request.user)
        except CheckoutOTP.DoesNotExist:
            return Response(
                {"error": "No verification code was requested. Call /checkout/send-otp/ first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not checkout_otp.otp_expires_at or timezone.now() > checkout_otp.otp_expires_at:
            return Response(
                {"error": "Code has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not checkout_otp.otp_code or checkout_otp.otp_code != otp:
            return Response(
                {"error": "Invalid code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        checkout_otp.is_verified = True
        checkout_otp.verified_at = timezone.now()
        # Clear the code itself so it can't be replayed/guessed again —
        # is_verified + verified_at is all CheckoutView needs afterwards.
        checkout_otp.otp_code = None
        checkout_otp.otp_expires_at = None
        checkout_otp.save(
            update_fields=["is_verified", "verified_at", "otp_code", "otp_expires_at", "updated_at"]
        )

        return Response(
            {
                "message": (
                    "Phone/email verified. You can now place your order, "
                    "and every order after this one — no need to verify "
                    "again."
                ),
                "already_verified": True,
            },
            status=status.HTTP_200_OK,
        )