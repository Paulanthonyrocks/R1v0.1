import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone',
  outputFileTracingRoot: require('path').join(__dirname, '..'),
  // @ts-ignore
  allowedDevOrigins: [
    '3000-firebase-r1v01-1757542787380.cluster-lu4mup47g5gm4rtyvhzpwbfadi.cloudworkstations.dev',
  ],
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'}/api/v1/:path*`,
      },
    ];
  },
  reactStrictMode: true,
  turbopack: {
    root: require('path').join(__dirname, '..'),
  },
  /* other config options here */
};

export default nextConfig;