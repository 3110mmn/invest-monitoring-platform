FROM node:24-alpine AS dependencies

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM node:24-alpine AS builder

ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_READ_ONLY=true
ARG NEXT_PUBLIC_PUBLIC_DEMO_WRITE_ENABLED=false
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_READ_ONLY=${NEXT_PUBLIC_READ_ONLY} \
    NEXT_PUBLIC_PUBLIC_DEMO_WRITE_ENABLED=${NEXT_PUBLIC_PUBLIC_DEMO_WRITE_ENABLED}

WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY frontend ./
RUN npm run build

FROM node:24-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=8080

WORKDIR /app
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 8080

CMD ["node", "server.js"]
