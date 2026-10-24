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

export type TelegramBotHealth = {
	configured: boolean;
	webhook_matches: boolean;
	pending_update_count: number;
	last_error_date: number | null;
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

const API_UNAVAILABLE =
	'The Telegram service is not installed or enabled on this server. Contact the application administrator.';

async function request<T>(
	token: string,
	path: string,
	method = 'GET',
	body?: unknown,
	signal?: AbortSignal
): Promise<T> {
	const controller = new AbortController();
	const cancel = () => controller.abort();
	signal?.addEventListener('abort', cancel, { once: true });
	if (signal?.aborted) controller.abort();
	const timeout = setTimeout(cancel, 15000);
	try {
		const response = await fetch(`${WEBUI_API_BASE_URL}/telegram${path}`, {
			method,
			headers: {
				Authorization: `Bearer ${token}`,
				Accept: 'application/json',
				...(body === undefined ? {} : { 'Content-Type': 'application/json' })
			},
			body: body === undefined ? undefined : JSON.stringify(body),
			signal: controller.signal,
			cache: 'no-store'
		});
		// Open WebUI serves the SPA HTML for unknown API routes (HTTP 200).
		// Treat it as a missing backend, never as a successful Telegram status.
		if (!response.headers.get('content-type')?.toLowerCase().includes('application/json')) {
			throw new TelegramApiError(API_UNAVAILABLE, 503);
		}
		const data = await response.json().catch(() => {
			throw new TelegramApiError('The Telegram service returned invalid JSON.', 502);
		});
		if (!response.ok) {
			const detail = typeof data?.detail === 'string' ? data.detail : data?.message;
			throw new TelegramApiError(
				typeof detail === 'string' ? detail : 'The Telegram request failed.',
				response.status
			);
		}
		return data as T;
	} catch (failure) {
		if (failure instanceof TelegramApiError) throw failure;
		if (signal?.aborted) throw failure;
		if (controller.signal.aborted) {
			throw new TelegramApiError('The Telegram service did not respond within 15 seconds.', 504);
		}
		throw new TelegramApiError('Could not reach the Telegram service. Check your connection.', 503);
	} finally {
		clearTimeout(timeout);
		signal?.removeEventListener('abort', cancel);
	}
}

export async function getTelegramStatus(token: string, signal?: AbortSignal): Promise<TelegramStatus> {
	const result = await request<TelegramStatus>(token, '/status', 'GET', undefined, signal);
	if (
		!result ||
		typeof result.configured !== 'boolean' ||
		typeof result.linked !== 'boolean' ||
		typeof result.dot_connected !== 'boolean' ||
		typeof result.dot_subscription_active !== 'boolean'
	) {
		throw new TelegramApiError('The Telegram service returned an invalid connection status.', 502);
	}
	return result;
}

export async function getTelegramMessages(
	token: string,
	signal?: AbortSignal
): Promise<{ messages: TelegramMessage[] }> {
	const result = await request<{ messages: TelegramMessage[] }>(
		token,
		'/messages?limit=30',
		'GET',
		undefined,
		signal
	);
	if (!Array.isArray(result?.messages)) {
		throw new TelegramApiError('The Telegram service returned an invalid message list.', 502);
	}
	return result;
}

export const getTelegramBotHealth = (token: string, signal?: AbortSignal) =>
	request<TelegramBotHealth>(token, '/bot-health', 'GET', undefined, signal);

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
