# Web: the built SPA served by Caddy, which also proxies /api to the app container (TRD 01 §1 `caddy`).
# Build context: frontend/.
FROM node:22-slim AS build
WORKDIR /src
COPY package.json package-lock.json .npmrc ./
# Behind a TLS-inspecting proxy: the optional `build_ca` secret (see backend/Dockerfile).
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; \
    npm ci --no-audit --no-fund
COPY . .
RUN npm run build && npm run check:offline

FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv
EXPOSE 80
