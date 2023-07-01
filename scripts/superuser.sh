#!/bin/sh
docker-compose -f spanza-journal-watch/production.yml exec django /entrypoint python manage.py createsuperuser
