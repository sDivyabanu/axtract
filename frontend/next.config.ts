import type { NextConfig } from "next";
import { bindingKey, verifyLocalBinding } from "./scripts/local-boundary.mjs";

// A normal next dev/start cannot enable anonymous network access by setting a flag.
if (process.env.AXTRACT_LOCAL_NO_AUTH !== "true") {
  throw new Error("AXTRACT is local-only. Set AXTRACT_LOCAL_NO_AUTH=true and run npm run dev:local.");
}
verifyLocalBinding(Reflect.get(globalThis, bindingKey));
const api = new URL(process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000");
if (!["http:", "https:"].includes(api.protocol) || !["127.0.0.1", "[::1]", "localhost"].includes(api.hostname)) {
  throw new Error("AXTRACT requires a loopback API URL.");
}

const nextConfig: NextConfig = {
  /* config options here */
  env: { NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH: "true" },
  async redirects() {
    return [{ source: "/login", destination: "/", permanent: false }];
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
