import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)


def send_verification_with_resend(
    email,
    verify_link,
    subject="Verify your email address",
    message=None,
    from_email=None,
    recipient_list=None,
    fail_silently=False,
):
    """
    Sends verification / password-reset / reactivation emails via Gmail SMTP.

    NOTE: function name kept as-is (send_verification_with_resend) so that
    email_verification_views.py and views.py don't need any changes —
    only the internal sending mechanism changed from Resend's HTTP API
    to Django's SMTP email backend (Gmail).
    """

    recipients = recipient_list or [email]
    sender = from_email or settings.DEFAULT_FROM_EMAIL

    if message is None:
        message = (
            "Please verify your email address by clicking "
            "the link below:\n\n"
            f"{verify_link}\n\n"
            "This verification link is valid for 24 hours."
        )

    html_message = f"""
        <html>
            <body>
                <h2>{subject}</h2>
                <p>{message.replace(chr(10), '<br>')}</p>
                <p><a href="{verify_link}">Continue</a></p>
            </body>
        </html>
    """

    try:
        email_msg = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=sender,
            to=recipients,
        )
        email_msg.attach_alternative(html_message, "text/html")
        email_msg.send(fail_silently=False)

        logger.info("Verification email sent to %s", recipients)
        return True

    except Exception:
        logger.exception("send_verification_with_resend: failed to send email to %s", recipients)
        if fail_silently:
            return None
        raise