FROM debian:bookworm-slim

# Install msmtp (SMTP client), postgresql-client (pg_dump/psql), and curl.
# restic is downloaded directly from GitHub to pin an exact version and avoid
# the outdated package in the bookworm apt repository.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        msmtp \
        msmtp-mta \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# renovate: datasource=github-releases depName=restic/restic
ARG RESTIC_VERSION=0.17.3
RUN curl -fsSL \
        "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_amd64.bz2" \
        | bunzip2 > /usr/local/bin/restic \
    && chmod +x /usr/local/bin/restic

# Scripts are bind-mounted at /backup at runtime — nothing to copy here.
# This keeps the image generic and means changes to scripts are reflected
# immediately without rebuilding.
WORKDIR /backup
