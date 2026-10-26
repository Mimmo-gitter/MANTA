import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  experimental: {
    // optimizePackageImports for performance
    optimizePackageImports: ["lucide-react", "recharts", "@tremor/react", "@xyflow/react"]
  }
};

export default nextConfig;
