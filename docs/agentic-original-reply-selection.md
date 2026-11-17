# Original Dot reply selection

When an assistant emits a governance contract in commentary and then returns its response, counting contracts across every assistant message makes the completed original appear unresolved. The reader now selects the response message first when the Dot UI supplies a positive reply reference to the uniquely verified original root.

Selection requires one exposed root-linked candidate, that candidate being the terminal nonempty message before the next user message, no declared branch or variant ambiguity, no active generation, and a stable message ID and exact response bytes across at least 900 ms. The read-only observer takes a second read after one second for this case. It returns exactly the selected message bytes; it never combines commentary with the response.

Without a positive terminal reply reference, existing cross-message ambiguity checks remain enforced. A wrong root reference, competing root-linked candidates, duplicate contracts within the selected message, hidden/inert content, an explicit alternate branch, changing IDs or bytes, or resumed generation prevents acceptance. Common packet identity still has to match. Core retains all business-result, evidence, publication, QA and ownership checks.

The existing owner-authenticated `/dot/handoff` and `/dot/observe` routes and their supported request/response contracts are unchanged. This changes response recognition, creates no execution or retry, and grants no authority. Recovery observes the existing original request; it never sends another prompt.

The shared relay source remains in the historical `deployment/windows/aibit-relay` directory. This release runs on Linux. The validated handoff helper is included as the observer's existing module dependency; its behavior is unchanged. Deployment must preserve valid live-only feed guards while applying these selection changes. The immutable-context-reference and outgoing-rejection guards already present in the live baseline are outside this commit.

Validation:

```sh
node --test deployment/windows/aibit-relay/tests/aibit_dot_feed.test.cjs \
  deployment/windows/aibit-relay/tests/aibit_dot_feed_binding.test.cjs \
  deployment/windows/aibit-relay/tests/aibit_dot_terminal_reply.test.cjs
ABIT_PYTHON=/path/to/approved/python npm run api:verify
```

All 93 Node tests and API verification passed. The HTML fixtures parse article/body/reply-reference markup with Cheerio and run the serialized production reader; style values are represented by fixture metadata. A retained private original confirmed exact root/message selection and byte preservation without becoming a committed fixture. Linux activation replaced only the two parser leaves, restarted only idle Node through the existing watchdog, and preserved the browser. Live same-original observation recovered the response without a new model run. Task business completion remains a separate ERP decision.
