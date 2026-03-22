# SPANZA Journal Watch

A web application for the Society of Paediatric Anaesthesia in New Zealand and Australia - Journal Watch

[![Built with Cookiecutter Django](https://img.shields.io/badge/built%20with-Cookiecutter%20Django-ff69b4.svg?logo=cookiecutter)](https://github.com/cookiecutter/cookiecutter-django/)
[![Black code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

License: MIT

## Settings

Moved to [settings](http://cookiecutter-django.readthedocs.io/en/latest/settings.html).

## Basic Commands

### Setting Up Your Users

- To create a **normal user account**, just go to Sign Up and fill out the form. Once you submit it, you'll see a "Verify Your E-mail Address" page. Go to your console to see a simulated email verification message. Copy the link into your browser. Now the user's email should be verified and ready to go.

- To create a **superuser account**, use this command:

      $ python manage.py createsuperuser

For convenience, you can keep your normal user logged in on Chrome and your superuser logged in on Firefox (or similar), so that you can see how the site behaves for both kinds of users.

### Type checks

Running type checks with mypy:

    $ mypy spanza_journal_watch

### Dependency management (pip-tools)

Direct dependencies now live in:

- requirements/base.in
- requirements/local.in
- requirements/production.in
- requirements/docs.in

Generate/update pinned lock files with:

```bash
pip-compile requirements/base.in -o requirements/base.txt
pip-compile requirements/local.in -o requirements/local.txt
pip-compile requirements/production.in -o requirements/production.txt
```

Upgrade specific packages (example: Django on 4.2 LTS):

```bash
pip-compile requirements/base.in -o requirements/base.txt --upgrade-package django
pip-compile requirements/local.in -o requirements/local.txt
pip-compile requirements/production.in -o requirements/production.txt
```

### Test coverage

To run the tests, check your test coverage, and generate an HTML coverage report:

    $ coverage run -m pytest
    $ coverage html
    $ open htmlcov/index.html

#### Running tests with pytest

    $ pytest

### Live reloading and Sass CSS compilation

Moved to [Live reloading and SASS compilation](https://cookiecutter-django.readthedocs.io/en/latest/developing-locally.html#sass-compilation-live-reloading).

### Celery

This app comes with Celery.

To run a celery worker:

```bash
cd spanza_journal_watch
celery -A config.celery_app worker -l info
```

Please note: For Celery's import magic to work, it is important _where_ the celery commands are run. If you are in the same folder with _manage.py_, you should be right.

To run [periodic tasks](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html), you'll need to start the celery beat scheduler service. You can start it as a standalone process:

```bash
cd spanza_journal_watch
celery -A config.celery_app beat
```

or you can embed the beat service inside a worker with the `-B` option (not recommended for production use):

```bash
cd spanza_journal_watch
celery -A config.celery_app worker -B -l info
```

### Email Server

In development, it is often nice to be able to see emails that are being sent from your application. For that reason local SMTP server [MailHog](https://github.com/mailhog/MailHog) with a web interface is available as docker container.

Container mailhog will start automatically when you will run all docker containers.
Please check [cookiecutter-django Docker documentation](http://cookiecutter-django.readthedocs.io/en/latest/deployment-with-docker.html) for more details how to start all containers.

With MailHog running, to view messages that are sent by your application, open your browser and go to `http://127.0.0.1:8025`

### Authelia (local)

Authelia has been added as an optional local profile using the Docker deployment approach from the official docs.

Start it with:

```bash
docker compose -f local.yml --profile authelia up -d authelia
```

Then open:

- `http://localhost:9091`

Local test users (password for both is `authelia`):

- `reviewer`
- `coordinator`

Authelia local config files:

- `compose/local/authelia/config/configuration.yml`
- `compose/local/authelia/config/users_database.yml`
- `compose/local/authelia/secrets/*`

OIDC clients are preconfigured for local shared login:

- Django callback: `http://localhost:8000/accounts/oidc/authelia/login/callback/`
- Planka callback: `http://localhost:3001/oidc-callback`

To run a local end-to-end auth smoke test:

1. Start app stack with Planka + Authelia profiles.
2. Open Django login page (`/accounts/login/`) and choose **Authelia**.
3. Confirm successful return to Django with the authenticated user session.
4. Open Planka (`http://localhost:3001`), use **Log in with SSO**, and confirm successful login.

These are development-only defaults and should be replaced before any non-local usage.

### Sentry

Sentry is an error logging aggregator service. You can sign up for a free account at <https://sentry.io/signup/?code=cookiecutter> or download and host it yourself.
The system is set up with reasonable defaults, including 404 logging and integration with the WSGI application.

You must set the DSN url in production.

## Deployment

The following details how to deploy this application.

### Docker

See detailed [cookiecutter-django Docker documentation](http://cookiecutter-django.readthedocs.io/en/latest/deployment-with-docker.html).

For this project specifically (prebuilt images on Docker Hub + minimal production compose), see:

- [docs/dockerhub_minimal_production.md](docs/dockerhub_minimal_production.md)

### Custom Bootstrap Compilation

The generated CSS is set up with automatic Bootstrap recompilation with variables of your choice.
Bootstrap v5 is installed using npm and customised by tweaking your variables in `static/sass/custom_bootstrap_vars`.

You can find a list of available variables [in the bootstrap source](https://github.com/twbs/bootstrap/blob/v5.1.3/scss/_variables.scss), or get explanations on them in the [Bootstrap docs](https://getbootstrap.com/docs/5.1/customize/sass/).

Bootstrap's javascript as well as its dependencies are concatenated into a single file: `static/js/vendors.js`.
