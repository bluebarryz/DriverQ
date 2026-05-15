FROM node:20-alpine AS builder

WORKDIR /app

COPY frontend/package.json /app/package.json
COPY frontend/package-lock.json /app/package-lock.json
RUN npm ci

COPY frontend /app

ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

RUN npm run build

FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production

COPY --from=builder /app/package.json /app/package.json
COPY --from=builder /app/package-lock.json /app/package-lock.json
COPY --from=builder /app/.next /app/.next
COPY --from=builder /app/public /app/public
COPY --from=builder /app/next.config.ts /app/next.config.ts
COPY --from=builder /app/node_modules /app/node_modules

# node:alpine ships with a built-in non-root "node" user (uid 1000)
RUN chown -R node:node /app
USER node

EXPOSE 3000

CMD ["npm", "run", "start"]
