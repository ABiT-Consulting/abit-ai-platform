import { WEBUI_API_BASE_URL } from '$lib/constants';

export type AutomationAdmissionStatus = {
  state: 'ready' | 'waiting' | 'blocked';
  code: string;
  reason: string;
  gate_state: string | null;
  gate_epoch: number | null;
  operator_owner: string | null;
  operator_action: string | null;
  operator_command_id: string | null;
  lease_id: string | null;
  lease_state: string | null;
  original_run_id: string | null;
  original_chat_url: string | null;
  next_action: string;
  evidence: string;
  denial_observed: boolean;
};

export const getAutomationAdmission = async (
  token: string, id: string
): Promise<AutomationAdmissionStatus> => {
  const response = await fetch(
    `${WEBUI_API_BASE_URL}/automations/${encodeURIComponent(id)}/admission`,
    { headers: { Authorization: `Bearer ${token}` }, credentials: 'same-origin' }
  );
  if (!response.ok) {
    throw new Error('Admission status could not be verified. Preserve the original work.');
  }
  return await response.json();
};
