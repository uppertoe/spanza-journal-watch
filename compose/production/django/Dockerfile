# Base images are pinned by digest so every build of a given commit uses the
# same layers. Bump the digest deliberately (Dependabot opens PRs for these):
#   docker buildx imagetools inspect python:3.13-alpine3.21 | grep Digest
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS docs-builder

# Sphinx and friends at the versions locked in requirements/docs.txt, without
# installing the Django stack that file also carries. pip constraints may not
# carry extras, so strip the [oidc]-style suffixes first.
COPY ./requirements/docs.txt /tmp/docs.txt
RUN sed -E 's/\[[^]]+\]//' /tmp/docs.txt > /tmp/constraints.txt \
  && pip install --no-cache-dir -c /tmp/constraints.txt sphinx myst-parser furo

WORKDIR /docs-src
COPY ./docs /docs-src/docs

RUN python -m sphinx docs/ /docs-build -q


FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS client-builder

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# npm ci installs exactly what package-lock.json records; a lockfile drift
# fails the build instead of silently floating package versions.
COPY ./package.json ./package-lock.json ${APP_HOME}/
RUN npm ci --no-audit --no-fund && npm cache clean --force
COPY ./webpack ${APP_HOME}/webpack
COPY ./spanza_journal_watch/static ${APP_HOME}/spanza_journal_watch/static
RUN npm run build
# define an alias for the specific python version used in this file.
FROM python:3.13-alpine3.21@sha256:e77011e08668411a8db356ec6bae7d39cdb0ff531db49bf1de9d5a64d8f75a6a AS python

# Python build stage
FROM python AS python-build-stage

ARG BUILD_ENVIRONMENT=production

# Install build dependencies
RUN apk add --no-cache \
  # dependencies for building Python packages
  build-base \
  # psycopg2 dependencies
  postgresql-dev

# Requirements are installed here to ensure they will be cached.
COPY ./requirements .

# Create Python Dependency and Sub-Dependency Wheels.
RUN pip wheel --no-cache-dir --wheel-dir /usr/src/app/wheels  \
  -r ${BUILD_ENVIRONMENT}.txt


# Python 'run' stage
FROM python AS python-run-stage

ARG BUILD_ENVIRONMENT=production
ARG APP_HOME=/app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV BUILD_ENV=${BUILD_ENVIRONMENT}

WORKDIR ${APP_HOME}

RUN addgroup -S django \
  && adduser -S -G django django \
  && apk add --no-cache \
    # PostgreSQL runtime library for psycopg
    postgresql-libs \
    # Translations dependencies
    gettext

# All absolute dir copies ignore workdir instruction. All relative dir copies are wrt to the workdir instruction
# copy python dependency wheels from python-build-stage
COPY --from=python-build-stage /usr/src/app/wheels  /wheels/

# use wheels to install python dependencies
RUN pip install --no-cache-dir --no-index --find-links=/wheels/ /wheels/* \
  && rm -rf /wheels/ \
  && pip uninstall -y pip setuptools \
  && find /usr/local/lib/python3.13 -type d -name __pycache__ -exec rm -rf {} +

COPY --chown=django:django --chmod=755 ./compose/production/django/entrypoint /entrypoint
COPY --chown=django:django --chmod=755 ./compose/production/django/start /start
COPY --chown=django:django --chmod=755 ./compose/production/django/celery/worker/start /start-celeryworker
COPY --chown=django:django --chmod=755 ./compose/production/django/celery/beat/start /start-celerybeat
COPY --chmod=755 ./compose/production/django/celery/flower/start /start-flower

# Copy application code into the runtime image. Frontend build dependencies
# stay in the Node build stage; only built assets are copied below.
COPY --chown=django:django ./config ${APP_HOME}/config
COPY --chown=django:django ./manage.py ${APP_HOME}/manage.py
COPY --chown=django:django ./spanza_journal_watch ${APP_HOME}/spanza_journal_watch
COPY --from=client-builder --chown=django:django \
  ${APP_HOME}/spanza_journal_watch/static/webpack_bundles \
  ${APP_HOME}/spanza_journal_watch/static/webpack_bundles
COPY --from=client-builder --chown=django:django ${APP_HOME}/webpack-stats.json ${APP_HOME}/webpack-stats.json

# Copy built documentation so it can be served at /editorial/docs/
COPY --from=docs-builder --chown=django:django /docs-build ${APP_HOME}/docs/_build/html

RUN mkdir -p \
    ${APP_HOME}/access_logs \
    ${APP_HOME}/staticfiles \
  && chown -R django:django ${APP_HOME}

USER django

ENTRYPOINT ["/entrypoint"]
