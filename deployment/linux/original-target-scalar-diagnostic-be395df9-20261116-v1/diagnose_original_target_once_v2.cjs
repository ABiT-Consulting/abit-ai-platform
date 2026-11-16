#!/usr/local/bin/node
'use strict';
// One original-target diagnostic: GET /json/list, then one constant scalar.
// No auth/account/DOM read, Page/Runtime enable, lifecycle change, navigation,
// target allocation, Send/Stop, capture callback, ownership write or recovery.
const fs = require('node:fs');
const http = require('node:http');
const crypto = require('node:crypto');
const TARGET = 'CFE8FB94A61EE88B229F3A1D7EA3A96D';
const THREAD = 'f451ca1bc0c4cb367e4b3438a78f9cb5b9678702';
const CHROME_PID = 1074156;
const CHROME_START = '77042475';
const PORT = 9228;
const REGISTRY = '/srv/abit-dev-linux/relay-runtime/state/chat-thread-targets.json';
const OUT = '/var/lib/abit-ai-original-target-scalar-be395df9-20261116-v1';
const FRAME = {id: 1, method: 'Runtime.evaluate', params: {
  expression: '1+1', returnByValue: true, awaitPromise: false,
  userGesture: false, throwOnSideEffect: true, silent: true,
  allowUnsafeEvalBlockedByCSP: false
}};
// Deliberately no protocol timeout or termination command: only a client deadline.
let request = null, websocket = null;
const result = {http_attempts: 0, cdp_command_attempts: 0, cdp_commands_sent: 0, automatic_retry: false};
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
function fail(code) { const error = new Error(); error.safeCode = code; throw error; }
function requireThat(ok, code) { if (!ok) fail(code); }
function readRegular(file, limit) {
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const before = fs.fstatSync(fd);
    requireThat(before.isFile() && before.size <= limit, 'file_bounds');
    const bytes = fs.readFileSync(fd), after = fs.fstatSync(fd);
    requireThat(bytes.length <= limit && before.ino === after.ino &&
      before.size === after.size && before.mtimeMs === after.mtimeMs, 'file_changed');
    return bytes;
  } finally { fs.closeSync(fd); }
}
function guards() {
  const processStat = fs.readFileSync('/proc/' + CHROME_PID + '/stat', 'utf8');
  const fields = processStat.slice(processStat.lastIndexOf(')') + 1).trim().split(/\s+/);
  requireThat(fields[19] === CHROME_START, 'chrome_cohort_changed');
  const inodes = new Set();
  for (const name of fs.readdirSync('/proc/' + CHROME_PID + '/fd')) {
    try {
      const link = fs.readlinkSync('/proc/' + CHROME_PID + '/fd/' + name);
      if (/^socket:\[\d+\]$/.test(link)) inodes.add(link.slice(8, -1));
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  const endpoint = '0100007F:' + PORT.toString(16).toUpperCase().padStart(4, '0');
  const sockets = fs.readFileSync('/proc/net/tcp', 'utf8').trim().split('\n').slice(1);
  requireThat(sockets.some(line => {
    const row = line.trim().split(/\s+/);
    return row[1] === endpoint && row[3] === '0A' && inodes.has(row[9]);
  }), 'exact_chrome_listener_missing');
  const registry = JSON.parse(readRegular(REGISTRY, 1024 * 1024));
  const row = registry[THREAD];
  requireThat(row && row.id === TARGET && row.touched === 1794843406191 &&
    Object.keys(row).length === 2, 'original_target_mapping_changed');
  return {chrome_pid: CHROME_PID, chrome_start_ticks: CHROME_START,
    loopback_port: PORT, selected_target_id: TARGET, mapping_matches: true};
}
function save(name, value) {
  const fd = fs.openSync(OUT + '/' + name,
    fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(value) + '\n'); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
}
function discover() {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error, value) => {
      if (settled) return; settled = true; clearTimeout(timer);
      if (error) reject(error); else resolve(value);
    };
    const timer = setTimeout(() => {
      request?.destroy(); finish(Object.assign(new Error(), {safeCode: 'target_discovery_timeout'}));
    }, 2500);
    result.http_attempts = 1;
    request = http.get({hostname: '127.0.0.1', port: PORT, path: '/json/list',
      agent: false, headers: {Accept: 'application/json', Connection: 'close'}}, response => {
      result.discovery_http_status = response.statusCode;
      if (response.statusCode !== 200) {
        response.destroy(); finish(Object.assign(new Error(), {safeCode: 'target_discovery_http_error'})); return;
      }
      let size = 0; const chunks = [];
      response.on('data', chunk => {
        size += chunk.length;
        if (size > 262144) {
          response.destroy(); finish(Object.assign(new Error(), {safeCode: 'target_discovery_body_limit'}));
        } else chunks.push(chunk);
      });
      response.on('error', () => finish(Object.assign(new Error(), {safeCode: 'target_discovery_body_error'})));
      response.on('end', () => {
        try {
          const targets = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          requireThat(Array.isArray(targets), 'target_list_invalid');
          const selected = targets.filter(target => target && target.id === TARGET);
          requireThat(selected.length === 1 && selected[0].type === 'page', 'original_page_not_found');
          result.selected_target = {id: TARGET, type: 'page', websocket_endpoint_available: false};
          const url = new URL(selected[0].webSocketDebuggerUrl);
          requireThat(url.protocol === 'ws:' && url.hostname === '127.0.0.1' &&
            url.port === String(PORT) && url.pathname === '/devtools/page/' + TARGET &&
            !url.username && !url.password && !url.search && !url.hash, 'original_endpoint_invalid');
          result.selected_target.websocket_endpoint_available = true;
          finish(null, url.href);
        } catch (error) { finish(error); }
      });
    });
    request.on('error', () => finish(Object.assign(new Error(), {safeCode: 'target_discovery_connection_error'})));
  });
}
function scalar(endpoint) {
  return new Promise((resolve, reject) => {
    let settled = false, sentAt = null;
    const finish = (error, value) => {
      if (settled) return; settled = true; clearTimeout(timer);
      try { websocket.close(); } catch (_) {}
      if (error) reject(error); else resolve(value);
    };
    let timer = setTimeout(() => finish(Object.assign(new Error(),
      {safeCode: 'existing_target_handshake_timeout'})), 2000);
    websocket = new WebSocket(endpoint);
    websocket.onopen = () => {
      if (settled) return;
      if (websocket.readyState !== WebSocket.OPEN) {
        finish(Object.assign(new Error(), {safeCode: 'scalar_socket_not_open'})); return;
      }
      result.websocket_opened = true;
      clearTimeout(timer); sentAt = performance.now();
      timer = setTimeout(() => finish(Object.assign(new Error(),
        {safeCode: 'scalar_response_timeout'})), 2000);
      result.cdp_command_attempts = 1;
      try { websocket.send(JSON.stringify(FRAME)); result.cdp_commands_sent = 1; }
      catch (_) { finish(Object.assign(new Error(), {safeCode: 'scalar_send_failed'})); }
    };
    websocket.onerror = () => finish(Object.assign(new Error(), {safeCode: 'existing_target_socket_error'}));
    websocket.onclose = () => finish(Object.assign(new Error(), {safeCode: 'existing_target_socket_closed'}));
    websocket.onmessage = event => {
      if (settled) return;
      if (typeof event.data !== 'string' || event.data.length > 65536) {
        finish(Object.assign(new Error(), {safeCode: 'cdp_response_bound'})); return;
      }
      let message;
      try { message = JSON.parse(event.data); } catch (_) {
        finish(Object.assign(new Error(), {safeCode: 'cdp_response_invalid'})); return;
      }
      if (!message || typeof message !== 'object' || Array.isArray(message)) {
        finish(Object.assign(new Error(), {safeCode: 'cdp_response_invalid'})); return;
      }
      if (message.id !== 1) return;  // Ignore unsolicited events; never serialize them.
      const reply = message.result;
      finish(null, {scalar_response_ms: Math.round(performance.now() - sentAt),
        scalar_response_received: true, command_error: Boolean(message.error),
        exception_present: Boolean(reply?.exceptionDetails),
        expected_scalar: !message.error && !reply?.exceptionDetails &&
          reply?.result?.type === 'number' && reply.result.value === 2});
    };
  });
}
async function main() {
  requireThat(process.getuid() === 0, 'root_required');
  process.umask(0o077);
  const mode = process.argv.slice(2);
  requireThat(mode.length === 1 && ['--check', '--execute-reviewed-once'].includes(mode[0]), 'explicit_mode_required');
  const cohort = guards();
  const controllerSha = sha(readRegular(__filename, 65536));
  if (mode[0] === '--check') {
    console.log(JSON.stringify({mode: 'metadata_only', controller_sha256: controllerSha, ...cohort, ...result}));
    return 0;
  }
  fs.mkdirSync(OUT, {mode: 0o700}); // Existing directory refuses a second attempt.
  const dir = fs.lstatSync(OUT);
  requireThat(dir.uid === 0 && (dir.mode & 0o777) === 0o700, 'private_directory_mode');
  save('intent.json', {created_utc: new Date().toISOString(), controller_sha256: controllerSha,
    ...cohort, method: 'GET /json/list then one Runtime.evaluate', expression: '1+1',
    client_deadline_ms: 8000, automatic_retry: false});
  const directory = fs.openSync(OUT, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY);
  try { fs.fsyncSync(directory); } finally { fs.closeSync(directory); }
  result.started_utc = new Date().toISOString();
  let overall;
  try {
    const work = async () => {
      guards();
      const endpoint = await discover();
      guards();
      return scalar(endpoint);
    };
    const timeout = new Promise((_, reject) => {
      overall = setTimeout(() => reject(Object.assign(new Error(), {safeCode: 'diagnostic_deadline'})), 8000);
    });
    Object.assign(result, await Promise.race([work(), timeout]));
    result.classification = result.expected_scalar ? 'original_target_scalar_responded' : 'scalar_response_not_expected';
  } catch (error) {
    result.classification = 'diagnostic_unresolved_no_retry';
    result.failure_code = error.safeCode || 'unclassified_error';
  } finally {
    clearTimeout(overall); request?.destroy();
    try { websocket?.close(); } catch (_) {}
    result.finished_utc = new Date().toISOString();
    save('result.json', result);
  }
  console.log(JSON.stringify(result));
  return result.expected_scalar ? 0 : 3;
}
main().then(code => process.exit(code), error => {
  console.log(JSON.stringify({classification: 'diagnostic_failed_no_retry',
    failure_code: error.safeCode || 'unclassified_error'}));
  process.exit(2);
});
