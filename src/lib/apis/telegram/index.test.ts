import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));

import {
	getTelegramStatus,
	getTelegramMessages,
	TelegramApiError
} from './index';

afterEach(() => vi.unstubAllGlobals());

describe('Telegram API response safety', () => {
	it('rejects the HTML SPA fallback when the Telegram backend is absent', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response('<!doctype html><title>abit.ai</title>', {
					status: 200,
					headers: { 'Content-Type': 'text/html' }
				})
			)
		);
		await expect(getTelegramStatus('test-token')).rejects.toMatchObject({
			name: 'TelegramApiError',
			status: 503
		});
		expect(fetch).toHaveBeenCalledWith(
			'/api/v1/telegram/status',
			expect.objectContaining({ cache: 'no-store' })
		);
	});

	it('rejects a partial or invalid status object', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(JSON.stringify({ configured: false }), {
					status: 200,
					headers: { 'Content-Type': 'application/json' }
				})
			)
		);
		await expect(getTelegramStatus('test-token')).rejects.toBeInstanceOf(TelegramApiError);
	});

	it('accepts a valid Telegram status and rejects a malformed message list', async () => {
		const valid = {
			configured: false, bot_username: null, public_url: null, linked: false,
			binding: null, pairing: null, dot_connected: false,
			dot_subscription_active: false, mcp_url: null
		};
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValueOnce(
				new Response(JSON.stringify(valid), {
					status: 200, headers: { 'Content-Type': 'application/json' }
				})
			).mockResolvedValueOnce(
				new Response(JSON.stringify({ messages: null }), {
					status: 200, headers: { 'Content-Type': 'application/json' }
				})
			)
		);
		await expect(getTelegramStatus('test-token')).resolves.toEqual(valid);
		await expect(getTelegramMessages('test-token')).rejects.toBeInstanceOf(TelegramApiError);
	});
});
