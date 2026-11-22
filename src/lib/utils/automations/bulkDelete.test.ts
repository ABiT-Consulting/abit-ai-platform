import { describe, expect, it } from 'vitest';
import { deleteAgentsSequentially, isAgentKnownRunning } from './bulkDelete';

describe('bulk agent deletion results', () => {
	it('deletes only reviewed IDs once, serially, and requires explicit API confirmation', async () => {
		const calls: string[] = [];
		let active = 0;
		let maximum = 0;
		const result = await deleteAgentsSequentially(
			['a', 'b', 'a', 'c', 'd'].map((id) => ({ id, name: id })),
			async (id) => {
				calls.push(id);
				active += 1;
				maximum = Math.max(maximum, active);
				await Promise.resolve();
				active -= 1;
				return id === 'a' ? true : id === 'b' ? false : id === 'c' ? null : { success: true };
			}
		);
		expect(calls).toEqual(['a', 'b', 'c', 'd']);
		expect(maximum).toBe(1);
		expect(result.deletedIds).toEqual(['a']);
		expect(result.failures.map((item) => item.id)).toEqual(['b', 'c', 'd']);
	});

	it('continues after permission and transport failures without reporting them as deleted', async () => {
		const result = await deleteAgentsSequentially(
			['forbidden', 'offline', 'ok'].map((id) => ({ id, name: id })),
			async (id) => {
				if (id === 'forbidden') throw 'Access denied';
				if (id === 'offline') throw new Error('Network error');
				return true;
			}
		);
		expect(result.deletedIds).toEqual(['ok']);
		expect(result.failures.map((item) => item.error)).toEqual(['Access denied', 'Network error']);
	});

	it('rechecks eligibility so agents that become running are not deleted', async () => {
		const calls: string[] = [];
		let secondEligible = true;
		const result = await deleteAgentsSequentially(
			[{ id: 'a', name: 'First' }, { id: 'b', name: 'Started in background' }],
			async (id) => {
				calls.push(id);
				secondEligible = false;
				return true;
			},
			{ canDelete: (id) => id !== 'b' || secondEligible }
		);
		expect(calls).toEqual(['a']);
		expect(result.deletedIds).toEqual(['a']);
		expect(result.failures.map((item) => item.id)).toEqual(['b']);
	});

	it('stops unsent work when the owning view is destroyed', async () => {
		let stopped = false;
		const calls: string[] = [];
		const progress: number[] = [];
		const result = await deleteAgentsSequentially(
			['a', 'b'].map((id) => ({ id, name: id })),
			async (id) => { calls.push(id); stopped = true; return true; },
			{ shouldStop: () => stopped, onProgress: (count) => progress.push(count) }
		);
		expect(calls).toEqual(['a']);
		expect(result.deletedIds).toEqual(['a']);
		expect(progress).toEqual([1]);
	});

	it('uses saved handoff/control signals conservatively in the management list', () => {
		expect(isAgentKnownRunning({ last_run: { status: 'handoff' } })).toBe(true);
		expect(isAgentKnownRunning({ last_run: { status: 'running' } })).toBe(true);
		expect(isAgentKnownRunning({ meta: { _agent_governance: { control: { state: 'running' } } } })).toBe(true);
		expect(isAgentKnownRunning({ last_run: { status: 'completed' } })).toBe(false);
		expect(isAgentKnownRunning({ last_run: null, meta: null })).toBe(false);
	});
});
