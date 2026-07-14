import type { NextConfig } from "next";

const basePath = process.env.BASE_PATH ?? "";

// Next.js requires basePath to be empty or start with "/" and not end with
// "/". Validate eagerly so misconfiguration (e.g. BASE_PATH=/DriverQ/ with a
// trailing slash) fails fast with a clear message instead of a confusing
// downstream routing error.
if (basePath !== "" && (!basePath.startsWith("/") || basePath.endsWith("/"))) {
  throw new Error(
    `Invalid BASE_PATH "${basePath}": must be empty or start with "/" and not end with "/" (e.g. "/DriverQ").`
  );
}

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
