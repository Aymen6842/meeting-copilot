# syntax=docker/dockerfile:1
# Build from the repository root: docker build -f apps/web/Dockerfile .
# Placeholder image until the Next.js app is built (milestone 1, task 10).
FROM node:22-alpine

WORKDIR /srv
COPY apps/web/placeholder-server.mjs ./

USER node
EXPOSE 3000
CMD ["node", "placeholder-server.mjs"]
