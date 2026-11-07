import { WEBUI_API_BASE_URL } from '$lib/constants';
import type { OpenApiSchema } from '$lib/utils/developer';

export interface DeveloperMetadata {
	title: string;
	version: string;
	role: string;
	base_url: string;
	authentication: {
		api_keys_enabled: boolean;
		can_manage_api_key: boolean;
		endpoint_restrictions: boolean;
		allowed_endpoints: string[];
		api_key_path: string;
	};
	counts: { operations: number; paths: number; groups: number };
	groups: { id: string; label: string; count: number }[];
	realtime?: Record<string, unknown>;
	[key: string]: unknown;
}

async function request<T>(token: string, path: string): Promise<T> {
	const response = await fetch(WEBUI_API_BASE_URL + '/developer/' + path, {
		method: 'GET',
		headers: { Accept: 'application/json', Authorization: 'Bearer ' + token },
		cache: 'no-store'
	});
	if (!response.ok) {
		const data = await response.json().catch(() => ({}));
		throw new Error(
			typeof data.detail === 'string'
				? data.detail
				: 'Unable to load API documentation (HTTP ' + response.status + ').'
		);
	}
	return response.json();
}

export const getDeveloperMetadata = (token: string) => request<DeveloperMetadata>(token, '');
export const getDeveloperSchema = (token: string) => request<OpenApiSchema>(token, 'openapi.json');
export const getDeveloperPostman = (token: string) =>
	request<Record<string, any>>(token, 'postman.json');
