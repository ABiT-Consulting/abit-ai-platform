# AIBIT verified email signup (Dev-02)

## User journey
1. On `/auth`, choose **Continue with Email → Sign up**. Enter name, email, password and password confirmation.
2. Signup returns a generic acknowledgement **without a session** and sends a single-use link to the entered email.
3. Click the link to `/auth?verify_email=…`, then **Activate Account** (POST, not a link-preview GET).
4. The backend atomically consumes the token and creates an active `user` role account; then the user can sign in with email/password.
5. Link expires after 24 hours. Resend is available from the check-inbox screen (60-second cooldown); older links become invalid. Active Directory login remains untouched.

Existing first-administrator onboarding uses the original bootstrap pathway; ordinary public email registrations always require verification.

## Required server-side email configuration
Use the existing secure runtime launcher/secret store; never put SMTP credentials in Git or user-facing config.

- `ABIT_SIGNUP_PUBLIC_URL=https://abit.ai` (HTTPS only; or configure `WEBUI_URL` to this URL).
- `ABIT_SIGNUP_SMTP_HOST` and `ABIT_SIGNUP_SMTP_FROM` (required).
- `ABIT_SIGNUP_SMTP_PORT=587` (default).
- `ABIT_SIGNUP_SMTP_STARTTLS=true` (default); alternatively set `ABIT_SIGNUP_SMTP_SSL=true` for port 465.
- `ABIT_SIGNUP_SMTP_USER` and `ABIT_SIGNUP_SMTP_PASSWORD` if SMTP authentication is required.

An SMTP provider with a verified sender domain and working SPF/DKIM/DMARC is recommended. If SMTP configuration is missing or delivery fails, new email signups fail closed with HTTP 503: no account/session is created.

## Controlled release
- **Do not enable public registration until SMTP is configured and an outbound test message is verified.** Enable Admin → General → Signups after setting SMTP and HTTPS URL.
- Back up live `webui.db` plus WAL/SHM and replaced source files; preserve process environment and LDAP.
- Apply Alembic migration `a6f3c2d9e1b0` during normal backend startup, deploy exact changed backend files and built frontend only, restart with the preserved runtime configuration, check `/health` and `/api/config`.
- End-to-end test using a **disposable** email inbox: signup → no login before email verification → mail receipt → activate → single-use token → sign in → refresh → new chat → LDAP sign in; then test expired/invalid links and resend.
- Accounts verified through this public email flow have the normal `user` role, never admin. Existing LDAP auto-registration policy is unchanged.

No live runtime or persistent account data is modified by committing the source changes.