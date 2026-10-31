'use strict';
// Local-only HTTPS CONNECT transport for the isolated ABiT Linux Chrome.
// Uses explicit public DNS resolvers without changing AD/DNS or system settings.
// Never accepts HTTP requests, raw IPs, non-HTTPS ports, or private destinations.
const http = require('node:http');
const net = require('node:net');
const dns = require('node:dns').promises;
const resolver = new dns.Resolver();
resolver.setServers(['1.1.1.1', '8.8.8.8']);
const PORT = Number(process.env.ABIT_BROWSER_EGRESS_PORT || 8812);
const HOST = '127.0.0.1';
const MAX_ACTIVE = 96;
const connections = new Set();
const isPublicIPv4 = (ip) => {
  if (net.isIP(ip) !== 4) return false;
  const [a, b, c] = ip.split('.').map(Number);
  if (a === 0 || a === 10 || a === 127 || a >= 224 ||
      a === 169 && b === 254 ||
      a === 172 && b >= 16 && b <= 31 ||
      a === 192 && b === 168 ||
      a === 100 && b >= 64 && b <= 127 ||
      a === 198 && (b === 18 || b === 19) ||
      a === 192 && b === 0 && c === 0 ||
      a === 192 && b === 0 && c === 2 ||
      a === 198 && b === 51 && c === 100 ||
      a === 203 && b === 0 && c === 113) return false;
  return true;
};
function reject(sock, status) {
  if (!sock.destroyed) sock.end('HTTP/1.1 ' + status + '\r\nConnection: close\r\n\r\n');
}
const server = http.createServer((req, res) => {
  res.writeHead(405, { 'Cache-Control':'no-store', 'Connection':'close' }); res.end();
});
server.on('connect', async (req, downstream, head) => {
  let remote = null;
  try {
    if (!req.url || connections.size >= MAX_ACTIVE) return reject(downstream, '503 Service Unavailable');
    const [host, port, ...extra] = req.url.split(':');
    const validHost = /^[A-Za-z][A-Za-z0-9.-]{1,250}$/.test(host || '') &&
      host.includes('.') && !host.includes('..') && !host.endsWith('.') &&
      net.isIP(host) === 0 &&
      !/(?:^|\.)(?:localhost|local|lan|internal|corp)$/.test(host) &&
      !host.toLowerCase().endsWith('.abitcons.com');
    if (!validHost || port !== '443' || extra.length > 0) return reject(downstream, '403 Forbidden');
    let ips;
    try { ips = await resolver.resolve4(host); }
    catch (_) { return reject(downstream, '502 Bad Gateway'); }
    const ip = ips.find(isPublicIPv4);
    if (!ip) return reject(downstream, '403 Forbidden');
    remote = net.connect({host:ip, port:443, timeout:15000});
    connections.add(remote);
    remote.once('connect', () => {
      downstream.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head && head.length) remote.write(head);
      downstream.pipe(remote); remote.pipe(downstream);
    });
    remote.on('timeout', () => { remote.destroy(); downstream.destroy(); });
    remote.on('error', () => downstream.destroy());
    downstream.on('error', () => remote.destroy());
    downstream.on('close', () => remote.destroy());
    remote.on('close', () => connections.delete(remote));
  } catch (_) {
    if (remote) remote.destroy();
    reject(downstream, '502 Bad Gateway');
  }
});
server.listen(PORT, HOST, () => console.log('ABiT Linux browser egress ready on '+HOST+':'+PORT));
process.on('SIGTERM', () => server.close());
