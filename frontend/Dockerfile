# frontend: Next.js (standalone server on :3000). /api is proxied to the api container (next.config.ts); the rewrite
# target is fixed at build time, so API_ORIGIN is a build argument as well as a runtime variable.
# Build context: frontend/.
FROM node:22-slim AS build
WORKDIR /src
ENV NEXT_TELEMETRY_DISABLED=1
ARG API_ORIGIN=http://api:8000
ENV API_ORIGIN=${API_ORIGIN}
COPY package.json package-lock.json .npmrc ./
# Behind a TLS-inspecting proxy: the optional `build_ca` secret (see backend/Dockerfile).
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; \
    npm ci --no-audit --no-fund
COPY . .
RUN npm run build && npm run check:offline

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
ARG API_ORIGIN=http://api:8000
ENV API_ORIGIN=${API_ORIGIN}
COPY --from=build --chown=node:node /src/.next/standalone ./
COPY --from=build --chown=node:node /src/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
