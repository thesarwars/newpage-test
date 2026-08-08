import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone bundles only the traced dependencies, so the runtime image
  // doesn't carry node_modules. Also what an ECS/Fargate deploy expects —
  // see docs/PLAN.md §16.
  output: "standalone",

  // The browser talks to the API origin directly rather than through a Next
  // rewrite: a proxy hop is one more place SSE can be buffered, and buffered
  // SSE looks exactly like a hung request. CORS is locked to the web origin
  // server-side instead.
};

export default nextConfig;
