import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';
import { defineConfig, normalizePath } from 'vite';
import { svelte, vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { viteStaticCopy } from 'vite-plugin-static-copy';

const projectRoot = fileURLToPath(new URL('.', import.meta.url));
const packageInfo = JSON.parse(readFileSync(resolve(projectRoot, 'package.json'), 'utf8'));

// Build only the developer module so a scoped release does not replace the
// application's currently deployed SvelteKit assets or live customizations.
export default defineConfig({
	root: resolve(projectRoot, 'src/developer'),
	base: '/developer-ui/',
	publicDir: false,
	cacheDir: resolve(projectRoot, 'node_modules/.vite-developers'),
	plugins: [
		svelte({ configFile: false, preprocess: vitePreprocess() }),
		viteStaticCopy({
			targets: [
				{
					src: normalizePath(resolve(projectRoot, 'static/abit-developers-navigation.js')),
					dest: '.',
					rename: 'navigation.js'
				}
			]
		})
	],
	resolve: {
		alias: [
			{ find: /^\$lib\/stores$/, replacement: resolve(projectRoot, 'src/developer/stores.ts') },
			{ find: /^\$lib\/utils$/, replacement: resolve(projectRoot, 'src/developer/clipboard.ts') },
			{ find: '$lib', replacement: resolve(projectRoot, 'src/lib') }
		]
	},
	define: {
		APP_VERSION: JSON.stringify(packageInfo.version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'developer-module')
	},
	css: { postcss: projectRoot },
	build: {
		outDir: resolve(projectRoot, 'build-developers'),
		emptyOutDir: true,
		copyPublicDir: false,
		sourcemap: false
	}
});
