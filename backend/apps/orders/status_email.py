# PATH: apps/orders/status_email.py
#
# NEW (Sep 2026 — order status update emails): sends the customer an
# email every time their order's status changes, alongside the existing
# in-app create_notification() call. Deliberately self-contained (does
# NOT import anything from apps.notifications) so it works regardless of
# what that app's utils.py looks like — same EmailMultiAlternatives
# pattern already used for the checkout OTP email (see
# apps/orders/otp_views.py) and the email-verification / 2FA emails in
# apps/users/email_service.py.
#
# NOT called from every create_notification() site — only the ones that
# fire because an order's STATUS changed and are addressed to the
# customer themselves (not store-admin notifications, and not the very
# first "Order placed" notification at checkout, which already has its
# own dedicated, more detailed confirmation email sent separately — see
# CheckoutView in views.py).

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)


def send_order_status_email(order, title, message):
    """
    Fire-and-forget order-status email to the customer's account email.
    Mirrors the title/message already used for the matching in-app
    notification, so the two channels always say the same thing.

    Never raises — a failed/misconfigured email must never break the
    request that triggered it, same reasoning create_notification()
    itself already follows throughout this codebase. Returns True/False
    so callers can log a failure if they want to, but none are required
    to check it.
    """
    try:
        email = order.customer.user.email
    except AttributeError:
        email = None

    if not email:
        return False

    subject = f"Order {order.order_number}: {title}"
    html_message = f"""
        <html>
            <body>
                <h2>{title}</h2>
                <p>{message}</p>
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
        logger.exception(
            "send_order_status_email: failed to send status email for order %s",
            getattr(order, "order_number", "?"),
        )
        return False