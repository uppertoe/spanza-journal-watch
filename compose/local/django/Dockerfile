# define an alias for the specific python version used in this file.
FROM python:3.13-alpine3.21 AS python

# Python build stage
FROM python AS python-build-stage

ARG BUILD_ENVIRONMENT=local

# Install build dependencies
RUN apk add --no-cache \
  # dependencies for building Python packages
  build-base \
  # psycopg2 dependencies
  postgresql-dev

# Requirements are installed here to ensure they will be cached.
COPY ./requirements .

# Create Python Dependency and Sub-Dependency Wheels.
RUN pip wheel --wheel-dir /usr/src/app/wheels  \
  -r ${BUILD_ENVIRONMENT}.txt


# Python 'run' stage
FROM python AS python-run-stage

ARG BUILD_ENVIRONMENT=local
ARG APP_HOME=/app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV BUILD_ENV=${BUILD_ENVIRONMENT}

WORKDIR ${APP_HOME}

# Install required system dependencies
RUN apk add --no-cache \
  # PostgreSQL runtime library for psycopg
  postgresql-libs \
  # Translations dependencies
  gettext

# All absolute dir copies ignore workdir instruction. All relative dir copies are wrt to the workdir instruction
# copy python dependency wheels from python-build-stage
COPY --from=python-build-stage /usr/src/app/wheels  /wheels/

# use wheels to install python dependencies
RUN pip install --no-cache-dir --no-index --find-links=/wheels/ /wheels/* \
  && rm -rf /wheels/

COPY --chmod=755 ./compose/production/django/entrypoint /entrypoint
COPY --chmod=755 ./compose/local/django/start /start
COPY --chmod=755 ./compose/local/django/celery/worker/start /start-celeryworker
COPY --chmod=755 ./compose/local/django/celery/beat/start /start-celerybeat
COPY --chmod=755 ./compose/local/django/celery/flower/start /start-flower


# copy application code to WORKDIR
COPY . ${APP_HOME}

ENTRYPOINT ["/entrypoint"]
