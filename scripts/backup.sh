#!/bin/sh
echo "Dump the Postgres db to the backups volume"
#~/.local/bin/docker-compose -f /home/ec2-user/spanza-journal-watch/production.yml exec -T postgres backup
/usr/local/bin/docker-compose -f /home/ec2-user/spanza-journal-watch/production.yml exec -T postgres backup
echo "Copy the backup to S3"
#~/.local/bin/docker-compose -f /home/ec2-user/spanza-journal-watch/production.yml run --rm awscli upload
/usr/local/bin/docker-compose -f /home/ec2-user/spanza-journal-watch/production.yml run --rm awscli upload
