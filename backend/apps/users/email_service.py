import requests
from django.conf import settings


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
    Send an email through Resend.

    Used for:
    - Email verification
    - Password reset
    - Account reactivation

    The first two arguments remain compatible with the existing
    registration verification flow.
    """

    url = "https://api.resend.com/emails"

    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    recipients = recipient_list or [email]

    sender = from_email or settings.RESEND_FROM_EMAIL

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

                <p>
                    {message.replace(chr(10), '<br>')}
                </p>

                <p>
                    <a href="{verify_link}">
                        Continue
                    </a>
                </p>
            </body>
        </html>
    """

    payload = {
        "from": sender,
        "to": recipients,
        "subject": subject,
        "html": html_message,
        "text": message,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=10,
        )

        print("RESEND STATUS:", response.status_code)
        print("RESEND RESPONSE:", response.text)

        response.raise_for_status()

        return response.json()

    except requests.RequestException:
        if fail_silently:
            return None

        raise