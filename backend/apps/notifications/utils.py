import logging

from django.conf import settings
from django.core.mail import send_mail

from .models import Notification
from apps.stores.models import Store

logger = logging.getLogger(__name__)


# FIX (B2): default sent_via updated from "web" to "in_app" to match
# the new Notification.SENT_VIA_CHOICES (in_app/email/sms).
#
# FIX (Cross-check, Sep 2026 — PDF Part 2 Item 3): reference_type/
# reference_id were added to the Notification model and to every caller
# of this function (checkout, cancel, admin status update, reinstate,
# complaints, QR payment flows, the stale-payment cron job) but were
# never added to this function's signature. Every one of those callers
# passes reference_type=/reference_id= as keyword arguments, so every
# single automatic notification in the app was raising
# "TypeError: create_notification() got an unexpected keyword argument
# 'reference_type'" at runtime — breaking checkout, cancellation, order
# status updates, reinstate, complaint replies, and all QR payment
# notifications. Adding the two parameters here (optional, default
# None, per spec: "POST /api/v1/notifications/send/ ... default null if
# omitted") and storing them on the created row fixes this.
def create_notification(
    user,
    title,
    message,
    notification_type,
    store=None,
    sent_via="in_app",
    reference_type=None,
    reference_id=None,
):
    # FIX (Cross-check, checkout crash — Sep 2026): this function used to
    # let Notification.objects.create() raise straight up to the caller.
    # Notification.store is a required (non-null) FK, and when no store
    # was passed in, we fell back to Store.objects.first() — which can be
    # None (no Store rows yet, or the query fails for any other reason).
    # That produced an IntegrityError *after* the caller had already
    # committed its own work (e.g. CheckoutView had already saved the
    # Order + Payment in its own transaction.atomic() block), so the
    # customer's order was created successfully in the DB but the
    # unhandled exception crashed the request before any response could
    # be sent back — the frontend saw zero response headers.
    #
    # A notification is a side effect only — nothing in this codebase
    # uses this function's return value — so it must never be allowed to
    # break whatever real business action (checkout, cancel, complaint
    # reply, refund, cron job, etc.) triggered it. We now resolve the
    # store defensively and wrap the actual create in try/except,
    # logging on any failure instead of raising.
    if store is None:
        store = Store.objects.first()

    if store is None:
        logger.warning(
            "create_notification: no Store available, skipping notification "
            "(title=%r, user=%s, reference_type=%s, reference_id=%s).",
            title, getattr(user, "id", None), reference_type, reference_id,
        )
        return None

    try:
        return Notification.objects.create(
            user=user,
            store=store,
            title=title,
            message=message,
            type=notification_type,
            sent_via=sent_via,
            reference_type=reference_type,
            reference_id=str(reference_id) if reference_id is not None else None,
        )
    except Exception:
        logger.exception(
            "create_notification: failed to create notification "
            "(title=%r, user=%s, reference_type=%s, reference_id=%s).",
            title, getattr(user, "id", None), reference_type, reference_id,
        )
        return None


# FIX (F14): customer previously got zero confirmation that their order was
# even placed — only an in-app Notification row was created, no actual
# email. This sends a real transactional email via the already-configured
# Gmail SMTP backend. Wrapped so a failed/unconfigured email backend can
# never break checkout itself — the order must still succeed even if the
# email fails to send, so we log and swallow the exception instead of
# raising.
def send_order_confirmation_email(order):
    customer = order.customer
    to_email = customer.email if customer else None

    if not to_email:
        logger.info(
            "send_order_confirmation_email: no email on file for order %s, skipping.",
            order.order_number,
        )
        return False

    subject = f"Order Confirmed — #{order.order_number}"

    lines = [
        f"Hi {customer.name},",
        "",
        f"Thanks for your order! We've received order #{order.order_number}.",
        "",
        "Order summary:",
    ]

    for item in order.items.all():
        lines.append(f"  - {item.quantity} x {item.product_name} — Rs. {item.total_price}")

    lines += [
        "",
        f"Total: Rs. {order.total_amount}",
        f"Shipping to: {order.shipping_address}, {order.city}",
        "",
        "We'll notify you again once your order is confirmed and shipped.",
    ]

    message = "\n".join(lines)

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or settings.EMAIL_HOST_USER,
            recipient_list=[to_email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception(
            "send_order_confirmation_email: failed to send email for order %s",
            order.order_number,
        )
        return False


# FIX (B29): sends the customer a clear confirmation that their refund was
# processed, instead of leaving them to guess from a silent status change.
def send_refund_confirmation_email(order):
    customer = order.customer
    to_email = customer.email if customer else None

    if not to_email:
        return False

    subject = f"Refund Processed — Order #{order.order_number}"
    message = (
        f"Hi {customer.name},\n\n"
        f"Your order #{order.order_number} was cancelled and your payment of "
        f"Rs. {order.total_amount} has been refunded.\n\n"
        "If you paid by card, please allow a few business days for the refund "
        "to reflect in your account."
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or settings.EMAIL_HOST_USER,
            recipient_list=[to_email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception(
            "send_refund_confirmation_email: failed to send email for order %s",
            order.order_number,
        )
        return False