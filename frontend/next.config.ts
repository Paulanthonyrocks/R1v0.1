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
  webpack: (config) => {
    config.module.rules.push({
      test: /\.(glb|gltf)$/,
      use: [
        {
          loader: 'file-loader',
          options: {
            outputPath: 'static/assets/',
            publicPath: '_next/static/assets/',
          },
        },
      ],
    });
    return config;
  },
  allowedDevOrigins: ['https://3000-firebase-r1v01-1754863305396.cluster-cbeiita7rbe7iuwhvjs5zww2i4.cloudworkstations.dev'],
  /* other config options here */
};

export default nextConfig;