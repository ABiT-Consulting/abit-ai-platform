# ERPNext Task intake for ABiT AI

ERPNext sends signed Task and ToDo events to `POST /api/v1/automations/erp/assignment`. The receiver authenticates the raw body and stores the event before returning. Task events can carry an event ID and ERP revision for deduplication. Older signed events that contain only the task ID remain supported.

Create ToDo webhooks for `after_insert` and `on_update` with the following condition.

```python
doc.reference_type == "Task" and doc.allocated_to == "ai-developer@abitcons.com" and doc.status == "Open"
```

Use the JSON body `{"task_id":"{{ doc.reference_name }}"}`. For Task creation and update events, use the following body.

```json
{"task_id":"{{ doc.name }}","event_id":"{{ doc.name }}:{{ doc.modified|string|replace(' ', 'T') }}","erp_revision":"{{ doc.modified }}"}
```

Enable native Frappe webhook security and configure the same protected signing secret on both systems. ERP Task requires a visible `custom_abit_ai_agent_url` field with the Data field type and URL option. Its status choices must include Working, Pending Review, Completed and Failed.

Set `ABIT_ERP_TASK_RESOLUTION_CONFIG` to the protected resolver JSON file on ABiT AI. Configure `erp_token_file`, `webhook_secret_file`, `owner_email`, `projects`, and the existing `standard_agent_creator` setting to enable Governance scheduling. This setting selects the scheduler. The actual intake gate is an open assignment to AI Developer, regardless of who created the Task. Credentials and site-specific project mappings belong in protected configuration.

The scheduler creates one agent per eligible task and reuses it when the task is reopened. The existing Governance scheduler executes the task using ChatGPT Browser Relay and the configured Codex CLI Linux secondary model. The server-side ERP client owns the agent link and status updates. An agent can execute using available ERP context without an ABiT AI project folder. A successful Governance response is recorded as Pending Review. It does not establish that work passed independent verification.

The legacy managed-run adapters are retained for existing history and deployments that use that mode. For such a deployment, set `ABIT_ERP_WORKER_USER` to the verified Linux account on both the resolver and the worker services. Include it in the environment of the Linux bridge and its child units. Configure repository and verifier mappings in private resolver and worker settings. Specify any task-specific DEV delivery checks in the private `delivery_tasks` map. A trusted publisher must sign delivery evidence that identifies the tested Git candidate and target.

Run `npm run api:verify`, `npm run api:verify:integration`, the ERP resolver tests and the relay worker tests before merging changes.
