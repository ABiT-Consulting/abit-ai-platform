# Server Script Type: API
# API Method: abit_erp_agent_control
# Do not allow Guest access. Uses the existing Frappe Webhook secret server side.
task_id = str(frappe.form_dict.get("task_id") or "").strip()
action = str(frappe.form_dict.get("action") or "status").strip()
valid_task = (task_id.startswith("TASK-") and 5 <= len(task_id) <= 70
              and all(ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-" for ch in task_id))
if not valid_task or action not in ("status", "start", "stop", "restart"):
    frappe.throw("Invalid Task or agent action")

if frappe.session.user == "Guest":
    frappe.throw("Login required to control ABIT.AI agents", frappe.PermissionError)

task_doc = frappe.get_doc("Task", task_id)
task_doc.check_permission("read")
if action != "status":
    task_doc.check_permission("write")

# Pre-existing ERPNext webhook holds the same signed integration credential used
# by the ABIT.AI task assignment endpoint. Never send this credential to the UI.
integration = frappe.get_doc("Webhook", "ABiT Agent TASK-2026-05593 after_insert")
secret = integration.get_password("webhook_secret")
if not secret:
    frappe.throw("ABIT.AI control credential unavailable")

try:
    result = frappe.make_post_request(
        "https://abit.ai/api/v1/erp-task-agent-control/" + task_id,
        headers={"Content-Type": "application/json", "X-ABIT-ERP-Control": secret},
        data='{"action":"' + action + '"}'
    )
except Exception:
    frappe.throw("ABIT.AI agent service unavailable or rejected the request")

if not isinstance(result, dict) or not isinstance(result.get("status"), str):
    frappe.throw("ABIT.AI returned an invalid agent status")
frappe.response["message"] = result
