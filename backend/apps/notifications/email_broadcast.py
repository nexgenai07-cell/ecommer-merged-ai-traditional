# PATH: apps/notifications/email_broadcast.py
#
# Actually delivers an admin-sent notification by EMAIL when the admin
# picks "Email" in the Send Notification form (sent_via == "email").
#
# Before this, choosing "Email" only saved the in-app Notification row -
# no email was ever sent. Self-contained (own imports, no changes to
# utils.py) and uses the same EmailMultiAlternatives + DEFAULT_FROM_EMAIL
# setup as every other email in the project (see orders/status_email.py).
#
# Rules:
#   * Specific user  -> that user's email.
#   * Broadcast      -> every active, non-deleted CUSTOMER with an email.
#                       (Admins/moderators are not marketing recipients.)
#   * Sent in a background thread (same fire-and-forget pattern the
#     project already uses for order emails) so a big broadcast can never
#     make the admin's request time out.
#   * Never raises into the request: a failed email is logged, the
#     in-app notification is unaffected.

import logging
import threading

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils.html import escape

logger = logging.getLogger(__name__)


def get_email_recipients(target_user):
    """
    Returns a list of (name, email) tuples.
    target_user=None means broadcast to all customers.
    """
    if target_user is not None:
        if target_user.email:
            return [(target_user.name, target_user.email)]
        return []

    User = get_user_model()
    rows = (
        User.objects.filter(role="customer", is_active=True, is_delete=False)
        .exclude(email="")
        .values_list("name", "email")
    )
    return list(rows)


def _build_message(name, email, title, message, store_name):
    # Subject must be a single line (Django rejects newlines in headers).
    subject = " ".join((title or "").split())[:200] or "Notification"

    greeting = f"Hi {name}," if name else "Hi,"
    text_body = f"{greeting}\n\n{message}\n\n- {store_name}"

    safe_title = escape(title)
    safe_message = escape(message).replace("\n", "<br>")
    html_body = f"""
    <html>
        <body style="margin:0; padding:0; background-color:#f4f4f7; font-family: Arial, Helvetica, sans-serif;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7; padding: 32px 0;">
                <tr>
                    <td align="center">
                        <table role="presentation" width="520" cellpadding="0" cellspacing="0" style="background-color:#ffffff; border-radius: 8px; padding: 32px;">
                            <tr>
                                <td>
                                    <h2 style="margin: 0 0 16px 0; color: #1a1a1a; font-size: 20px;">{safe_title}</h2>
                                    <p style="margin: 0 0 12px 0; color: #444444; font-size: 15px;">{escape(greeting)}</p>
                                    <p style="margin: 0 0 24px 0; color: #444444; font-size: 15px; line-height: 1.6;">{safe_message}</p>
                                    <p style="margin: 0; color: #999999; font-size: 13px;">- {escape(store_name)}</p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
    </html>
    """

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    msg.attach_alternative(html_body, "text/html")
    return msg


def _send_all(recipients, title, message, store_name):
    sent = failed = 0
    connection = None
    try:
        # One SMTP connection reused for the whole batch (much faster
        # than reconnecting for every recipient).
        connection = get_connection(fail_silently=False)
        for name, email in recipients:
            try:
                msg = _build_message(name, email, title, message, store_name)
                msg.connection = connection
                msg.send(fail_silently=False)
                sent += 1
            except Exception:
                failed += 1
                logger.exception(
                    "email_broadcast: failed to send to %s", email
                )
    except Exception:
        logger.exception("email_broadcast: could not open email connection")
        failed = len(recipients) - sent
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    logger.info(
        "email_broadcast: '%s' finished - %s sent, %s failed.",
        title, sent, failed,
    )


def send_notification_email(recipients, title, message, store=None):
    """
    Starts the background send and returns immediately.
    Returns the number of recipients the email was queued for.
    """
    if not recipients:
        return 0

    store_name = getattr(store, "name", None) or "Our Store"

    try:
        threading.Thread(
            target=_send_all,
            args=(recipients, title, message, store_name),
            daemon=True,
        ).start()
    except Exception:
        logger.exception("email_broadcast: could not start send thread")
        return 0

    return len(recipients)
