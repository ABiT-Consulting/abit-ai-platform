# ABiT AI Google account registration

The public Linux application at https://abit.ai already supports Google OAuth in Open WebUI.
This deployment helper activates the native Google provider without modifying chats,
users, projects, AD/LDAP, passwords, or the production database.

## Required Google Cloud OAuth client

Use Google Cloud Console > Google Auth Platform. Configure a **Web application**
OAuth client with these **exact** values:

- Authorized JavaScript origin: `https://abit.ai`
- Authorized redirect URI: `https://abit.ai/oauth/google/callback`
- Scope: `openid email profile`
- Publish the consent screen for the intended audience (External / production for
  public Google signups, or Internal for a Google Workspace-only deployment).

Do not save client credentials in Git or in chat. Create an on-server JSON file,
outside a repository, containing `client_id` and `client_secret`, chmod 0600.

## Activation (only after credentials exist)

```bash
python3 /srv/abit-dev-linux/abit-ai/deployment/google-signup-20261112/activate.py --check
sudo -n python3 /srv/abit-dev-linux/abit-ai/deployment/google-signup-20261112/activate.py --apply /path/to/private-google-oauth-credentials.json
```

The command checks host identity and baseline health, makes a consistent SQLite
backup, writes a root-only secret EnvironmentFile, creates a dedicated systemd
drop-in, safely restarts the existing service, and verifies public Google OAuth
registration/redirect. It rolls back service configuration on verification failure.

- Password and AD/LDAP sign-in remain enabled.
- Existing accounts are **not** automatically merged by email (avoids takeover).
- Newly created accounts inherit the existing default role **Pending**.
- The user must still do one real Google consent/callback and admin-approval test.
- Verification: `https://abit.ai/api/config` must advertise `oauth.providers.google`;
  clicking Google on the sign-in page must reach Google's consent screen.

Tests: `python3 -m unittest -v test_activate.py`.