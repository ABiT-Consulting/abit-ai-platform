import { mount } from 'svelte';
import App from './App.svelte';
import '../tailwind.css';
import '../app.css';

// Respect the existing application's appearance preference without changing it.
let preference = 'system';
try {
	preference = localStorage.getItem('theme') || 'system';
} catch {
	// Cookie authentication still works if browser storage is unavailable.
}
const dark =
	preference === 'system'
		? window.matchMedia('(prefers-color-scheme: dark)').matches
		: !['light', 'her'].includes(preference);
document.documentElement.classList.add(preference === 'her' ? 'her' : dark ? 'dark' : 'light');
if (preference === 'oled-dark') {
	for (const [name, value] of Object.entries({
		'--color-gray-800': '#101010',
		'--color-gray-850': '#050505',
		'--color-gray-900': '#000000',
		'--color-gray-950': '#000000'
	}))
		document.documentElement.style.setProperty(name, value);
}
document
	.querySelector('meta[name="theme-color"]')
	?.setAttribute('content', preference === 'her' ? '#983724' : dark ? '#171717' : '#ffffff');

const target = document.getElementById('developer-app');
if (!target) throw new Error('Developer module mount point is missing.');
mount(App, { target });
