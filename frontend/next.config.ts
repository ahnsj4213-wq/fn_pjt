import type { NextConfig } from 'next';

// Same-origin: the browser only talks to this server and /api is forwarded to FastAPI (API_ORIGIN), so the session
// cookie stays SameSite=Strict and there is no CORS. localhost is a secure context for the microphone (8.4).
const apiOrigin = process.env.API_ORIGIN ?? 'http://127.0.0.1:8000';

const nextConfig: NextConfig = {
  output: 'standalone',
  poweredByHeader: false,
  reactStrictMode: true,
  typescript: { tsconfigPath: 'tsconfig.next.json' },
  // The proxy cuts request bodies at 10 MB by default and forwards the rest as a hung request, so a PDF just over
  // the limit (RES-02, 10 MB) ended in a 30 s timeout instead of the API's 413. Leave room for the multipart overhead.
  experimental: { proxyClientMaxBodySize: '12mb' },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${apiOrigin}/api/:path*` }];
  },
  async headers() {
    return [{
      source: '/:path*',
      headers: [
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'Referrer-Policy', value: 'no-referrer' },
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'Permissions-Policy', value: 'microphone=(self), camera=(self), geolocation=()' },
      ],
    }];
  },
};

export default nextConfig;
