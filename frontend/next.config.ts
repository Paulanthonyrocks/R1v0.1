import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/v1/:path*`,
      },
    ];
  },
  
    allowedDevOrigins: [
        'https://3000-firebase-r1v01-1757542787380.cluster-lu4mup47g5gm4rtyvhzpwbfadi.cloudworkstations.dev',
        ...(process.env.NEXT_PUBLIC_API_BASE_URL ? [process.env.NEXT_PUBLIC_API_BASE_URL] : []),
    ],
  /* other config options here */
};

export default nextConfig;