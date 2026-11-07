export async function copyToClipboard(text: string): Promise<void> {
	if (!navigator.clipboard?.writeText) {
		throw new Error('Clipboard access is unavailable.');
	}
	await navigator.clipboard.writeText(text);
}
