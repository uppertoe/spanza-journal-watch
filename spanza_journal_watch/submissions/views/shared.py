"""Helpers shared by the public review, issue, topic and search pages.

Absolute and canonical URLs, share links, meta descriptions and the display fields
attached to review querysets.
"""

from urllib.parse import urlencode

from django.db.models import Count

from spanza_journal_watch.backend.models import (
    PubmedArticleUserState,
)
from spanza_journal_watch.utils.functions import get_domain_url, shorten_text


def build_share_urls(
    share_title,
    canonical_url,
    share_description="",
    *,
    journal_name="",
    email_summary="",
):
    share_text = "\n".join(part for part in [share_title, "", canonical_url] if part)
    trimmed_email_summary = shorten_text(email_summary or share_description, 900).strip()
    email_lines = [
        "This Journal Watch review is being shared with you from SPANZA Journal Watch.",
        "",
        f"Review: {share_title.removeprefix('SPANZA Journal Watch - ').strip()}",
    ]
    if journal_name:
        email_lines.append(f"Journal: {journal_name}")
    if trimmed_email_summary:
        email_lines.extend(
            [
                "",
                "A brief summary is below. You can read the full review at the link.",
                "",
                trimmed_email_summary,
            ]
        )
    email_lines.extend(["", "Read the review:", canonical_url])
    email_body = "\n".join(email_lines)
    return {
        "share_text": share_text,
        "bluesky_share_url": f"https://bsky.app/intent/compose?{urlencode({'text': share_text})}",
        "x_share_url": f"https://twitter.com/intent/tweet?{urlencode({'text': share_title, 'url': canonical_url})}",
        "facebook_share_url": f"https://www.facebook.com/sharer/sharer.php?{urlencode({'u': canonical_url})}",
        "email_share_url": f"mailto:?{urlencode({'subject': share_title, 'body': email_body})}",
    }


def build_absolute_url(path):
    return f"{get_domain_url()}{path}"


def build_request_absolute_url(request, path):
    if request is None:
        return build_absolute_url(path)
    return request.build_absolute_uri(path)


# Longest article title we place in <title>; longer ones are cut at a word boundary.
SEO_TITLE_MAX_LENGTH = 150
# Search engines flag descriptions shorter than this as too thin to be useful.
META_DESCRIPTION_MIN_LENGTH = 120


def build_review_meta_description(summary, article):
    """Return a meta description for a review, padding thin summaries with context."""
    summary = " ".join((summary or "").split())  # meta content must be a single line
    if len(summary) >= META_DESCRIPTION_MIN_LENGTH:
        return summary
    journal_name = article.source_journal_name or (str(article.journal) if article.journal else "")
    if journal_name:
        suffix = f"A SPANZA Journal Watch review of research published in {journal_name}."
    else:
        suffix = "A SPANZA Journal Watch review of recent paediatric anaesthesia research."
    return f"{summary} {suffix}".strip()


def build_paginated_canonical_url(request, path):
    # Keep ?page=N (pages after the first are distinct content) but drop all
    # other query params so tracking URLs don't declare themselves canonical
    canonical = build_request_absolute_url(request, path)
    page = request.GET.get("page", "") if request is not None else ""
    if page.isdigit() and int(page) > 1:
        canonical = f"{canonical}?page={int(page)}"
    return canonical


def attach_review_display_fields(reviews, *, issue=None, include_share_context=False, request=None):
    review_list = list(reviews)
    if not review_list:
        return review_list

    for review in review_list:
        review.display_review_date = issue.date if issue and issue.date else review.get_review_date()

        if include_share_context:
            article_title = review.article.get_title().strip()
            review_share_title = f"SPANZA Journal Watch - {article_title}"
            review_canonical_url = build_request_absolute_url(request, review.get_absolute_url())
            review_share_description = review.get_truncated_body().strip()
            review_share_email_summary = review.get_plain_body().strip()
            review_share_context = build_share_urls(
                review_share_title,
                review_canonical_url,
                review_share_description,
                journal_name=str(review.article.journal),
                email_summary=review_share_email_summary,
            )

            review.share_title = review_share_title
            review.canonical_url = review_canonical_url
            review.share_description = review_share_description
            review.share_email_summary = review_share_email_summary
            review.share_text = review_share_context["share_text"]
            review.bluesky_share_url = review_share_context["bluesky_share_url"]
            review.x_share_url = review_share_context["x_share_url"]
            review.facebook_share_url = review_share_context["facebook_share_url"]
            review.email_share_url = review_share_context["email_share_url"]

    # Attach star state + star counts per review for star buttons
    if request:
        article_ids = [r.article_id for r in review_list]
        # Star counts per article
        star_count_map = {}
        if article_ids:
            star_counts = (
                PubmedArticleUserState.objects.filter(
                    article_id__in=article_ids,
                    starred_at__isnull=False,
                )
                .values("article_id")
                .annotate(count=Count("id"))
            )
            star_count_map = {row["article_id"]: row["count"] for row in star_counts}

        if request.user.is_authenticated:
            state_map = {
                s.article_id: s
                for s in PubmedArticleUserState.objects.filter(user=request.user, article_id__in=article_ids)
            }
            for review in review_list:
                review.pubmed_user_state = state_map.get(review.article_id)
                review.pubmed_session_starred = False
                review.star_target_id = f"review-star-actions-{review.pk}"
                review.star_count = star_count_map.get(review.article_id, 0)
        else:
            starred_ids = set(request.session.get("starred_article_ids", []))
            for review in review_list:
                review.pubmed_user_state = None
                review.pubmed_session_starred = review.article_id in starred_ids
                review.star_target_id = f"review-star-actions-{review.pk}"
                review.star_count = star_count_map.get(review.article_id, 0)

    return review_list
