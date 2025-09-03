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
  
  allowedDevOrigins: ['https://3000-firebase-r1v01-1754863305396.cluster-cbeiita7rbe7iuwhvjs5zww2i4.cloudworkstations.dev'],
  /* other config options here */
};

export default nextConfig;