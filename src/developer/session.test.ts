import { afterEach, describe, expect, it, vi } from 'vitest';
import { getDeveloperSession, readSessionToken } from './session';
import { getDeveloperMetadata, getDeveloperSchema, getDeveloperPostman } from '$lib/apis/developer';

afterEach(() => {
	vi.unstubAllGlobals();
});

const respond = (body: unknown, status = 200) => {
	const fetch = vi.fn().mockImplementation(
		async () =>
			new Response(JSON.stringify(body), {
				status,
				headers: { 'Content-Type': 'application/json' }
			})
	);
	vi.stubGlobal('fetch', fetch);
	return fetch;
};

describe('developer module session guard', () => {
	it.each(['admin', 'user'])('opens documentation for approved %s sessions', async (role) => {
		respond({ role });
		expect(await getDeveloperSession('example-session')).toBe('active');
	});

	it('keeps pending accounts outside the documentation view', async () => {
		respond({ role: 'pending' });
		expect(await getDeveloperSession()).toBe('pending');
	});

	it('requests sign-in only when the server rejects the session', async () => {
		respond({ detail: 'Unauthorized' }, 401);
		expect(await getDeveloperSession()).toBe('sign-in');
	});

	it.each([403, 500])('reports HTTP %s instead of redirecting repeatedly', async (status) => {
		respond({ detail: 'Unavailable' }, status);
		await expect(getDeveloperSession()).rejects.toThrow('HTTP ' + status);
	});

	it('uses the current origin cookies without a fabricated bearer token', async () => {
		const fetch = respond({ role: 'user' });
		await getDeveloperSession();
		expect(fetch).toHaveBeenCalledWith(
			'/api/v1/auths/',
			expect.objectContaining({
				credentials: 'same-origin',
				cache: 'no-store',
				headers: { Accept: 'application/json' }
			})
		);
	});

	it('keeps session credentials in the authorization header', async () => {
		const fetch = respond({ role: 'admin' });
		await getDeveloperSession('example-session');
		expect(fetch).toHaveBeenCalledWith(
			'/api/v1/auths/',
			expect.objectContaining({
				headers: { Accept: 'application/json', Authorization: 'Bearer example-session' }
			})
		);
	});

	it('continues with cookie authentication when browser storage is unavailable', () => {
		vi.stubGlobal('localStorage', {
			getItem: () => {
				throw new Error('Storage blocked');
			}
		});
		expect(readSessionToken()).toBeUndefined();
	});
});

describe('developer API requests', () => {
	it('loads metadata using existing cookies when no bearer token is stored', async () => {
		const fetch = respond({ counts: { operations: 3 } });
		expect(await getDeveloperMetadata()).toEqual({ counts: { operations: 3 } });
		expect(fetch).toHaveBeenCalledWith(
			'/api/v1/developer/',
			expect.objectContaining({
				headers: { Accept: 'application/json' },
				credentials: 'same-origin',
				cache: 'no-store'
			})
		);
	});

	it('keeps export requests on the same origin with the supplied session credential', async () => {
		const fetch = respond({ paths: {} });
		await getDeveloperSchema('example-session');
		await getDeveloperPostman('example-session');
		for (const [url, options] of fetch.mock.calls) {
			expect(url).toMatch(/^\/api\/v1\/developer\/(openapi|postman)\.json$/);
			expect(options.headers.Authorization).toBe('Bearer example-session');
			expect(options.credentials).toBe('same-origin');
		}
	});

	it('reports rejected documentation requests without returning their body as a schema', async () => {
		respond({ detail: 'Not authorized' }, 403);
		await expect(getDeveloperSchema()).rejects.toThrow('Not authorized');
	});

	it('shows a useful status when an error detail is not a string', async () => {
		respond({ detail: [{ message: 'invalid' }] }, 422);
		await expect(getDeveloperMetadata()).rejects.toThrow('HTTP 422');
	});
});
