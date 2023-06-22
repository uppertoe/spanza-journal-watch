#!/bin/sh
# This script is run by userdata associated with the
# EC2 instance to ensure docker-compose
# is run on instance startup
echo "Take docker-compose offline"
docker-compose -f spanza-journal-watch/production.yml down
echo "Pull the latest version from GitHub"
git -C spanza-journal-watch pull
docker-compose -f spanza-journal-watch/production.yml build
docker-compose -f spanza-journal-watch/production.yml run --rm django python manage.py migrate
echo "Start docker-compose"
docker-compose -f spanza-journal-watch/production.yml up -d
echo "Check filesystem space"
df -h
