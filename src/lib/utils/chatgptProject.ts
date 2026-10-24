export const CHATGPT_RELAY_MODEL_ID = 'omniroute.abit-chatgpt-relay/project-management';

export const isSyncedChatGPTProject = (folder: unknown): boolean => {
	if (!folder || typeof folder !== 'object') return false;
	const meta = (folder as { meta?: Record<string, unknown> }).meta;
	return typeof meta?.chatgpt_project_id === 'string' && meta.chatgpt_project_id.startsWith('g-p-');
};

// Keep the same request ID across network failures and page refreshes without
// storing project instructions. Successful requests release their retry key.
export const projectCreationRequest = async (owner: string, name: string, instructions: string) => {
	const bytes = new TextEncoder().encode(JSON.stringify([owner, name.trim(), instructions.trim()]));
	const digest = await crypto.subtle.digest('SHA-256', bytes);
	const key =
		'aibit-project-create:' +
		Array.from(new Uint8Array(digest), (n) => n.toString(16).padStart(2, '0')).join('');
	let requestId = sessionStorage.getItem(key);
	if (!requestId || !/^[a-f0-9-]{36}$/i.test(requestId)) {
		requestId = crypto.randomUUID();
		sessionStorage.setItem(key, requestId);
	}
	return { key, requestId };
};

type ProjectFolder = { id: string; parent_id?: string | null; meta?: Record<string, unknown> };

// Keep other accounts' folders on the server, but never mix their projects in
// the active account's sidebar. Ordinary local folders remain unchanged.
export const filterProjectFoldersForAccount = <T extends ProjectFolder>(
	rows: T[],
	account?: string
): T[] => {
	if (!account) return rows;
	const hidden = new Set(
		rows
			.filter((row) => isSyncedChatGPTProject(row) && row.meta?.chatgpt_account !== account)
			.map((row) => row.id)
	);
	let changed = true;
	while (changed) {
		changed = false;
		for (const row of rows) {
			if (row.parent_id && hidden.has(row.parent_id) && !hidden.has(row.id)) {
				hidden.add(row.id);
				changed = true;
			}
		}
	}
	return rows.filter((row) => !hidden.has(row.id));
};
