import { writable } from 'svelte/store';
import { APP_NAME } from '$lib/constants';

// The embedded route uses the application's real stores. Only this isolated
// build aliases these four UI stores; authentication stays with the live API.
export const WEBUI_NAME = writable(APP_NAME);
export const mobile = writable(false);
export const showSidebar = writable(false);
const settings = writable<boolean | string>(false);
export const showSettings = {
	subscribe: settings.subscribe,
	set(value: boolean | string) {
		settings.set(value);
		if (value) {
			window.location.assign(
				'/?settings=' + encodeURIComponent(typeof value === 'string' ? value : 'general')
			);
		}
	}
};
