FROM node:24-alpine AS base
ENV PNPM_HOME=/pnpm PATH="/pnpm:$PATH"
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
