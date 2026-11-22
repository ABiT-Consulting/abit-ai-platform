"""Read-only admission contract; it never authorizes execution or recovery."""
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class AutomationAdmissionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["ready", "waiting", "blocked"]
    code: str
    reason: str
    gate_state: Optional[str] = None
    gate_epoch: Optional[int] = None
    operator_owner: Optional[str] = None
    operator_action: Optional[str] = None
    operator_command_id: Optional[str] = None
    lease_id: Optional[str] = None
    lease_state: Optional[str] = None
    original_run_id: Optional[str] = None
    original_chat_url: Optional[str] = None
    next_action: str
    evidence: str
    denial_observed: bool = False
