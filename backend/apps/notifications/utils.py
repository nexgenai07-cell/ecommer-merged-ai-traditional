import logging

from django.conf import settings
from django.core.mail import send_mail

from .models import Notification
from apps.stores.models import Store

logger = logging.getLogger(__name__)


def create_notification(
    user,
    title,
    message,
    notification_type,
    reference_type,
    reference_id,
    store=None,
    sent_via="web",
):
    if store is None:
        store = Store.objects.first()

    return Notification.objects.create(
        user=user,
        store=store,
        title=title,
        message=message,
        type=notification_type,
        reference_type=reference_type,
        reference_id=str(reference_id),
        sent_via=sent_via,
    )


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
        lines.append(
            f"  - {item.quantity} x {item.product_name} — Rs. {item.total_price}"
        )

    lines += [
        "",
        f"Total: Rs. {order.total_amount}",
        f"Shipping to: {order.shipping_address}, {order.city}",
        "",
        "We'll notify you again once your order is confirmed and shipped.",
    ]

    try:
        send_mail(
            subject=subject,
            message="\n".join(lines),
            from_email=(
                getattr(settings, "DEFAULT_FROM_EMAIL", None)
                or settings.EMAIL_HOST_USER
            ),
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
            from_email=(
                getattr(settings, "DEFAULT_FROM_EMAIL", None)
                or settings.EMAIL_HOST_USER
            ),
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