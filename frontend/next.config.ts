import type { NextConfig } from "next";

const basePath = process.env.BASE_PATH ?? "";

// Dev-only proxy target for the API. Deliberately separate from
// NEXT_PUBLIC_API_BASE_URL: in production that var is a path-only prefix
// (e.g. "/DriverQServer") which is not a valid upstream URL to proxy to, so
// fall back to localhost:8000 whenever it isn't an absolute http(s) URL.
const rawApiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const devApiProxyTarget = /^https?:\/\//.test(rawApiBase) ? rawApiBase : "http://localhost:8000";

const nextConfig: NextConfig = {
  basePath,
  images: { unoptimized: true },
  trailingSlash: true,
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [
      { source: "/api/:path*", destination: `${devApiProxyTarget}/api/:path*` },
      { source: "/cameras/:path*", destination: `${devApiProxyTarget}/cameras/:path*` },
    ];
  },
};

export default nextConfig;
