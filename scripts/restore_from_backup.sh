echo "First download your backup file"
echo "docker-compose -f spanza-journal-watch/production.yml run --rm awscli download [backup on s3]"

sudo systemctl stop supervisord
docker-compose -f spanza-journal-watch/production.yml down
docker-compose -f spanza-journal-watch/production.yml up -d postgres
echo "Now use the following to restore from backup"
echo "docker-compose -f spanza-journal-watch/production.yml exec postgres restore"
