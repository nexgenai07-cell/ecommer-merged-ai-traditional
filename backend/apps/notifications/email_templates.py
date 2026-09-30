# PATH: apps/notifications/email_templates.py
#
# ONE shared, email-client-safe HTML layout for every email the project
# sends (order confirmation, refund, order status, OTP codes, verification
# links, abandoned cart, sales report).
#
# WHY: most emails were the plain-text body wrapped in <pre>...</pre> (that
# is the monospace, no-styling "typewriter" look in the order confirmation)
# or bare <h2>/<p> tags. Every email now shares the same clean, branded,
# mobile-friendly layout, so they all look consistent.
#
# Rules this module follows so it can never break an email:
#   * Only builds the HTML part. The plain-text body of each email is
#     untouched (it is still sent as the text alternative).
#   * Every public builder NEVER raises: if anything goes wrong while
#     building, it logs the problem and returns a simple, safe fallback
#     HTML made from the plain text - the email still goes out.
#   * Every dynamic value is HTML-escaped (names, addresses, product names,
#     messages), so nothing a customer typed can inject markup.
#   * Email-client-safe: table layout + inline CSS only (no <style> blocks,
#     no external images except the store logo when it has a full URL).
#
# Store name / logo / phone are read from the Store row, so the emails
# follow whatever the admin sets in store settings.

import html
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# --- one place to tweak the look ------------------------------------------
BRAND = "#16a34a"        # main green (same green the verification email used)
BRAND_DARK = "#15803d"
BRAND_SOFT = "#f0fdf4"
TEXT = "#1f2937"
MUTED = "#6b7280"
BORDER = "#e5e7eb"
PAGE_BG = "#f4f4f7"
WARN = "#b45309"
WARN_SOFT = "#fffbeb"
DANGER = "#b91c1c"
DANGER_SOFT = "#fef2f2"
FONT = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

# Where the buttons in emails lead. Both default to the store home page; if
# your frontend has dedicated pages, set the path here (e.g. "/cart").
CART_PATH = ""
ORDERS_PATH = ""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def _multiline(value):
    return esc(value).replace("\r\n", "\n").replace("\n", "<br>")


def money(value):
    try:
        return f"Rs. {Decimal(str(value)):,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return f"Rs. {esc(value)}"


def fmt_dt(value):
    """'30 Sep 2026, 11:23 AM' in the project's local time zone."""
    if not value:
        return ""
    try:
        value = timezone.localtime(value)
    except Exception:
        pass
    return value.strftime("%d %b %Y, %I:%M %p").lstrip("0")


def store_url(path=""):
    base = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else ""


def get_store_info():
    """{'name', 'logo_url', 'phone'} for the store; safe defaults on any error."""
    info = {"name": "Our Store", "logo_url": None, "phone": None}
    try:
        from apps.stores.models import Store

        store = Store.objects.first()
        if store is None:
            return info
        info["name"] = store.name or info["name"]
        info["phone"] = store.phone or None
        if store.logo:
            url = store.logo.url
            # Only use the logo when it is a full public URL (Cloudinary);
            # a relative /media/... path would show as a broken image.
            if url.startswith("http"):
                info["logo_url"] = url.replace("http://", "https://")
    except Exception:
        logger.exception("email_templates: could not read store info")
    return info


# ---------------------------------------------------------------------------
# building blocks (return HTML strings)
# ---------------------------------------------------------------------------

def icon_circle(symbol, color=BRAND, soft=BRAND_SOFT):
    return f"""
    <table role="presentation" align="center" cellpadding="0" cellspacing="0" style="margin:0 auto 16px auto;">
      <tr><td align="center" width="64" height="64" style="width:64px;height:64px;border-radius:32px;background:{soft};color:{color};font-size:30px;line-height:64px;font-weight:bold;">{symbol}</td></tr>
    </table>"""


def heading(text, sub=None, align="center"):
    out = (
        f'<h1 style="margin:0 0 8px 0;font-family:{FONT};font-size:24px;line-height:1.3;'
        f'color:{TEXT};text-align:{align};">{esc(text)}</h1>'
    )
    if sub:
        out += (
            f'<p style="margin:0 0 24px 0;font-family:{FONT};font-size:15px;line-height:1.6;'
            f'color:{MUTED};text-align:{align};">{sub}</p>'
        )
    return out


def paragraph(text_html, align="left", color=TEXT, size=15, margin="0 0 16px 0"):
    """text_html must already be escaped / trusted HTML."""
    return (
        f'<p style="margin:{margin};font-family:{FONT};font-size:{size}px;line-height:1.7;'
        f'color:{color};text-align:{align};">{text_html}</p>'
    )


def button(label, url, align="center"):
    if not url:
        return ""
    return f"""
    <table role="presentation" align="{align}" cellpadding="0" cellspacing="0" style="margin:8px auto 8px auto;">
      <tr><td align="center" style="border-radius:8px;background:{BRAND};">
        <a href="{esc(url)}" target="_blank" style="display:inline-block;padding:14px 32px;font-family:{FONT};font-size:15px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:8px;">{esc(label)}</a>
      </td></tr>
    </table>"""


def otp_box(code):
    return f"""
    <table role="presentation" align="center" cellpadding="0" cellspacing="0" style="margin:8px auto 20px auto;">
      <tr><td align="center" style="background:{BRAND_SOFT};border:2px dashed {BRAND};border-radius:10px;padding:16px 26px 16px 36px;font-family:'Courier New',Courier,monospace;font-size:34px;font-weight:bold;letter-spacing:10px;color:{BRAND_DARK};">{esc(code)}</td></tr>
    </table>"""


def callout(text_html, tone="info"):
    colors = {
        "info": (BRAND_SOFT, BRAND, BRAND_DARK),
        "warning": (WARN_SOFT, "#f59e0b", WARN),
        "danger": (DANGER_SOFT, "#ef4444", DANGER),
    }
    bg, border, fg = colors.get(tone, colors["info"])
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px 0;">
      <tr><td style="background:{bg};border-left:4px solid {border};border-radius:6px;padding:12px 16px;font-family:{FONT};font-size:14px;line-height:1.6;color:{fg};">{text_html}</td></tr>
    </table>"""


def section_title(text):
    return (
        f'<p style="margin:24px 0 8px 0;font-family:{FONT};font-size:12px;font-weight:bold;'
        f'letter-spacing:1px;text-transform:uppercase;color:{MUTED};">{esc(text)}</p>'
    )


def kv_table(rows):
    """rows: list of (label, value_html). Skips rows with an empty value."""
    cells = ""
    for label, value in rows:
        if value in (None, ""):
            continue
        cells += f"""
      <tr>
        <td style="padding:8px 0;border-bottom:1px solid {BORDER};font-family:{FONT};font-size:14px;color:{MUTED};width:38%;vertical-align:top;">{esc(label)}</td>
        <td style="padding:8px 0;border-bottom:1px solid {BORDER};font-family:{FONT};font-size:14px;color:{TEXT};text-align:right;vertical-align:top;">{value}</td>
      </tr>"""
    if not cells:
        return ""
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 8px 0;">{cells}</table>'


def divider():
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="border-top:1px solid {BORDER};font-size:0;line-height:0;height:1px;">&nbsp;</td></tr></table>'


# ---------------------------------------------------------------------------
# page layout
# ---------------------------------------------------------------------------

def render_email(title, body_html, preheader="", footer_note=None):
    store = get_store_info()

    if store["logo_url"]:
        brand = (
            f'<img src="{esc(store["logo_url"])}" alt="{esc(store["name"])}" height="40" '
            f'style="height:40px;border:0;display:inline-block;vertical-align:middle;">'
        )
    else:
        brand = (
            f'<span style="font-family:{FONT};font-size:22px;font-weight:bold;'
            f'letter-spacing:.5px;color:{TEXT};">{esc(store["name"])}</span>'
        )

    footer_lines = []
    if footer_note:
        footer_lines.append(footer_note)
    contact = f' &middot; {esc(store["phone"])}' if store["phone"] else ""
    footer_lines.append(f"{esc(store['name'])}{contact}")
    footer_lines.append("This is an automated email.")
    footer_html = "<br>".join(footer_lines)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
</head>
<body style="margin:0;padding:0;background:{PAGE_BG};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;font-size:1px;line-height:1px;">{esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE_BG};padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">
      <tr><td align="center" style="padding:0 0 16px 0;">{brand}</td></tr>
      <tr><td style="background:#ffffff;border:1px solid {BORDER};border-radius:12px;overflow:hidden;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr><td style="height:5px;line-height:5px;font-size:0;background:{BRAND};">&nbsp;</td></tr>
          <tr><td style="padding:32px 28px 28px 28px;">{body_html}</td></tr>
        </table>
      </td></tr>
      <tr><td align="center" style="padding:20px 12px 0 12px;font-family:{FONT};font-size:12px;line-height:1.7;color:{MUTED};">{footer_html}</td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def _fallback(title, plain_text):
    """Simple, always-safe HTML used only if a builder fails."""
    return (
        f'<html><body style="font-family:{FONT};color:{TEXT};line-height:1.6;">'
        f"<h2>{esc(title)}</h2><p>{_multiline(plain_text)}</p></body></html>"
    )


def _safe(builder, title, plain_text):
    try:
        return builder()
    except Exception:
        logger.exception("email_templates: failed to build '%s', using fallback", title)
        return _fallback(title, plain_text)


# ---------------------------------------------------------------------------
# ready-made emails
# ---------------------------------------------------------------------------

def order_confirmation_html(order, customer, plain_text=""):
    def build():
        items = list(order.items.all())
        subtotal = sum((i.total_price for i in items), Decimal("0"))
        discount = Decimal(str(order.discount_amount or 0))
        shipping = Decimal(str(order.shipping_cost or 0))

        # Item rows
        item_rows = ""
        for i in items:
            item_rows += f"""
          <tr>
            <td style="padding:12px 0;border-bottom:1px solid {BORDER};font-family:{FONT};font-size:14px;color:{TEXT};">
              <strong>{esc(i.product_name)}</strong><br>
              <span style="color:{MUTED};font-size:13px;">Qty {esc(i.quantity)} &times; {money(i.price)}</span>
            </td>
            <td align="right" style="padding:12px 0;border-bottom:1px solid {BORDER};font-family:{FONT};font-size:14px;color:{TEXT};white-space:nowrap;vertical-align:top;"><strong>{money(i.total_price)}</strong></td>
          </tr>"""

        def total_row(label, value_html, bold=False, color=TEXT, big=False):
            size = 18 if big else 14
            weight = "bold" if bold else "normal"
            return f"""
          <tr>
            <td style="padding:6px 0;font-family:{FONT};font-size:{size}px;font-weight:{weight};color:{color};">{esc(label)}</td>
            <td align="right" style="padding:6px 0;font-family:{FONT};font-size:{size}px;font-weight:{weight};color:{color};white-space:nowrap;">{value_html}</td>
          </tr>"""

        totals = total_row("Subtotal", money(subtotal), color=MUTED)
        if discount > 0:
            totals += total_row("Discount", f"- {money(discount)}", color=BRAND_DARK)
        totals += total_row(
            "Shipping", "Free" if shipping <= 0 else money(shipping), color=MUTED
        )
        totals += f'<tr><td colspan="2" style="padding:6px 0 0 0;border-top:2px solid {TEXT};font-size:0;line-height:0;">&nbsp;</td></tr>'
        totals += total_row("Total", money(order.total_amount), bold=True, big=True)

        # Payment / delivery details
        payment_label = None
        qr_deadline = None
        try:
            payment = order.payment
            payment_label = {"qr": "QR code payment", "stripe": "Card payment"}.get(
                payment.payment_method
            )
            if payment.payment_method == "qr" and order.status == "order_placed":
                qr_deadline = payment.qr_upload_deadline
        except Exception:
            pass

        delivery = None
        try:
            from apps.orders.serializers import SHIPPING_DELIVERY_ESTIMATES

            est = SHIPPING_DELIVERY_ESTIMATES.get(order.shipping_method)
            if est:
                delivery = f"{esc(str(order.shipping_method).title())} &middot; {esc(est['label'])}"
        except Exception:
            pass

        details = kv_table([
            ("Order number", f"<strong>#{esc(order.order_number)}</strong>"),
            ("Order date", esc(fmt_dt(order.created_at))),
            ("Payment", esc(payment_label) if payment_label else ""),
            ("Estimated delivery", delivery or ""),
        ])

        # Delivery address
        phone = order.contact_phone or (customer.phone if customer else "")
        address_lines = [order.shipping_address]
        city_line = ", ".join(p for p in [order.city, order.postal_code] if p)
        if city_line:
            address_lines.append(city_line)
        address_html = (
            f"<strong>{esc(customer.name if customer else '')}</strong><br>"
            + "<br>".join(_multiline(l) for l in address_lines if l)
            + (f"<br>{esc(phone)}" if phone else "")
        )

        deadline_note = ""
        if qr_deadline:
            deadline_note = callout(
                "<strong>One more step:</strong> please upload your payment "
                f"screenshot before <strong>{esc(fmt_dt(qr_deadline))}</strong> "
                "so we can confirm your order.",
                "warning",
            )

        name = esc(customer.name) if customer and customer.name else "there"
        body = (
            icon_circle("&#10003;")
            + heading(
                "Thank you for your order!",
                f"Hi {name}, we&rsquo;ve received your order and will keep you "
                "updated as soon as it&rsquo;s confirmed and shipped.",
            )
            + deadline_note
            + section_title("Order details")
            + details
            + section_title("Your items")
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{item_rows}</table>'
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 0 0;">{totals}</table>'
            + section_title("Delivering to")
            + f'<p style="margin:0 0 24px 0;font-family:{FONT};font-size:14px;line-height:1.7;color:{TEXT};">{address_html}</p>'
            + button("Continue Shopping", store_url(ORDERS_PATH))
        )
        return render_email(
            f"Order Confirmed #{order.order_number}",
            body,
            preheader=f"We've received order #{order.order_number} - total {money(order.total_amount)}.",
            footer_note="Thank you for shopping with us!",
        )

    return _safe(build, "Order confirmed", plain_text)


def refund_html(order, customer, plain_text=""):
    def build():
        name = esc(customer.name) if customer and customer.name else "there"
        body = (
            icon_circle("&#8617;")
            + heading(
                "Your refund has been processed",
                f"Hi {name}, your order was cancelled and your payment has been refunded.",
            )
            + kv_table([
                ("Order number", f"<strong>#{esc(order.order_number)}</strong>"),
                ("Refunded amount", f"<strong>{money(order.total_amount)}</strong>"),
            ])
            + callout(
                "If you paid by card, please allow a few business days for the "
                "refund to show up in your account."
            )
            + button("Continue Shopping", store_url())
        )
        return render_email(
            f"Refund processed - Order #{order.order_number}",
            body,
            preheader=f"{money(order.total_amount)} refunded for order #{order.order_number}.",
        )

    return _safe(build, "Refund processed", plain_text)


_STATUS_ICONS = {
    "confirmed": ("&#10003;", BRAND, BRAND_SOFT),
    "shipped": ("&#128230;", BRAND, BRAND_SOFT),
    "out_for_delivery": ("&#128666;", BRAND, BRAND_SOFT),
    "delivered": ("&#127881;", BRAND, BRAND_SOFT),
    "on_hold": ("&#9203;", WARN, WARN_SOFT),
    "pending_payment": ("&#9203;", WARN, WARN_SOFT),
    "cancelled": ("&#10005;", DANGER, DANGER_SOFT),
}


def order_status_html(order, title, message):
    def build():
        symbol, color, soft = _STATUS_ICONS.get(
            getattr(order, "status", ""), ("&#128276;", BRAND, BRAND_SOFT)
        )
        body = (
            icon_circle(symbol, color, soft)
            + heading(title)
            + paragraph(_multiline(message), align="center", margin="0 0 24px 0")
            + kv_table([("Order number", f"<strong>#{esc(order.order_number)}</strong>")])
            + button("View My Orders", store_url(ORDERS_PATH))
        )
        return render_email(
            f"Order {order.order_number}: {title}",
            body,
            preheader=f"{title} - order #{order.order_number}",
        )

    return _safe(build, title, message)


def otp_html(title, intro, code, validity_text, note, extra_rows=None, plain_text=""):
    """Shared by checkout OTP, 2FA, email/phone/password change codes."""
    def build():
        body = (
            icon_circle("&#128274;")
            + heading(title, esc(intro))
            + otp_box(code)
            + (kv_table(extra_rows) if extra_rows else "")
            + paragraph(esc(validity_text), align="center", color=MUTED, size=14, margin="0 0 8px 0")
            + paragraph(esc(note), align="center", color=MUTED, size=13, margin="0")
        )
        return render_email(
            title, body, preheader=f"Your code is {code}",
            footer_note="Never share this code with anyone.",
        )

    return _safe(build, title, plain_text or f"{intro}\n\n{code}")


def notice_html(title, paragraphs_html, tone="warning", plain_text=""):
    """Security-style notice (e.g. 'email change requested')."""
    def build():
        body = (
            icon_circle("&#9888;", WARN if tone == "warning" else DANGER,
                        WARN_SOFT if tone == "warning" else DANGER_SOFT)
            + heading(title)
            + "".join(paragraph(p, align="center") for p in paragraphs_html)
        )
        return render_email(title, body, preheader=title)

    return _safe(build, title, plain_text)


def verification_html(subject, html_intro, verify_link, button_text, plain_text=""):
    def build():
        body = (
            icon_circle("&#9993;")
            + heading(subject, esc(html_intro))
            + button(button_text, verify_link)
            + paragraph(
                "If the button above doesn&rsquo;t work, copy and paste this link "
                f'into your browser:<br><a href="{esc(verify_link)}" '
                f'style="color:{BRAND};word-break:break-all;">{esc(verify_link)}</a>',
                align="center", color=MUTED, size=13, margin="20px 0 0 0",
            )
        )
        return render_email(
            subject, body, preheader=str(html_intro),
            footer_note="Never share this link with anyone.",
        )

    return _safe(build, subject, plain_text)


def abandoned_cart_html(name, items, plain_text=""):
    """items: list of (product_name, quantity)."""
    def build():
        rows = ""
        for product_name, qty in items:
            rows += f"""
          <tr>
            <td style="padding:12px 0;border-bottom:1px solid {BORDER};font-family:{FONT};font-size:14px;color:{TEXT};"><strong>{esc(product_name)}</strong></td>
            <td align="right" style="padding:12px 0;border-bottom:1px solid {BORDER};font-family:{FONT};font-size:14px;color:{MUTED};white-space:nowrap;">Qty {esc(qty)}</td>
          </tr>"""
        who = esc(name) if name else "there"
        body = (
            icon_circle("&#128722;")
            + heading(
                "You left something in your cart",
                f"Hi {who}, your items are still waiting for you.",
            )
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px 0;">{rows}</table>'
            + paragraph("Complete your order before it sells out.", align="center", color=MUTED)
            + button("Complete My Order", store_url(CART_PATH))
        )
        return render_email(
            "You left something in your cart",
            body,
            preheader="Your items are still waiting - complete your order before it sells out.",
        )

    return _safe(build, "You left something in your cart", plain_text)


def sales_report_html(store_name, sections, plain_text=""):
    """sections: list of (label, orders_count, revenue)."""
    def build():
        blocks = ""
        for label, orders, revenue in sections:
            blocks += f"""
      {section_title(label)}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 8px 0;">
        <tr>
          <td width="48%" align="center" style="background:{BRAND_SOFT};border-radius:10px;padding:18px 8px;">
            <div style="font-family:{FONT};font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:1px;">Orders</div>
            <div style="font-family:{FONT};font-size:26px;font-weight:bold;color:{BRAND_DARK};padding-top:4px;">{esc(orders)}</div>
          </td>
          <td width="4%">&nbsp;</td>
          <td width="48%" align="center" style="background:{BRAND_SOFT};border-radius:10px;padding:18px 8px;">
            <div style="font-family:{FONT};font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:1px;">Revenue</div>
            <div style="font-family:{FONT};font-size:26px;font-weight:bold;color:{BRAND_DARK};padding-top:4px;">{money(revenue)}</div>
          </td>
        </tr>
      </table>"""
        body = (
            icon_circle("&#128200;")
            + heading("Sales report", esc(store_name))
            + blocks
            + paragraph(
                "Revenue counts confirmed, shipped, out-for-delivery and delivered orders.",
                align="center", color=MUTED, size=12, margin="16px 0 0 0",
            )
        )
        return render_email("Sales report", body, preheader=f"Sales report for {store_name}")

    return _safe(build, "Sales report", plain_text)