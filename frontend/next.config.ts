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
    // @ts-ignore
  },
  /* other config options here */
};

export default nextConfig;