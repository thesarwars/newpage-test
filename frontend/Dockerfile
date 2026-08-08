FROM node:24-alpine AS base
ENV PNPM_HOME=/pnpm PATH="/pnpm:$PATH"
# Next collects anonymous usage telemetry by default and posts it to Vercel.
# The README states that the only external egress from this app is
# api.anthropic.com, and that has to be true rather than nearly true — an app
# holding somebody's résumé does not get to phone home about itself. Set on the
# base stage so it applies to build and runtime alike.
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app

# ─── deps ─────────────────────────────────────────────────────────────────────
# --frozen-lockfile fails loudly if pnpm-lock.yaml is stale rather than
# resolving something CI never tested.
FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# ─── dev ──────────────────────────────────────────────────────────────────────
FROM deps AS dev
# pnpm 11 verifies node_modules against the lockfile before every script run and
# reinstalls on a mismatch. In this container that check is guaranteed to be
# wrong: node_modules is an anonymous volume, deliberately decoupled from the
# bind-mounted source so a macOS-built tree cannot shadow the Linux one. The
# reinstall it triggers then aborts — it wants to delete a directory and there is
# no TTY to confirm — and the dev server never starts.
# pnpm 11 verifies node_modules against the lockfile before every script run and
# reinstalls on a mismatch. Here node_modules is an anonymous volume that
# `docker compose up --force-recreate` deliberately REUSES, so it can outlive the
# image it was seeded from — add a dependency inside a running container and the
# volume records a store path the rebuilt image no longer uses. pnpm then tries
# to purge, asks for confirmation, finds no TTY, and aborts, so the dev server
# never starts and the error names none of this.
#
# `--renew-anon-volumes` is the cure for an already-diverged volume;
# `confirm-modules-purge false` lets the container heal itself instead of dying.
RUN pnpm config set --global confirm-modules-purge false
COPY . .
EXPOSE 3000
CMD ["pnpm", "dev"]

# ─── build ────────────────────────────────────────────────────────────────────
FROM deps AS build
COPY . .
RUN pnpm build

# ─── prod ─────────────────────────────────────────────────────────────────────
FROM base AS prod
ENV NODE_ENV=production
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
