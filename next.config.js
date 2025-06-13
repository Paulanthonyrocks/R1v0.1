/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  staticPageGenerationTimeout: 180,
  output: 'standalone',
  webpack: (config, { isServer }) => {
    config.watchOptions = {
      poll: 1000,
      aggregateTimeout: 300,
    }
    config.experiments = {
      ...config.experiments,
      topLevelAwait: true
    }
    return config
  }
};

module.exports = nextConfig;