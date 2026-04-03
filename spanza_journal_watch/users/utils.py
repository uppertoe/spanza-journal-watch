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


def migrate_session_fulltext_to_user(session, user):
    """Move session-based full-text click records to PubmedArticleUserState.

    Called alongside star migration at signup and login.
    """
    clicked_ids = session.pop("fulltext_clicked_ids", [])
    if not clicked_ids:
        return 0

    from spanza_journal_watch.backend.models import PubmedArticle, PubmedArticleUserState

    valid_ids = set(PubmedArticle.objects.filter(pk__in=clicked_ids).values_list("pk", flat=True))
    existing = set(
        PubmedArticleUserState.objects.filter(
            user=user, article_id__in=valid_ids, full_text_clicked_at__isnull=False
        ).values_list("article_id", flat=True)
    )
    now = timezone.now()
    to_create = []
    to_update_ids = []
    for aid in valid_ids:
        if aid in existing:
            continue
        # Check if a state row already exists (e.g. from starring)
        if PubmedArticleUserState.objects.filter(user=user, article_id=aid).exists():
            to_update_ids.append(aid)
        else:
            to_create.append(PubmedArticleUserState(user=user, article_id=aid, full_text_clicked_at=now))
    if to_create:
        PubmedArticleUserState.objects.bulk_create(to_create, ignore_conflicts=True)
    if to_update_ids:
        PubmedArticleUserState.objects.filter(
            user=user, article_id__in=to_update_ids, full_text_clicked_at__isnull=True
        ).update(full_text_clicked_at=now)
    return len(to_create) + len(to_update_ids)
