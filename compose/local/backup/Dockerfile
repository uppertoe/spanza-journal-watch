FROM debian:bookworm-slim

# Install msmtp (SMTP client), postgresql-client (pg_dump/psql), and curl.
# restic is downloaded directly from GitHub to pin an exact version and avoid
# the outdated package in the bookworm apt repository.
# Add the official PostgreSQL apt repository so we can install postgresql-client-17.
# The version here must match the postgres service in docker-compose (currently 17).
RUN apt-get update && apt-get install -y --no-install-recommends \
        bzip2 \
        ca-certificates \
        curl \
        gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
         | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        msmtp \
        msmtp-mta \
        postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

# renovate: datasource=github-releases depName=restic/restic
ARG RESTIC_VERSION=0.17.3
# TARGETARCH is set automatically by Docker Buildx (amd64, arm64, etc.) and
# matches the naming convention used in restic release filenames.
ARG TARGETARCH
RUN curl -fsSL \
        "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${TARGETARCH}.bz2" \
        | bunzip2 > /usr/local/bin/restic \
    && chmod +x /usr/local/bin/restic

# Scripts are bind-mounted at /backup at runtime — nothing to copy here.
# This keeps the image generic and means changes to scripts are reflected
# immediately without rebuilding.
WORKDIR /backup
