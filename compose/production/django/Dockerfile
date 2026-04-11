# Docs build stage — produces the static HTML served at /editorial/docs/
FROM python:3.13-slim-bookworm AS docs-builder

RUN pip install --no-cache-dir sphinx myst-parser furo

WORKDIR /docs-src
COPY ./docs /docs-src/docs

RUN python -m sphinx docs/ /docs-build -q


FROM node:22-bookworm-slim AS client-builder

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

COPY ./package.json ${APP_HOME}/
RUN yarn install && yarn cache clean --force
COPY ./webpack ${APP_HOME}/webpack
COPY ./spanza_journal_watch/static ${APP_HOME}/spanza_journal_watch/static
RUN yarn run build
# define an alias for the specific python version used in this file.
FROM python:3.13-alpine3.21 AS python

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
