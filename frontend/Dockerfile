FROM node:20-alpine AS builder

WORKDIR /app

COPY frontend/package.json /app/package.json
COPY frontend/package-lock.json /app/package-lock.json
RUN npm ci

COPY frontend /app

ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

# Path prefix Apache exposes for the frontend (baked into Next.js's routing/asset paths)
ARG BASE_PATH
ENV BASE_PATH=${BASE_PATH}

RUN npm run build

FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production

# Next.js reloads next.config.ts at runtime, so BASE_PATH must also be set
# here (not just in the builder stage), or `next start` will serve routes at
# "/" instead of the prefixed path baked in above.
ARG BASE_PATH
ENV BASE_PATH=${BASE_PATH}

# node:alpine ships with a built-in non-root "node" user (uid 1000).
# Use COPY --chown instead of a separate `RUN chown -R` layer to avoid an
# extra, size-doubling copy of every file.
COPY --from=builder --chown=node:node /app/package.json /app/package.json
COPY --from=builder --chown=node:node /app/package-lock.json /app/package-lock.json
COPY --from=builder --chown=node:node /app/.next /app/.next
COPY --from=builder --chown=node:node /app/public /app/public
COPY --from=builder --chown=node:node /app/next.config.ts /app/next.config.ts
COPY --from=builder --chown=node:node /app/node_modules /app/node_modules

USER node

EXPOSE 3000

CMD ["npm", "run", "start"]
