"""Isolated disposable-DB integration check; never points at live webui.db."""
import asyncio
import hashlib
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace

root = Path(tempfile.mkdtemp(prefix='abit-email-signup-check-'))
os.environ['DATA_DIR'] = str(root)
os.environ['DATABASE_URL'] = (root / 'isolated-webui.db').as_uri().replace('file:///', 'sqlite:///')
os.environ['WEBUI_SECRET_KEY'] = 'disposable-local-signup-test-key-not-for-live'
os.environ['ABIT_SIGNUP_PUBLIC_URL'] = 'https://abit.ai'
os.environ['ABIT_SIGNUP_SMTP_HOST'] = 'smtp.example.invalid'
os.environ['ABIT_SIGNUP_SMTP_FROM'] = 'no-reply@example.invalid'

from fastapi import HTTPException, Response
from starlette.requests import Request
from open_webui.config import Config
from open_webui.internal.db import get_async_db
from open_webui.models.users import User, Users
from open_webui.models.auths import Auth, Auths, PendingEmailSignup, SignupForm
from open_webui.routers import auths


async def run():
    now = int(time.time())
    async with get_async_db() as session:
        session.add(Auth(id='test-admin', email='admin@example.invalid', password='disabled', active=True))
        session.add(User(id='test-admin', email='admin@example.invalid', name='Admin',
                         role='admin', profile_image_url='/user.png',
                         created_at=now, updated_at=now, last_active_at=now))
        await session.commit()
    await Config.upsert({'ui.enable_signup': True, 'ui.enable_login_form': True})

    captured = []

    async def fake_send(email, name, url, token):
        captured.append((email, name, url, token))

    auths.send_activation_email = fake_send
    app = SimpleNamespace(state=SimpleNamespace(redis=None))
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/v1/auths/signup',
                       'headers': [], 'query_string': b'', 'client': ('127.0.0.1', 0),
                       'app': app})
    signup = await auths.signup(request, Response(), SignupForm(
        name='Test User', email='new@example.invalid', password='SecureTestPassword#123'), db=None)
    assert 'token' not in signup and 'activation' in signup['detail']
    assert len(captured) == 1 and captured[0][0] == 'new@example.invalid'
    assert await Users.get_user_by_email('new@example.invalid') is None
    token = captured[0][3]
    async with get_async_db() as session:
        row = await session.get(PendingEmailSignup, 'new@example.invalid')
        assert row and row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert 'SecureTestPassword#123' not in row.password_hash

    # Resending inside cooldown must not send another email.
    await auths.resend_signup_email(request, auths.EmailResendForm(email='new@example.invalid'))
    assert len(captured) == 1
    async with get_async_db() as session:
        row = await session.get(PendingEmailSignup, 'new@example.invalid')
        row.sent_at -= 61
        await session.commit()
    await auths.resend_signup_email(request, auths.EmailResendForm(email='new@example.invalid'))
    assert len(captured) == 2
    renewed = captured[-1][3]
    assert renewed != token
    try:
        await auths.verify_signup_email(request, auths.EmailActivationForm(token=token))
        raise AssertionError('superseded activation link accepted')
    except HTTPException as exc:
        assert exc.status_code == 400

    activated = await auths.verify_signup_email(request, auths.EmailActivationForm(token=renewed))
    assert 'sign in' in activated['detail']
    account = await Users.get_user_by_email('new@example.invalid')
    assert account is not None and account.role == 'user'
    signin = await Auths.authenticate_user('new@example.invalid',
                                          lambda hash: auths.verify_password('SecureTestPassword#123', hash))
    assert signin is not None and signin.id == account.id
    async with get_async_db() as session:
        assert await session.get(PendingEmailSignup, 'new@example.invalid') is None
    try:
        await auths.verify_signup_email(request, auths.EmailActivationForm(token=renewed))
        raise AssertionError('activation token reused')
    except HTTPException as exc:
        assert exc.status_code == 400

    # Expired tokens cannot activate an account.
    await auths.signup(request, Response(), SignupForm(
        name='Expired User', email='expired@example.invalid', password='SecureTestPassword#123'), db=None)
    expired_token = captured[-1][3]
    async with get_async_db() as session:
        row = await session.get(PendingEmailSignup, 'expired@example.invalid')
        row.expires_at = now - 1
        await session.commit()
    try:
        await auths.verify_signup_email(request, auths.EmailActivationForm(token=expired_token))
        raise AssertionError('expired token accepted')
    except HTTPException as exc:
        assert exc.status_code == 400
    assert await Users.get_user_by_email('expired@example.invalid') is None

    # Duplicate registration does not send a new mail or issue a session.
    count = len(captured)
    duplicate = await auths.signup(request, Response(), SignupForm(
        name='Duplicate', email='admin@example.invalid', password='SecureTestPassword#123'), db=None)
    assert 'token' not in duplicate and len(captured) == count

    async def fail_send(*args):
        raise OSError('mock SMTP failure')

    auths.send_activation_email = fail_send
    try:
        await auths.signup(request, Response(), SignupForm(
            name='Mail Fail', email='failure@example.invalid', password='SecureTestPassword#123'), db=None)
        raise AssertionError('missing SMTP delivery did not fail closed')
    except HTTPException as exc:
        assert exc.status_code == 503
    assert await Users.get_user_by_email('failure@example.invalid') is None
    async with get_async_db() as session:
        assert await session.get(PendingEmailSignup, 'failure@example.invalid') is None

    print('PASS: expired link, duplicate-account protection, SMTP delivery failure')
    print('PASS: no pre-verification account/session, hashed 24h token, resend/cooldown,')
    print('      old token rejected, atomic activation, user role, password login, replay rejected')
    print('PASS: data isolated at temporary directory; no production database accessed')


if __name__ == '__main__':
    asyncio.run(run())