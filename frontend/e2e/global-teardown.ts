import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

/**
 * AC-22 verdict for the egress-blocked run (tools/offline/run.sh). Outside that run (no E2E_OFFLINE) it does nothing.
 *
 * - backend + worker: the Python audit log must be empty (no lookup or connection outside loopback / Unix sockets).
 * - browser (Chromium net-log): every request made for the app's pages (network isolation key = the app's site)
 *   and every UDP datagram (WebRTC media) must stay on loopback or a private address. DNS server sockets are
 *   lookups, judged by the names asked for.
 * Chromium's own vendor services (sign-in, component updates, ...) are not part of the platform: they are written
 * to the report as `browser_vendor_hosts` for reference, not counted. A stock browser on the demo machine is
 * covered by the manual rehearsal (docs/offline-rehearsal.md).
 */
const LOCAL_NAMES = new Set(['localhost', '127.0.0.1', '::1', '[::1]', '0.0.0.0']);

export function isPrivateHost(host: string): boolean {
  const h = host.replace(/^\[|\]$/g, '');
  if (LOCAL_NAMES.has(h)) return true;
  const m = h.match(/^(\d+)\.(\d+)\.\d+\.\d+$/);
  if (m) {
    const [a, b] = [Number(m[1]), Number(m[2])];
    return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254);
  }
  return /^(fe80:|fc|fd)/i.test(h) || h === '::1';
}

interface NetLogEvent { type: number; source: { type: number }; params?: Record<string, unknown> }
interface NetLog { constants: { logEventTypes: Record<string, number> }; events: NetLogEvent[] }

const hostOf = (url: string) => url.replace(/^[a-z]+:\/\//i, '').split(/[/?#]/)[0].replace(/:\d+$/, '');
const addrHost = (addr: string) => addr.replace(/:\d+$/, '').replace(/^\[|\]$/g, '');

export function judgeNetlog(log: NetLog, appSites: string[]) {
  const t = log.constants.logEventTypes;
  const dnsServers = new Set<string>();
  const appExternal = new Set<string>();
  const udpExternal = new Set<string>();
  const vendor = new Set<string>();
  const lookups = new Set<string>();
  for (const e of log.events) {
    const p = e.params ?? {};
    if (e.type === t.DNS_CONFIG_CHANGED) {
      for (const ns of (p.nameservers as string[] | undefined) ?? []) dnsServers.add(addrHost(ns));
    }
  }
  for (const e of log.events) {
    const p = e.params ?? {};
    if (e.type === t.URL_REQUEST_START_JOB && typeof p.url === 'string') {
      const host = hostOf(p.url);
      const nik = String(p.network_isolation_key ?? '');
      const forApp = appSites.some((s) => nik.includes(s));
      if (forApp && !isPrivateHost(host)) appExternal.add(host);
      else if (!forApp && !isPrivateHost(host)) vendor.add(host);
    }
    if ((e.type === t.UDP_BYTES_SENT || e.type === t.UDP_CONNECT) && typeof p.address === 'string') {
      const host = addrHost(p.address);
      if (!isPrivateHost(host) && !dnsServers.has(host)) udpExternal.add(host);
    }
    if (e.type === t.HOST_RESOLVER_MANAGER_REQUEST && typeof p.host === 'string') lookups.add(hostOf(p.host));
  }
  // A name the app itself asked for would already be in appExternal; other lookups belong to vendor services.
  const vendorLookups = [...lookups].filter((h) => !isPrivateHost(h) && !appExternal.has(h));
  return { appExternal: [...appExternal], udpExternal: [...udpExternal],
    vendorHosts: [...new Set([...vendor, ...vendorLookups])].sort() };
}

export default function globalTeardown(): void {
  if (!process.env.E2E_OFFLINE) return;
  const problems: string[] = [];
  const report: Record<string, unknown> = {};
  const backendLog = process.env.E2E_EGRESS_LOG;
  if (!backendLog || !existsSync(backendLog)) problems.push('backend egress log missing');
  else {
    const lines = [...new Set(readFileSync(backendLog, 'utf8').split('\n').filter(Boolean))];
    report.backend_attempts = lines;
    if (lines.length) problems.push(`backend tried to reach: ${lines.join(', ')}`);
  }
  const netlog = process.env.E2E_NETLOG;
  if (!netlog || !existsSync(netlog)) problems.push('chromium net-log missing');
  else {
    const v = judgeNetlog(JSON.parse(readFileSync(netlog, 'utf8')) as NetLog, ['http://localhost', 'http://127.0.0.1']);
    Object.assign(report, { app_external_hosts: v.appExternal, webrtc_external_addresses: v.udpExternal,
      browser_vendor_hosts: v.vendorHosts });
    if (v.appExternal.length) problems.push(`app requests left the network: ${v.appExternal.join(', ')}`);
    if (v.udpExternal.length) problems.push(`UDP (WebRTC) to non-private addresses: ${v.udpExternal.join(', ')}`);
  }
  report.verdict = problems.length ? 'FAIL' : 'PASS';
  if (backendLog) writeFileSync(path.join(path.dirname(backendLog), 'ac22-report.json'), JSON.stringify(report, null, 2));
  if (problems.length) throw new Error(`AC-22 failed:\n${problems.join('\n')}`);
  console.log(`AC-22 PASS: backend 0 external attempts; app requests and WebRTC stayed on loopback/private addresses. `
    + `Browser vendor services (not counted): ${(report.browser_vendor_hosts as string[]).join(', ') || 'none'}`);
}
