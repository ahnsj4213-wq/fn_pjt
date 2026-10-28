import { describe, expect, it } from 'vitest';
import { isPrivateHost, judgeNetlog } from './global-teardown';

const T = { URL_REQUEST_START_JOB: 1, UDP_BYTES_SENT: 2, UDP_CONNECT: 3, DNS_CONFIG_CHANGED: 4, HOST_RESOLVER_MANAGER_REQUEST: 5 };
const ev = (type: number, params: Record<string, unknown>) => ({ type, source: { type: 0 }, params });
const APP = 'http://localhost http://localhost';

describe('AC-22 net-log verdict', () => {
  it('treats loopback and private ranges as inside', () => {
    expect(['localhost', '127.0.0.1', '10.77.0.10', '192.168.0.3', '172.20.1.1', '[::1]'].every(isPrivateHost)).toBe(true);
    expect(['8.8.8.8', '172.32.0.1', 'cdn.example.com', '1.1.1.1'].some(isPrivateHost)).toBe(false);
  });

  it('flags an app request or WebRTC datagram that leaves the network, not vendor traffic or DNS servers', () => {
    const v = judgeNetlog({ constants: { logEventTypes: T }, events: [
      ev(T.DNS_CONFIG_CHANGED, { nameservers: ['8.8.8.8:53'] }),
      ev(T.URL_REQUEST_START_JOB, { url: 'http://localhost:5173/api/postings', network_isolation_key: APP }),
      ev(T.URL_REQUEST_START_JOB, { url: 'https://cdn.example.com/font.woff2', network_isolation_key: APP }),
      ev(T.URL_REQUEST_START_JOB, { url: 'https://update.googleapis.com/x', network_isolation_key: 'null null' }),
      ev(T.UDP_CONNECT, { address: '8.8.8.8:53' }),
      ev(T.UDP_BYTES_SENT, { address: '10.77.0.10:5000' }),
      ev(T.UDP_BYTES_SENT, { address: '74.125.250.129:19302' }), // a public STUN server
    ] }, ['http://localhost']);
    expect(v.appExternal).toEqual(['cdn.example.com']);
    expect(v.udpExternal).toEqual(['74.125.250.129']);
    expect(v.vendorHosts).toEqual(['update.googleapis.com']);
  });
});
