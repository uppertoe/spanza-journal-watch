#!/bin/sh
echo "Dump the Postgres db to the backups volume"
docker-compose -f spanza-journal-watch/production.yml exec postgres backup
echo "Copy the backups volume to a local folder"
docker cp $(docker-compose -f spanza-journal-watch/production.yml ps -q postgres):/backups/. ./backups
echo "Sync the local backups folder to S3"
aws s3 sync ./backups s3://spanza-journal-watch-backups/backups
