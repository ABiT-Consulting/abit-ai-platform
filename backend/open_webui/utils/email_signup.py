"""ABiT verified-email registration delivery (no tokens/secrets logged)."""

import asyncio
from email.message import EmailMessage
from html import escape
import os
import smtplib
import ssl
from urllib.parse import urlsplit, urlencode


GENERIC_SIGNUP_MESSAGE = 'If this address can be registered, an activation link has been sent.'
TOKEN_TTL_SECONDS = 24 * 60 * 60
RESEND_COOLDOWN_SECONDS = 60


def get_public_url(configured_url: str | None) -> str | None:
    raw = (os.getenv('ABIT_SIGNUP_PUBLIC_URL') or configured_url or '').strip().rstrip('/')
    parsed = urlsplit(raw)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        return None
    if parsed.scheme != 'https' or not parsed.hostname:
        return None
    return raw


def mail_is_configured(public_url: str | None) -> bool:
    return bool(public_url and os.getenv('ABIT_SIGNUP_SMTP_HOST') and os.getenv('ABIT_SIGNUP_SMTP_FROM'))


def _send_activation_email(to_email: str, display_name: str, public_url: str, token: str) -> None:
    link = f'{public_url}/auth?{urlencode({"verify_email": token})}'
    msg = EmailMessage()
    msg['Subject'] = 'Activate your AIBIT account'
    msg['From'] = os.environ['ABIT_SIGNUP_SMTP_FROM']
    msg['To'] = to_email
    msg['Auto-Submitted'] = 'auto-generated'
    msg.set_content(
        f'Hello {display_name},\n\nPlease activate your AIBIT account using this secure link:\n'
        f'{link}\n\nThis link expires in 24 hours and can be used only once.\n'
        'If you did not request an account, ignore this email.\n\nAIBIT\n'
    )
    safe_name = escape(display_name)
    safe_link = escape(link, quote=True)
    msg.add_alternative(
        '<!doctype html><html><body style="font-family:Arial,sans-serif;color:#202a3d;'
        'background:#f5f7fb;padding:30px">'
        '<div style="max-width:540px;margin:auto;background:white;border-radius:16px;'
        'padding:32px;border:1px solid #e2e8f0">'
        '<h2 style="margin-top:0;color:#172554">Welcome to AIBIT</h2>'
        f'<p>Hello {safe_name},</p><p>Verify your email address to activate your account.</p>'
        '<p style="margin:30px 0"><a href="' + safe_link + '" style="background:#1d4ed8;'
        'color:white;padding:14px 22px;border-radius:8px;text-decoration:none">'
        'Activate my account</a></p><p>This one-time link expires in 24 hours.</p>'
        '<p style="font-size:12px;color:#64748b">If you did not register, ignore this email.</p>'
        '</div></body></html>', subtype='html'
    )
    host = os.environ['ABIT_SIGNUP_SMTP_HOST']
    port = int(os.getenv('ABIT_SIGNUP_SMTP_PORT', '587'))
    use_ssl = os.getenv('ABIT_SIGNUP_SMTP_SSL', 'false').lower() == 'true'
    context = ssl.create_default_context()
    transport = smtplib.SMTP_SSL(host, port, timeout=15, context=context) if use_ssl else smtplib.SMTP(host, port, timeout=15)
    with transport as smtp:
        if not use_ssl and os.getenv('ABIT_SIGNUP_SMTP_STARTTLS', 'true').lower() == 'true':
            smtp.starttls(context=context)
        if os.getenv('ABIT_SIGNUP_SMTP_USER'):
            smtp.login(os.environ['ABIT_SIGNUP_SMTP_USER'], os.getenv('ABIT_SIGNUP_SMTP_PASSWORD', ''))
        smtp.send_message(msg)


async def send_activation_email(to_email: str, display_name: str, public_url: str, token: str) -> None:
    await asyncio.to_thread(_send_activation_email, to_email, display_name, public_url, token)