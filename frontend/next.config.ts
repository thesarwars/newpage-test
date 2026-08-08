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

  // Origins allowed to request /_next/* assets from the dev server.
  //
  // Next blocks cross-origin dev requests by default, and the block is a bare
  // 403 on the asset — the page renders as a blank body with no error of its
  // own. That is how it presents: not "forbidden", just nothing. It applies to
  // any browser not on this machine, which includes the one inside the
  // Playwright container and a phone testing on the LAN.
  //
  // Dev only; `next build` output is unaffected.
  allowedDevOrigins: ["host.docker.internal", "127.0.0.1", "localhost"],
};

export default nextConfig;
