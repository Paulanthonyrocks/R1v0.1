import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  outputFileTracingRoot: require('path').join(__dirname, '../../'),
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/v1/:path*`,
      },
    ];
  },
  reactStrictMode: true,
  experimental: {
    // This allows the specific cloud workstation domain you are using
    allowedDevOrigins: [
      "http://localhost:3000",
      "https://*.cloudworkstations.dev",
      "https://*.ngrok-free.app",
      "https://3000-firebase-r1v01-1757542787380.cluster-lu4mup47g5gm4rtyvhzpwbfadi.cloudworkstations.dev"
    ],
  },
  /* other config options here */
};

export default nextConfig;