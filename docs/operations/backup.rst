Backup and Restore
==================

.. contents:: Contents
   :local:
   :depth: 2

Overview
--------

Two PostgreSQL databases are backed up to a Restic_ repository on Amazon S3:

- **Django database** — the main application database (users, issues, reviews,
  newsletters, subscribers).
- **Planka database** — the Planka kanban board used for the editorial workflow.

Media files (article images, Planka attachments) are stored directly in S3 with
versioning enabled, so S3 itself provides their durability. They are not
included in the Restic backups.

Backups run daily at **02:00 UTC** via a systemd timer on the VPS host.
The backup process runs *outside* the Docker stack so that a problem with
the application containers does not affect the ability to back up or restore.

.. _Restic: https://restic.net

Architecture
------------

.. code-block:: text

   VPS host (systemd timer)
   │
   ├─ /opt/backup/backup.sh  ──► pg_dump ──► restic ──► S3 bucket /backups
   │       │                        │
   │       │               connects to 127.0.0.1:5432 (Django postgres)
   │       │               connects to 127.0.0.1:5433 (Planka postgres)
   │       │
   │       └─► msmtp ──► Amazon SES ──► admin email
   │                     (on failure, or success if NOTIFY_ON_SUCCESS=true)
   │
   └─ /opt/backup/restore.sh ─► restic dump ──► psql ──► target database

The ``minimal.dockerhub.yml`` compose file exposes both postgres containers on
the loopback interface (``127.0.0.1:5432`` and ``127.0.0.1:5433``) so the
host-side scripts can connect without entering the Docker network.

Scripts
-------

Both scripts live in ``compose/backup/`` in the repository.

``backup.sh``
~~~~~~~~~~~~~

Backs up both databases to the configured Restic repository and optionally
verifies the repository afterwards.

.. code-block:: bash

   /opt/backup/backup.sh [--dry-run] [--verify]

``--dry-run``
    Print every command without executing it.  Useful for testing configuration.

``--verify``
    Run ``restic check`` and list recent snapshots after backing up.

``restore.sh``
~~~~~~~~~~~~~~

Restores one or both databases from a Restic snapshot.

.. code-block:: bash

   /opt/backup/restore.sh [OPTIONS]

   --database django|planka|all    Database to restore (default: all).
   --snapshot ID                   Restic snapshot ID (default: latest).
   --target DBNAME                 Target database name.  See Safety below.
   --list                          List available snapshots and exit.
   --dry-run                       Print commands without executing.

Environment variables
---------------------

All configuration is read from ``/etc/restic/env`` on the production host, or
from Docker Compose environment variables when testing locally.  A template is
at ``compose/production/backup/env.example``.

.. list-table::
   :header-rows: 1
   :widths: 30 10 60

   * - Variable
     - Required
     - Description
   * - ``RESTIC_REPOSITORY``
     - Yes
     - Restic repo URL.  ``s3:s3.amazonaws.com/bucket/prefix`` for S3,
       ``/restic-repo`` for the local test volume.
   * - ``RESTIC_PASSWORD``
     - Yes
     - Restic encryption passphrase.  **Store this in a password manager —
       losing it makes all backups unreadable.**
   * - ``POSTGRES_HOST``
     - Yes
     - Django PostgreSQL host.  ``127.0.0.1`` on the VPS, ``postgres`` in
       the local Docker network.
   * - ``POSTGRES_DB``
     - Yes
     - Django database name (e.g. ``spanza_journal_watch``).
   * - ``POSTGRES_USER``
     - Yes
     - Django database user.
   * - ``POSTGRES_PASSWORD``
     - Yes
     - Django database password.
   * - ``POSTGRES_PORT``
     - No
     - Django PostgreSQL port (default: ``5432``).
   * - ``PLANKA_DB_HOST``
     - Yes
     - Planka PostgreSQL host.  ``127.0.0.1`` on the VPS, ``planka_postgres``
       locally.
   * - ``PLANKA_DB_PORT``
     - No
     - Planka PostgreSQL port (default: ``5432``).  On the VPS this is
       ``5433`` because Planka postgres is mapped to that host port.
   * - ``KEEP_DAILY``
     - No
     - Daily snapshots to retain (default: ``7``).
   * - ``KEEP_WEEKLY``
     - No
     - Weekly snapshots to retain (default: ``4``).
   * - ``KEEP_MONTHLY``
     - No
     - Monthly snapshots to retain (default: ``6``).
   * - ``ALERT_EMAIL``
     - No
     - Recipient address for failure (and optionally success) notifications.
       No email is sent if this is unset.
   * - ``SMTP_HOST``
     - No
     - SMTP server hostname (default: ``localhost``).
   * - ``SMTP_PORT``
     - No
     - SMTP port (default: ``587``).
   * - ``SMTP_TLS``
     - No
     - ``on`` or ``off`` (default: ``on``).  Set to ``off`` for Mailhog.
   * - ``SMTP_USER``
     - No
     - SMTP username.  Omit for unauthenticated servers (Mailhog).
   * - ``SMTP_PASSWORD``
     - No
     - SMTP password.  Required when ``SMTP_USER`` is set.
   * - ``SMTP_FROM``
     - No
     - Sender address (default: ``backup@localhost``).
   * - ``NOTIFY_ON_SUCCESS``
     - No
     - Set to ``true`` to also send an email on successful backup
       (default: ``false``).
   * - ``AWS_ACCESS_KEY_ID``
     - S3 only
     - IAM access key with S3 permissions on the backup bucket.
   * - ``AWS_SECRET_ACCESS_KEY``
     - S3 only
     - IAM secret key.
   * - ``AWS_DEFAULT_REGION``
     - S3 only
     - AWS region (e.g. ``ap-southeast-2``).

Testing locally
---------------

A dedicated Docker Compose service (``backup`` profile) provides a pre-built
container with restic, msmtp, and postgresql-client.  The backup scripts are
bind-mounted from ``compose/backup/`` so changes on the host are reflected
immediately without rebuilding.

Notification emails are sent to **Mailhog** (no real email is dispatched),
viewable at http://localhost:8025.

The Restic repository is stored in a named Docker volume (``backup_repo``)
instead of S3.

Starting the test environment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Bring up the base stack (postgres + mailhog required)
   docker compose -f local.yml up -d postgres mailhog

   # Optional: include Planka if you want to test Planka backups
   docker compose -f local.yml --profile planka up -d planka planka_postgres

Common test commands
~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   COMPOSE="docker compose -f local.yml --profile backup run --rm backup"

   # 1. Dry run — verify configuration and see what would run
   $COMPOSE /backup/backup.sh --dry-run

   # 2. Full backup with repository verification (emails land in Mailhog)
   $COMPOSE /backup/backup.sh --verify

   # 3. List snapshots
   $COMPOSE /backup/restore.sh --list

   # 4. Safe restore test — restores Django into a temporary database
   #    that does not touch the live 'spanza_journal_watch' database
   $COMPOSE /backup/restore.sh --database django --target django_restore_test

   # 5. Safe restore test for Planka
   $COMPOSE /backup/restore.sh --database planka --target planka_restore_test

   # 6. Interactive shell for ad-hoc investigation
   $COMPOSE bash

Verifying a test restore
~~~~~~~~~~~~~~~~~~~~~~~~~

After a test restore into a temporary database, connect to it and inspect the
data before committing to a live restore:

.. code-block:: bash

   # Connect to the test database
   docker exec -it spanza_journal_watch_local_postgres \
     psql -U $POSTGRES_USER -d django_restore_test

   -- Spot-check some tables
   SELECT COUNT(*) FROM django_migrations;
   SELECT COUNT(*) FROM submissions_issue;
   SELECT name FROM submissions_issue ORDER BY created_at DESC LIMIT 5;

   -- Clean up when done
   DROP DATABASE django_restore_test;

Production setup
----------------

Run the install script once on the VPS after cloning the repository:

.. code-block:: bash

   cd /path/to/spanza_journal_watch
   sudo bash compose/production/backup/install.sh

The script installs dependencies, copies scripts to ``/opt/backup/``, installs
the systemd units, and enables the timer.  It does **not** start the backup.

After installation
~~~~~~~~~~~~~~~~~~

1. **Fill in credentials** — edit ``/etc/restic/env`` (created from the
   template at ``compose/production/backup/env.example``):

   .. code-block:: bash

      sudo nano /etc/restic/env
      sudo chmod 600 /etc/restic/env

2. **Initialise the repository** — this only needs to be done once:

   .. code-block:: bash

      source /etc/restic/env
      restic init

3. **Run a manual backup to verify**:

   .. code-block:: bash

      sudo systemctl start backup.service
      journalctl -u backup.service -f

4. **Confirm the timer**:

   .. code-block:: bash

      systemctl list-timers backup.timer

Monitoring
----------

**Failure notifications** are sent automatically by ``backup.sh`` via msmtp
when any step fails.  The email includes the hostname, timestamp, script line
number, and exit code, plus a command to view the full journal output.

**Journal logs** contain the complete output of every backup run:

.. code-block:: bash

   # Live output during a run
   journalctl -u backup.service -f

   # Last 100 lines from the most recent run
   journalctl -u backup.service -n 100

   # All runs from today
   journalctl -u backup.service --since today

**Timer status**:

.. code-block:: bash

   systemctl list-timers backup.timer
   systemctl status backup.service

Restore procedures
------------------

Safety model
~~~~~~~~~~~~

``restore.sh`` distinguishes between safe and destructive restores based on
the ``--target`` option:

- ``--target`` set to a **different** database name: creates a new database
  and restores into it.  The live database is untouched.  Inspect and verify
  before deciding to swap.
- ``--target`` set to the **real** database name (or omitted): overwrites the
  live database.  Requires typing the database name to confirm unless
  ``RESTORE_NO_CONFIRM=true`` is set.

**Always prefer the safe path:** restore to a test database first, verify the
data, then swap if satisfied.

Listing snapshots
~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # On the VPS
   source /etc/restic/env && restic snapshots

   # In the local test container
   docker compose -f local.yml --profile backup run --rm backup \
     /backup/restore.sh --list

Safe restore (recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~

Restore into a temporary database and inspect before promoting to live:

.. code-block:: bash

   source /etc/restic/env

   # Restore Django into a test database
   /opt/backup/restore.sh --database django --target django_restored

   # Connect and verify
   psql -h 127.0.0.1 -U $POSTGRES_USER -d django_restored
   # \dt, SELECT COUNT(*) FROM submissions_issue, etc.

   # If satisfied, stop the app and swap databases
   docker compose -f /path/to/minimal.dockerhub.yml stop django celeryworker celerybeat

   psql -h 127.0.0.1 -U $POSTGRES_USER -d postgres <<SQL
     SELECT pg_terminate_backend(pid)
       FROM pg_stat_activity
      WHERE datname = 'spanza_journal_watch';
     DROP DATABASE spanza_journal_watch;
     ALTER DATABASE django_restored RENAME TO spanza_journal_watch;
   SQL

   docker compose -f /path/to/minimal.dockerhub.yml start django celeryworker celerybeat

Destructive restore (last resort)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you cannot use the safe path (e.g. disk is full and the test database cannot
be created):

.. code-block:: bash

   source /etc/restic/env

   # Stop the application to prevent writes during restore
   docker compose -f /path/to/minimal.dockerhub.yml stop django celeryworker celerybeat

   # Restore — will prompt for confirmation
   /opt/backup/restore.sh --database django

   # Start the application
   docker compose -f /path/to/minimal.dockerhub.yml start django celeryworker celerybeat

Restoring from a specific snapshot
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # List snapshots to find the ID
   restic snapshots

   # Restore from that snapshot
   /opt/backup/restore.sh --database django --snapshot abc1234 --target django_restored

Updating the scripts
--------------------

The scripts live in the repository.  To update them on the VPS after a
``git pull``:

.. code-block:: bash

   cd /path/to/spanza_journal_watch
   sudo install -m 750 compose/backup/backup.sh  /opt/backup/backup.sh
   sudo install -m 750 compose/backup/restore.sh /opt/backup/restore.sh

The systemd units only need to be reinstalled if ``backup.service`` or
``backup.timer`` change:

.. code-block:: bash

   sudo install -m 644 compose/production/backup/backup.service /etc/systemd/system/
   sudo install -m 644 compose/production/backup/backup.timer   /etc/systemd/system/
   sudo systemctl daemon-reload
