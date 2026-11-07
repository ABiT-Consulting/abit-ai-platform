#!/usr/bin/env node
/**
 * API checks shared by local builds, pull requests and release workflows.
 * No application configuration, live database, provider or credential is loaded.
 */
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const mode = process.argv[2] ?? '--check';
const modes = new Set(['--check', '--verify', '--integration', '--requirements', '--help']);
if (!modes.has(mode) || process.argv.length > 3) {
  console.error('Usage: node scripts/check-api.mjs [--check|--verify|--integration|--requirements]');
  process.exit(2);
}
if (mode === '--help') {
  console.log('check: source coverage; verify: coverage, API/UI tests and portal types; integration: isolated full application');
  process.exit(0);
}
if (mode === '--requirements') {
  // Follow the application's dependency pins without installing the ML stack.
  const needed = new Set(['fastapi', 'pydantic', 'httpx', 'python-multipart']);
  const pins = new Map();
  for (const line of readFileSync(join(root, 'backend/requirements.txt'), 'utf8').split(/\r?\n/)) {
    const match = line.match(/^([a-zA-Z0-9_-]+)(?:\[[^\]]+\])?(==[^\s#]+)/);
    if (match && needed.has(match[1].toLowerCase())) {
      pins.set(match[1].toLowerCase(), match[1].toLowerCase() + match[2]);
    }
  }
  if (pins.size !== needed.size) {
    console.error('API test dependencies must retain exact application pins.');
    process.exit(1);
  }
  console.log([...needed].map((name) => pins.get(name)).join('\n'));
  process.exit(0);
}

const candidates = process.env.ABIT_PYTHON
  ? [[process.env.ABIT_PYTHON, []]]
  : process.platform === 'win32'
    ? [['python', []], ['py', ['-3.11']], ['python3', []]]
    : [['python3', []], ['python', []]];
const python = candidates.find(([command, prefix]) =>
  spawnSync(command, [...prefix, '-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'],
    { cwd: root, stdio: 'ignore', shell: false }).status === 0
);
if (!python) {
  console.error('API checks require Python 3.11+. Set ABIT_PYTHON to the interpreter path if needed.');
  process.exit(1);
}
function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', shell: false });
  if (result.error || result.status !== 0) {
    if (result.error) console.error(result.error.message);
    process.exit(result.status || 1);
  }
}
function runPython(args) {
  run(python[0], [...python[1], ...args]);
}
runPython(['scripts/check_api_contract.py']);
if (mode === '--verify') {
  runPython(['-m', 'unittest', 'discover', '-s', 'scripts/tests', '-p', 'test_api*.py', '-v']);
  runPython(['-m', 'unittest', 'discover', '-s', 'backend/tests', '-p', 'test_developer*.py', '-v']);
  run(process.execPath, ['node_modules/vitest/vitest.mjs', 'run',
    'src/lib/utils/developer.test.ts', 'src/developer/session.test.ts']);
  run(process.execPath, ['node_modules/svelte-check/bin/svelte-check', '--tsconfig', 'tsconfig.developers.json']);
}
if (mode === '--integration') {
  runPython(['scripts/test_developer_module.py']);
}
console.log(mode === '--check' ? 'API source coverage check passed.' : 'API verification completed successfully.');
