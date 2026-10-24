import { WEBUI_API_BASE_URL } from '$lib/constants';

export type TelegramPairing = {
	id: string;
	status: 'pending' | 'claimed';
	expires_at: number;
	telegram_username?: string | null;
	display_name?: string | null;
};

export type TelegramStatus = {
	configured: boolean;
	bot_username: string | null;
	public_url: string | null;
	linked: boolean;
	binding: {
		id: string;
		telegram_username: string | null;
		display_name: string;
		linked_at: number;
	} | null;
	pairing: TelegramPairing | null;
	dot_connected: boolean;
	dot_subscription_active: boolean;
	mcp_url: string | null;
};

export type TelegramMessage = {
	id: string;
	text: string;
	created_at: number;
	status: string;
	response_text?: string | null;
	error?: string | null;
};

export type TelegramPairingResult = TelegramPairing & {
	deep_link: string;
	qr_data_url: string;
};

export class TelegramApiError extends Error {
	status: number;
	constructor(message: string, status: number) {
		super(message);
		this.name = 'TelegramApiError';
		this.status = status;
	}
}

async function request<T>(
	token: string,
	path: string,
	method = 'GET',
	body?: unknown,
	signal?: AbortSignal
): Promise<T> {
	const response = await fetch(`${WEBUI_API_BASE_URL}/telegram${path}`, {
		method,
		headers: {
			Authorization: `Bearer ${token}`,
			Accept: 'application/json',
			...(body === undefined ? {} : { 'Content-Type': 'application/json' })
		},
		body: body === undefined ? undefined : JSON.stringify(body),
		signal,
		cache: 'no-store'
	});
	const data = await response.json().catch(() => ({}));
	if (!response.ok) {
		const detail = typeof data.detail === 'string' ? data.detail : data.message;
		throw new TelegramApiError(detail || 'The Telegram connection could not be updated.', response.status);
	}
	return data as T;
}

export const getTelegramStatus = (token: string, signal?: AbortSignal) =>
	request<TelegramStatus>(token, '/status', 'GET', undefined, signal);

export const getTelegramMessages = (token: string, signal?: AbortSignal) =>
	request<{ messages: TelegramMessage[] }>(token, '/messages?limit=30', 'GET', undefined, signal);

export const configureTelegramBot = (
	token: string,
	config: { token: string; public_url: string; replace_existing_webhook?: boolean },
	signal?: AbortSignal
) => request<TelegramStatus>(token, '/config', 'POST', config, signal);

export const createTelegramPairing = (token: string, signal?: AbortSignal) =>
	request<TelegramPairingResult>(token, '/pairings', 'POST', {}, signal);

export const confirmTelegramPairing = (token: string, id: string, signal?: AbortSignal) =>
	request<TelegramStatus>(token, `/pairings/${encodeURIComponent(id)}/confirm`, 'POST', {}, signal);

export const cancelTelegramPairing = (token: string, id: string, signal?: AbortSignal) =>
	request<TelegramStatus>(token, `/pairings/${encodeURIComponent(id)}`, 'DELETE', undefined, signal);

export const unlinkTelegram = (token: string, signal?: AbortSignal) =>
	request<TelegramStatus>(token, '/link', 'DELETE', undefined, signal);

export const disconnectTelegramDot = (token: string, signal?: AbortSignal) =>
	request<TelegramStatus>(token, '/disconnect-dot', 'POST', {}, signal);
