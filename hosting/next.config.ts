import type { NextConfig } from 'next';
import { getBackendBaseURL } from './lib/api/backendBaseUrl';

const nextConfig: NextConfig = {
  output: 'standalone',
  outputFileTracingRoot: __dirname,
  allowedDevOrigins: [
    '3000-firebase-r1v01-1774108349517.cluster-lu4mup47g5gm4rtyvhzpwbfadi.cloudworkstations.dev',
  ],
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${getBackendBaseURL()}/api/v1/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          {
            key: 'X-Content-Type-Options',
            value: 'nosniff',
          },
          {
            key: 'X-Frame-Options',
            value: 'DENY',
          },
          {
            key: 'X-XSS-Protection',
            value: '1; mode=block',
          },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=31536000; includeSubDomains',
          },
          {
            key: 'Referrer-Policy',
            value: 'strict-origin-when-cross-origin',
          },
          {
            key: 'Content-Security-Policy',
            value: "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: blob: *.openstreetmap.org *.arcgisonline.com *.cartocdn.com unpkg.com; connect-src 'self' ws: wss: http: https:; font-src 'self' data: https://fonts.gstatic.com; frame-ancestors 'none';",
          },
        ],
      },
    ];
  },
  reactStrictMode: true,
  turbopack: {
    root: __dirname,
  },
  experimental: {
    // Next.js 16.3.0 reads `experimental.instantInsights.validationLevel`
    // unconditionally in base-server.js:404 but does NOT default the object
    // itself. Without this, every request 500s with:
    //   TypeError: Cannot read properties of undefined (reading 'validationLevel')
    // Pin to the schema default so we match Next.js's own dev probe worker
    // (`use-cache-probe-worker.js:175`).
    instantInsights: {
      validationLevel: 'warning',
    },
  },
  /* other config options here */
};

export default nextConfig;