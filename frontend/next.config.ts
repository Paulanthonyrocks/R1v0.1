import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
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
  /* other config options here */
};

export default nextConfig;