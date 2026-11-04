import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_out
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(user_logged_out)
def revoke_planka_sessions_on_logout(sender, request, user, **kwargs):
    """Delete the user's Planka sessions on logout.

    Planka keeps its own session (the httpOnlyToken cookie) once the gateway
    has signed a person in. Deleting the session rows invalidates that cookie
    on Planka's next request, so the person signs in again through the gateway
    check (backend/planka_gateway.py).
    """
    if user is None:
        return

    _revoke_planka_db_sessions(user)


def _revoke_planka_db_sessions(user):
    """Delete all Planka session rows for this user."""

    db_url = (getattr(settings, "PLANKA_DB_URL", "") or "").strip()
    if not db_url:
        return

    # Find the user's Planka ID from any IssueContributor record or by email lookup
    planka_user_id = _resolve_planka_user_id(user, db_url)
    if not planka_user_id:
        return

    try:
        import psycopg2

        conn = psycopg2.connect(db_url)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM session WHERE user_id = %s", (planka_user_id,))
                    if cur.rowcount:
                        logger.info("Revoked %d Planka session(s) for user %s", cur.rowcount, user.pk)
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to revoke Planka sessions for user %s", user.pk)


def _resolve_planka_user_id(user, db_url):
    """Look up the Planka user ID by email."""
    try:
        import psycopg2

        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM user_account WHERE LOWER(email) = LOWER(%s)",
                    (user.email,),
                )
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to look up Planka user ID for user %s", user.pk)
        return None
