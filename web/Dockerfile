FROM node:20-alpine AS deps
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json .npmrc ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Production URLs stay empty by default so immutable images built for single-digest
# promotion never bake loopback defaults into static bundles. Callers may supply
# either build-args here or container runtime env vars (read by /env.js & SSR).
ARG NEXT_PUBLIC_API_URL=
ARG NEXT_PUBLIC_WS_URL=
ARG NEXT_PUBLIC_APP_URL=
ARG NEXT_PUBLIC_KEYCLOAK_URL=
ARG NEXT_PUBLIC_KEYCLOAK_REALM=manta
ARG NEXT_PUBLIC_KEYCLOAK_CLIENT_ID=manta-portal
ARG NEXT_PUBLIC_REDIRECT_URI=
ARG NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI=
ARG NEXT_PUBLIC_ENV_LABEL=PRODUCTION

ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_WS_URL=${NEXT_PUBLIC_WS_URL} \
    NEXT_PUBLIC_APP_URL=${NEXT_PUBLIC_APP_URL} \
    NEXT_PUBLIC_KEYCLOAK_URL=${NEXT_PUBLIC_KEYCLOAK_URL} \
    NEXT_PUBLIC_KEYCLOAK_REALM=${NEXT_PUBLIC_KEYCLOAK_REALM} \
    NEXT_PUBLIC_KEYCLOAK_CLIENT_ID=${NEXT_PUBLIC_KEYCLOAK_CLIENT_ID} \
    NEXT_PUBLIC_REDIRECT_URI=${NEXT_PUBLIC_REDIRECT_URI} \
    NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI=${NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI} \
    NEXT_PUBLIC_ENV_LABEL=${NEXT_PUBLIC_ENV_LABEL} \
    NEXT_TELEMETRY_DISABLED=1 \
    ESLINT_USE_FLAT_CONFIG=false

RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app

# Re-declare build-args so images built with --build-arg carry them as container
# defaults while remaining 100% overridable via `docker run -e` / docker-compose.
ARG NEXT_PUBLIC_API_URL=
ARG NEXT_PUBLIC_WS_URL=
ARG NEXT_PUBLIC_APP_URL=
ARG NEXT_PUBLIC_KEYCLOAK_URL=
ARG NEXT_PUBLIC_KEYCLOAK_REALM=manta
ARG NEXT_PUBLIC_KEYCLOAK_CLIENT_ID=manta-portal
ARG NEXT_PUBLIC_REDIRECT_URI=
ARG NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI=
ARG NEXT_PUBLIC_ENV_LABEL=PRODUCTION

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME="0.0.0.0" \
    NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_WS_URL=${NEXT_PUBLIC_WS_URL} \
    NEXT_PUBLIC_APP_URL=${NEXT_PUBLIC_APP_URL} \
    NEXT_PUBLIC_KEYCLOAK_URL=${NEXT_PUBLIC_KEYCLOAK_URL} \
    NEXT_PUBLIC_KEYCLOAK_REALM=${NEXT_PUBLIC_KEYCLOAK_REALM} \
    NEXT_PUBLIC_KEYCLOAK_CLIENT_ID=${NEXT_PUBLIC_KEYCLOAK_CLIENT_ID} \
    NEXT_PUBLIC_REDIRECT_URI=${NEXT_PUBLIC_REDIRECT_URI} \
    NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI=${NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI} \
    NEXT_PUBLIC_ENV_LABEL=${NEXT_PUBLIC_ENV_LABEL}

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/env.js').then((r) => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"

CMD ["node", "server.js"]
