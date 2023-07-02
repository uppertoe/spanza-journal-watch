#!/bin/sh
docker-compose -f spanza-journal-watch/production.yml down
docker-compose -f spanza-journal-watch/production.yml run --rm django python manage.py createsuperuser
