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
    button_text="Verify My Account",
    html_intro=None,
):
    """
    Sends verification / password-reset / reactivation emails via Gmail SMTP.

    NOTE: function name kept as-is (send_verification_with_resend) so that
    email_verification_views.py and views.py don't need any changes to
    their imports — only the internal sending mechanism changed from
    Resend's HTTP API to Django's SMTP email backend (Gmail).

    UPDATED (v4.1): the raw verification link is no longer shown as a
    visible blue link above the button in the HTML email — only a short
    instruction line (html_intro, e.g. "Click the button below...")
    appears there now, per supervisor's request. The plain-text email
    body (message) still includes the actual link, since plain-text-only
    email clients have no button to click. The full link is still shown
    at the very bottom as a fallback ("If the button above doesn't
    work...") in case the button itself fails to render.
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

    if html_intro is None:
        html_intro = "Click the button below to verify your email address."

        html_message = f"""
        <html>
            <body style="margin:0; padding:0; background-color:#f4f4f7; font-family: Arial, Helvetica, sans-serif;">
                <center>
                    <table role="presentation" width="100%" align="center" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7; padding: 40px 0;">
                        <tr>
                            <td align="center">
                                <table role="presentation" width="480" align="center" cellpadding="0" cellspacing="0" style="background-color:#ffffff; border-radius: 8px; padding: 40px; margin: 0 auto; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
                                    <tr>
                                        <td align="center">
                                            <h2 style="margin: 0 0 16px 0; color: #1a1a1a; font-size: 20px; text-align: center;">
                                                {subject}
                                            </h2>
                                            <p style="margin: 0 0 24px 0; color: #444444; font-size: 15px; line-height: 1.6; text-align: center;">
                                                {html_intro}
                                            </p>
                                            <table role="presentation" align="center" cellpadding="0" cellspacing="0" style="margin: 0 auto;">
                                                <tr>
                                                    <td align="center" style="border-radius: 6px; background-color: #16a34a;">
                                                        <a href="{verify_link}"
                                                           target="_blank"
                                                           style="display: inline-block; padding: 14px 32px; font-size: 15px; font-weight: bold; color: #ffffff; text-decoration: none; border-radius: 6px;">
                                                            {button_text}
                                                        </a>
                                                    </td>
                                                </tr>
                                            </table>
                                            <p style="margin: 28px 0 0 0; color: #999999; font-size: 13px; line-height: 1.5; text-align: center;">
                                                If the button above doesn't work, copy and paste this link into your browser:<br>
                                                <a href="{verify_link}" style="color: #16a34a; word-break: break-all;">{verify_link}</a>
                                            </p>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>
                </center>
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


def send_2fa_code_email(user, code):
    """
    Sends the 6-digit 2FA verification code to the user's email.
    Used both during 2FA setup (API 16) and during login when 2FA is
    enabled (API 2 -> API 10).
    """
    subject = "Your verification code"
    message = (
        f"Your two-factor authentication code is: {code}\n\n"
        "This code is valid for 10 minutes. If you did not request this, "
        "please ignore this email."
    )
    html_message = f"""
        <html>
            <body>
                <h2>Your verification code</h2>
                <p>Use this code to complete sign-in:</p>
                <h1 style="letter-spacing: 4px;">{code}</h1>
                <p>This code is valid for 10 minutes.</p>
            </body>
        </html>
    """

    try:
        email_msg = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        email_msg.attach_alternative(html_message, "text/html")
        email_msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("send_2fa_code_email: failed to send code to %s", user.email)
        return False