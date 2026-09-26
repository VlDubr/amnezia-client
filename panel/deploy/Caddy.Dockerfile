# Build context: repository root.
#   docker build -f panel/deploy/Caddy.Dockerfile -t amnezia-panel-caddy .
FROM node:22-alpine AS web
WORKDIR /web
COPY panel/web/package.json panel/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY panel/web/ ./
RUN npm run build

FROM caddy:2-alpine
COPY panel/deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=web /web/dist /srv/web
