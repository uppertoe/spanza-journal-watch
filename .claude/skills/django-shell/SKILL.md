---
name: django-shell
description: Run a Python snippet inside the local Django container
allowed-tools: Bash
---

To run code inside the local Django container, always use `/entrypoint` so that
environment variables (DATABASE_URL, etc.) are loaded correctly:

```bash
docker exec spanza_journal_watch_local_django /entrypoint python manage.py shell -c "<python code>"
```

Plain `docker exec ... python manage.py shell` will fail with
`ImproperlyConfigured: Set the DATABASE_URL environment variable` because the
env vars from `.envs/` are not available in a bare `docker exec` shell session.

Example — check a user's display name:

```bash
docker exec spanza_journal_watch_local_django /entrypoint python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
for u in User.objects.all():
    print(repr(u.email), repr(getattr(u, 'name', None)))
"
```

Example — run a management command:

```bash
docker exec spanza_journal_watch_local_django /entrypoint python manage.py migrate --check
```
