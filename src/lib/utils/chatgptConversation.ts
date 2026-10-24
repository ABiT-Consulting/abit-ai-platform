/** Accept only direct ChatGPT conversation URLs; never a home page or arbitrary redirect. */
export const safeChatGPTConversationUrl = (value: unknown): string | null => {
	if (typeof value !== 'string' || /[\u0000-\u0020\\]/.test(value)) return null;
	try {
		const url = new URL(value);
		if (
			url.protocol !== 'https:' ||
			url.hostname !== 'chatgpt.com' ||
			url.port ||
			url.username ||
			url.password
		) return null;
		const path = url.pathname.replace(/\/$/, '');
		const conversation = '[A-Za-z0-9_-]{5,190}';
		const general = new RegExp(`^/c/${conversation}$`);
		const project = new RegExp(`^/g/g-p-[A-Za-z0-9_-]{5,370}/c/${conversation}$`);
		if (!general.test(path) && !project.test(path)) return null;
		return `https://chatgpt.com${path}`;
	} catch {
		return null;
	}
};
