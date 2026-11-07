export type DeveloperSessionState = 'active' | 'sign-in' | 'pending';

export { getDeveloperToken as readSessionToken } from '$lib/apis/developer';

export async function getDeveloperSession(
	token?: string,
	signal?: AbortSignal
): Promise<DeveloperSessionState> {
	const response = await fetch('/api/v1/auths/', {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			...(token ? { Authorization: 'Bearer ' + token } : {})
		},
		credentials: 'same-origin',
		cache: 'no-store',
		signal
	});
	if (response.status === 401) return 'sign-in';
	if (!response.ok) {
		throw new Error('Unable to verify your session (HTTP ' + response.status + ').');
	}
	const session = await response.json();
	return session?.role === 'admin' || session?.role === 'user' ? 'active' : 'pending';
}
