# ChatGPT Project relay: required tool isolation

Reviewed against official OpenAI documentation on 13 November 2026. This records
an unresolved runtime dependency, not a deployed scoped gateway or successful
provider test. See tracker issue19.

## Observed current transport

The existing owner relay submits owner/account/job/message to a browser chat.
Its browser context, HTTPS egress tunnel and project instructions do not restrict
the connected tools a model can reach. It has no actual `/dot/scope` enforcement
endpoint. The new executor therefore rejects real governed model submission;
returning proof booleans without an enforced proxy is not an implementation.

## Supported boundaries

- A [Project](https://learn.chatgpt.com/docs/projects) groups files, instructions
  and chats. A separate Project/chat does not establish a documented app allowlist.
- A personal dot uses its owner's authorized app connections; see the
  [dots administration guide](https://learn.chatgpt.com/docs/enterprise/dots-admin-guide).
- [Workspace/member app and action controls](https://learn.chatgpt.com/docs/enterprise/apps-and-connectors)
  can restrict available connections. Effective grants must be tested for the
  intended restricted member, including cumulative role grants.
- Alternative browser/computer/password-manager access also needs explicit
  restriction; see [dots security](https://learn.chatgpt.com/docs/enterprise/dots-security).
- API [allowed-tools configuration](https://developers.openai.com/api/docs/guides/function-calling)
  is not evidence that the ChatGPT browser/dot transport accepts those parameters.
  This implementation does not silently substitute the API for the requested relay.

## Required production integration

1. Select a dedicated restricted ChatGPT principal or isolated workspace with
   only the approved scoped ABiT gateway connection available. Do not remove
   unrelated tools from the owner's normal account as a side effect.
2. Connect a real authenticated MCP gateway. ChatGPT custom apps use OAuth
   authorization-code/PKCE; verify token issuer, audience, expiry and scopes on
   every request. Customer API keys, client-credentials grants and arbitrary
   client certificates are not substitutes. See
   [authentication](https://developers.openai.com/plugins/build/auth).
3. Bind the authenticated gateway session to an independently verified original
   invocation outside model-supplied arguments. Project/Task/role/repository,
   environment, current lease and allowed operations come from server records.
   Reject unbound sessions, caller-selected identities and all other scopes.
4. Enforce that binding on every tool/read/write and delegated operation, with
   native ERP vetoes and exact one-use approvals. Secrets remain behind the
   gateway. A repository/command executor also needs an actual isolated runtime;
   an owner-wide shell or connector is not a scoped implementation.
5. Verify one approved disposable operation and explicit cross-project,
   wrong-role, revoked-lease, forbidden-operation and alternate-tool denials.
   Preserve receiving-service receipts and administrator configuration evidence.
   Only then may the relay expose a fresh, truthful protocol-v1 scope proof.

Client metadata can include `openai/session`, `openai/subject` and organization
correlation. These are not signed ERP authority. See the
[MCP reference](https://developers.openai.com/plugins/reference#_meta-fields-the-client-provides).
Connection-level OAuth alone does not bind a chat to an ERP job. No job ID,
permission flag, declared tool list or secret pasted into the model prompt may
replace the required server binding and actual tool enforcement.

## Current release state

The source verifier, native ERP/control/evidence flow and controlled-provider
canary can be tested independently. Actual scoped ChatGPT gateway integration,
restricted-principal configuration and real-provider acceptance remain pending.
No new account, app connection, OAuth grant, permission or provider request was
created during this documentation review. Hosted-CI waiver does not waive this
boundary. The normal owner relay and legacy work remain preserved.
