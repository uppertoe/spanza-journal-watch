cd ~
docker-compose -f spanza-journal-watch/production.yml --verbose build

echo "Users must be migrated separately"
docker-compose -f spanza-journal-watch/production.yml run --rm django python manage.py migrate users
docker-compose -f spanza-journal-watch/production.yml run --rm django python manage.py migrate
