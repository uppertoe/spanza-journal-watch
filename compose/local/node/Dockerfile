FROM node:26-bookworm-slim@sha256:367679cf9792759492a486e4aa4b421764d71a9546a6dae8aab81a99eb797b3e

WORKDIR /app

COPY ./package.json ./package-lock.json /app/

# Same install as the production client-builder stage: exact lockfile versions.
RUN npm ci --no-audit --no-fund && npm cache clean --force

ENV PATH=./node_modules/.bin/:$PATH
