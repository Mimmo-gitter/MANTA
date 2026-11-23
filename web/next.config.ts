import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  async redirects() {
    return [
      {
        source: "/members",
        destination: "/access-roles",
        permanent: true,
      },
      {
        source: "/members/:path*",
        destination: "/access-roles",
        permanent: true,
      },
    ];
  },
  experimental: {
    // optimizePackageImports for performance
    optimizePackageImports: ["lucide-react", "recharts", "@xyflow/react"],
  },
};

export default nextConfig;
