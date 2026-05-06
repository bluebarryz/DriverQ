import type { NextConfig } from "next";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  images: { unoptimized: true },
  trailingSlash: true,
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [
      { source: "/api/:path*", destination: `${apiBase}/api/:path*` },
      { source: "/cameras/:path*", destination: `${apiBase}/cameras/:path*` },
    ];
  },
};

export default nextConfig;
