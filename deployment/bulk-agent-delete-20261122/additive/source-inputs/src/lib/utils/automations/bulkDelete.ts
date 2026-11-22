export type BulkAgentTarget = {
	id: string;
	name: string;
	disabled?: boolean;
};

export type BulkAgentDeleteResult = {
	deletedIds: string[];
	failures: (BulkAgentTarget & { error: string })[];
};

// The API returns a boolean. A resolved request alone is not proof of deletion.
export async function deleteAgentsSequentially(
	targets: readonly BulkAgentTarget[],
	remove: (id: string) => Promise<unknown>,
	options: {
		canDelete?: (id: string) => boolean;
		onProgress?: (completed: number) => void;
		shouldStop?: () => boolean;
	} = {}
): Promise<BulkAgentDeleteResult> {
	const result: BulkAgentDeleteResult = { deletedIds: [], failures: [] };
	const uniqueTargets = [...new Map(targets.map((target) => [target.id, { ...target }])).values()];
	for (const [index, target] of uniqueTargets.entries()) {
		if (options.shouldStop?.()) break;
		try {
			if (target.disabled || (options.canDelete && !options.canDelete(target.id))) {
				throw new Error('This agent is no longer available for deletion. Refresh and review it.');
			}
			if ((await remove(target.id)) !== true) {
				throw new Error('Deletion was not confirmed. Refresh before retrying.');
			}
			result.deletedIds.push(target.id);
		} catch (error) {
			result.failures.push({
				...target,
				error:
					error instanceof Error
						? error.message
						: typeof error === 'string'
							? error
							: 'Unable to delete this agent. Refresh before retrying.'
			});
		}
		options.onProgress?.(index + 1);
	}
	return result;
}

export function isAgentKnownRunning(agent: {
	last_run?: { status?: string } | null;
	meta?: Record<string, any> | null;
}): boolean {
	return (
		['running', 'handoff'].includes(agent.last_run?.status ?? '') ||
		agent.meta?._agent_governance?.control?.state === 'running'
	);
}
