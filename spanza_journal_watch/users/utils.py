from django.utils import timezone


def migrate_session_stars_to_user(session, user):
    """Move session-based starred articles to PubmedArticleUserState records.

    Called at signup (while the session is still available) and again at login
    as a safety net for returning users who starred while logged out.
    Returns the number of new star records created.
    """
    starred_ids = session.pop("starred_article_ids", [])
    if not starred_ids:
        return 0

    from spanza_journal_watch.backend.models import PubmedArticle, PubmedArticleUserState

    # Filter to article IDs that actually exist
    valid_ids = set(PubmedArticle.objects.filter(pk__in=starred_ids).values_list("pk", flat=True))
    existing = set(
        PubmedArticleUserState.objects.filter(user=user, article_id__in=valid_ids).values_list("article_id", flat=True)
    )
    now = timezone.now()
    new_states = [
        PubmedArticleUserState(user=user, article_id=aid, starred_at=now) for aid in valid_ids if aid not in existing
    ]
    if new_states:
        PubmedArticleUserState.objects.bulk_create(new_states, ignore_conflicts=True)
    return len(new_states)
