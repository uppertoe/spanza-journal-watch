"""The journal browser: shelf, month navigation, article list, reading list and per-article actions."""

import datetime
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from spanza_journal_watch.backend.models import (
    PubmedArticle,
    PubmedArticleUserState,
    PubmedArticleVisitorRecommendation,  # noqa: I001
    WatchedJournal,
    WatchedJournalArticle,
    can_recommend_pubmed_articles,
)
from spanza_journal_watch.backend.pubmed_cache import article_matches_topic, article_metadata_list, shift_month
from spanza_journal_watch.utils.mixins import AnonymousCacheMixin
from spanza_journal_watch.utils.seo import noindex_response

from ..models import Review, Tag
from .shared import build_request_absolute_url

# ---------------------------------------------------------------------------
# Journal browser: section grouping for table-of-contents
# ---------------------------------------------------------------------------

JOURNAL_SECTIONS = [
    ("Editorials", "editorials", {"Editorial", "Comment", "Published Erratum", "Introductory Journal Article"}),
    (
        "Reviews",
        "reviews",
        {"Review", "Systematic Review", "Meta-Analysis", "Scoping Review", "Network Meta-Analysis"},
    ),
    ("Guidelines", "guidelines", {"Practice Guideline", "Consensus Statement"}),
    (
        "Original Research",
        "original-research",
        {
            "Randomized Controlled Trial",
            "Clinical Trial",
            "Clinical Trial, Phase I",
            "Clinical Trial, Phase II",
            "Clinical Trial, Phase III",
            "Clinical Trial, Phase IV",
            "Observational Study",
            "Multicenter Study",
            "Comparative Study",
            "Equivalence Trial",
            "Pragmatic Clinical Trial",
            "Validation Study",
        },
    ),
    ("Case Reports", "case-reports", {"Case Reports"}),
    ("Letters", "letters", {"Letter"}),
]

_PAEDIATRIC_MESH_TERMS = {
    "Pediatrics",
    "Infant",
    "Infant, Newborn",
    "Child",
    "Child, Preschool",
    "Adolescent",
}
_PAEDIATRIC_TEXT_TERMS = {
    "pediatric",
    "paediatric",
    "child",
    "children",
    "infant",
    "newborn",
    "neonat",
    "adolescent",
}

IGNORED_PUBLICATION_TYPES = {
    "Journal Article",
    "Research Support, Non-U.S. Gov't",
    "Research Support, U.S. Gov't, P.H.S.",
    "Research Support, U.S. Gov't, Non-P.H.S.",
    "Research Support, N.I.H., Extramural",
    "Research Support, N.I.H., Intramural",
    "Video-Audio Media",
    "Historical Article",
    "Lecture",
}


def _group_articles_by_section(rows):
    """Group article rows into (section_name, section_slug, [rows]) tuples.

    Each article is placed in the first matching section based on its
    publication_types, with title-based heuristics as a fallback for articles
    that PubMed only tags as "Journal Article".  Empty sections are omitted.
    """
    import re

    # Title patterns that indicate correspondence (Comment/Reply at end of title)
    _correspondence_re = re.compile(r":\s*(Comment|Reply|Response|Correspondence|Authors?\s*Reply)\.?\s*$", re.I)

    buckets = {slug: [] for _, slug, _ in JOURNAL_SECTIONS}
    buckets["articles"] = []  # fallback

    for row in rows:
        ptypes = set(row.publication_types) - IGNORED_PUBLICATION_TYPES
        placed = False
        for _name, slug, type_set in JOURNAL_SECTIONS:
            if ptypes & type_set:
                buckets[slug].append(row)
                placed = True
                break
        if not placed:
            # Title-based heuristic: articles ending with ": Comment." etc. go to Letters
            title = row.article.title or ""
            if _correspondence_re.search(title):
                buckets["letters"].append(row)
                placed = True
        if not placed:
            buckets["articles"].append(row)

    sections = []
    for name, slug, _ in JOURNAL_SECTIONS:
        if buckets[slug]:
            sections.append((name, slug, buckets[slug]))
    if buckets["articles"]:
        sections.append(("Articles", "articles", buckets["articles"]))
    return sections


def _parse_journal_month(value):
    text = (value or "").strip()
    if not text:
        return timezone.now().date().replace(day=1)
    try:
        return datetime.datetime.strptime(text, "%Y-%m").date().replace(day=1)
    except ValueError:
        return timezone.now().date().replace(day=1)


def _best_default_month(journal_id, min_articles=10):
    """Find the most recent month with at least `min_articles` for this journal.

    Falls back to the most recent month with any articles, or the current month.
    """
    from django.db.models import Count as _Count

    months = (
        WatchedJournalArticle.objects.filter(watched_journal_id=journal_id)
        .values("publication_month")
        .annotate(article_count=_Count("id"))
        .order_by("-publication_month")[:12]
    )
    fallback = None
    for row in months:
        if fallback is None:
            fallback = row["publication_month"]
        if row["article_count"] >= min_articles:
            return row["publication_month"]
    return fallback or timezone.now().date().replace(day=1)


def _journal_month_options():
    months = list(
        WatchedJournalArticle.objects.exclude(publication_month__isnull=True)
        .values_list("publication_month", flat=True)
        .distinct()
        .order_by("-publication_month")[:24]
    )
    current_month = timezone.now().date().replace(day=1)
    if current_month not in months:
        months.insert(0, current_month)
    return months


def _journal_browser_context(request):
    """Build context for the single-journal browsable view."""
    active_journals = list(WatchedJournal.objects.filter(active=True, visible_on_frontend=True).order_by("name", "pk"))
    shelf_tones = ["cobalt", "sunset", "sage", "berry", "ochre", "marine", "rose", "slate"]
    hidden_journal_ids = set(request.session.get("hidden_shelf_journals", []))
    for journal in active_journals:
        journal.shelf_tone = shelf_tones[journal.pk % len(shelf_tones)]
        journal.shelf_hidden = journal.pk in hidden_journal_ids

    # --- Determine selected journal (single, not multi) ---
    raw_journal = request.GET.get("journal")
    selected_journal_id = int(raw_journal) if raw_journal and str(raw_journal).isdigit() else None

    if selected_journal_id is None:
        if request.user.is_authenticated and getattr(request.user, "last_viewed_journal_id", None):
            selected_journal_id = request.user.last_viewed_journal_id
        else:
            selected_journal_id = request.session.get("last_viewed_journal_id")

    # Validate the ID exists among active journals
    active_ids = {j.pk for j in active_journals}
    if selected_journal_id not in active_ids:
        selected_journal_id = active_journals[0].pk if active_journals else None

    selected_journal = next((j for j in active_journals if j.pk == selected_journal_id), None)

    # Persist last-viewed
    if selected_journal_id:
        request.session["last_viewed_journal_id"] = selected_journal_id
        if request.user.is_authenticated and request.user.last_viewed_journal_id != selected_journal_id:
            from django.contrib.auth import get_user_model

            get_user_model().objects.filter(pk=request.user.pk).update(last_viewed_journal_id=selected_journal_id)

    # --- Month selection + prev/next ---
    raw_month = request.GET.get("month")
    if raw_month:
        selected_month = _parse_journal_month(raw_month)
    else:
        # Find the most recent month with a reasonable number of articles for this journal
        selected_month = _best_default_month(selected_journal_id)
    prev_month = shift_month(selected_month, -1)
    next_month = shift_month(selected_month, 1)
    has_prev_month = WatchedJournalArticle.objects.filter(
        watched_journal_id=selected_journal_id, publication_month=prev_month
    ).exists()
    has_next_month = WatchedJournalArticle.objects.filter(
        watched_journal_id=selected_journal_id, publication_month=next_month
    ).exists()

    # --- Fetch articles for this journal + month ---
    article_links = (
        WatchedJournalArticle.objects.filter(
            publication_month=selected_month,
            watched_journal_id=selected_journal_id,
        )
        .select_related("article", "watched_journal")
        .annotate(
            recommendation_count=Count(
                "article__user_states",
                filter=Q(article__user_states__recommended_at__isnull=False),
                distinct=True,
            ),
            visitor_recommendation_count=Count("article__visitor_recommendations", distinct=True),
            star_count=Count(
                "article__user_states",
                filter=Q(article__user_states__starred_at__isnull=False),
                distinct=True,
            ),
        )
        .order_by("-article__publication_date", "-article__publication_month", "article__title")
    )

    article_links = list(article_links)
    user_state_map = {}
    if request.user.is_authenticated:
        user_state_map = {
            state.article_id: state
            for state in PubmedArticleUserState.objects.filter(
                user=request.user, article_id__in=[link.article_id for link in article_links]
            )
        }

    session_starred_ids = set()
    session_recommended_ids = set()
    session_fulltext_ids = set()
    if not request.user.is_authenticated:
        session_starred_ids = set(request.session.get("starred_article_ids", []))
        session_recommended_ids = set(request.session.get("recommended_article_ids", []))
        session_fulltext_ids = set(request.session.get("fulltext_clicked_ids", []))

    # Build PubmedArticle.pk → Review lookup for articles that have been reviewed
    pubmed_ids = [link.article_id for link in article_links]
    review_map = {}
    if pubmed_ids:
        reviewed = Review.objects.filter(active=True, article_id__in=pubmed_ids).select_related("author")
        for rev in reviewed:
            review_map.setdefault(rev.article_id, rev)

    # --- Parse filter state from query params, falling back to session, then default on ---
    filter_paediatric = request.GET.get("paediatric", request.session.get("jw_filter_paediatric", "1")) == "1"
    filter_has_abstract = request.GET.get("has_abstract", request.session.get("jw_filter_has_abstract", "1")) == "1"
    request.session["jw_filter_paediatric"] = "1" if filter_paediatric else "0"
    request.session["jw_filter_has_abstract"] = "1" if filter_has_abstract else "0"

    rows = []
    total_unfiltered = 0
    seen_article_ids = set()
    for link in article_links:
        if link.article_id in seen_article_ids:
            continue
        seen_article_ids.add(link.article_id)
        total_unfiltered += 1
        link.user_state = user_state_map.get(link.article_id)
        link.session_starred = link.article_id in session_starred_ids
        link.session_recommended = link.article_id in session_recommended_ids
        link.publication_types = article_metadata_list(link.article, "publication_types")
        link.mesh_terms = article_metadata_list(link.article, "mesh_terms")
        link.keywords = article_metadata_list(link.article, "keywords")
        link.is_paediatric = article_matches_topic(
            link.article,
            mesh_terms=_PAEDIATRIC_MESH_TERMS,
            text_terms=_PAEDIATRIC_TEXT_TERMS,
        )
        link.review = review_map.get(link.article_id)
        if request.user.is_authenticated:
            state = user_state_map.get(link.article_id)
            link.full_text_read = bool(state and state.full_text_clicked_at)
        else:
            link.full_text_read = link.article_id in session_fulltext_ids

        # Apply server-side filters
        if filter_paediatric and not link.is_paediatric:
            continue
        if filter_has_abstract and not link.article.abstract:
            continue

        rows.append(link)

    sections = _group_articles_by_section(rows)
    _attach_related_reviews(rows)

    return {
        "rows": rows,
        "sections": sections,
        "active_journals": active_journals,
        "selected_journal_id": selected_journal_id,
        "selected_journal": selected_journal,
        "selected_month": selected_month,
        "prev_month": prev_month,
        "next_month": next_month,
        "has_prev_month": has_prev_month,
        "has_next_month": has_next_month,
        "can_recommend": can_recommend_pubmed_articles(request.user),
        "session_starred_ids": session_starred_ids,
        "filter_paediatric": filter_paediatric,
        "filter_has_abstract": filter_has_abstract,
        "all_filtered_out": total_unfiltered > 0 and len(rows) == 0,
        "hidden_journal_count": sum(1 for j in active_journals if j.shelf_hidden),
    }


def _attach_related_reviews(rows):
    """Batch-attach related reviews to all journal browser articles based on shared curated tags.

    Uses two queries total (tag links + candidate reviews) instead of one per row.
    """
    if not rows:
        return

    article_ids = [r.article_id for r in rows]

    # 1) Fetch curated tag IDs per article in one query
    tag_links = Tag.objects.filter(curated=True, active=True, articles__id__in=article_ids).values_list(
        "articles__id", "id"
    )
    article_tag_map: dict[int, set[int]] = {}
    all_tag_ids: set[int] = set()
    for article_id, tag_id in tag_links:
        article_tag_map.setdefault(article_id, set()).add(tag_id)
        all_tag_ids.add(tag_id)

    if not all_tag_ids:
        for row in rows:
            row.related_reviews = []
        return

    # 2) Fetch all candidate related reviews in one query, excluding current page articles
    article_id_set = set(article_ids)
    TagArticle = Tag.articles.through
    candidate_reviews = list(
        Review.objects.filter(active=True)
        .filter(Exists(TagArticle.objects.filter(pubmedarticle_id=OuterRef("article_id"), tag_id__in=all_tag_ids)))
        .exclude(article_id__in=article_id_set)
        .select_related("article__journal", "author")
        .prefetch_related("article__tags")
    )

    # Build review → tag_id set lookup from prefetched tags
    review_tag_map: dict[int, set[int]] = {}
    for review in candidate_reviews:
        review_tag_map[review.pk] = {t.pk for t in review.article.tags.all()}

    # 3) Distribute reviews to rows based on shared tag overlap
    for row in rows:
        row_tag_ids = article_tag_map.get(row.article_id, set())
        if not row_tag_ids:
            row.related_reviews = []
            continue

        scored = []
        for review in candidate_reviews:
            shared = len(review_tag_map.get(review.pk, set()) & row_tag_ids)
            if shared:
                scored.append((shared, review))
        scored.sort(key=lambda x: (-x[0], -(x[1].publish_date or datetime.date.min).toordinal()))
        row.related_reviews = [review for _, review in scored[:2]]


def _attach_related_reviews_to_issue_page(reviews):
    """Batch-attach related reviews to issue detail page reviews (same pattern as journal browser)."""
    if not reviews:
        return

    article_ids = [r.article_id for r in reviews]

    tag_links = Tag.objects.filter(curated=True, active=True, articles__id__in=article_ids).values_list(
        "articles__id", "id"
    )
    article_tag_map: dict[int, set[int]] = {}
    all_tag_ids: set[int] = set()
    for article_id, tag_id in tag_links:
        article_tag_map.setdefault(article_id, set()).add(tag_id)
        all_tag_ids.add(tag_id)

    if not all_tag_ids:
        for review in reviews:
            review.related_reviews = []
        return

    article_id_set = set(article_ids)
    TagArticle = Tag.articles.through
    candidate_reviews = list(
        Review.objects.filter(active=True)
        .filter(Exists(TagArticle.objects.filter(pubmedarticle_id=OuterRef("article_id"), tag_id__in=all_tag_ids)))
        .exclude(article_id__in=article_id_set)
        .select_related("article__journal", "author")
        .prefetch_related("article__tags")
    )

    review_tag_map: dict[int, set[int]] = {}
    for candidate in candidate_reviews:
        review_tag_map[candidate.pk] = {t.pk for t in candidate.article.tags.all()}

    for review in reviews:
        row_tag_ids = article_tag_map.get(review.article_id, set())
        if not row_tag_ids:
            review.related_reviews = []
            continue
        scored = []
        for candidate in candidate_reviews:
            shared = len(review_tag_map.get(candidate.pk, set()) & row_tag_ids)
            if shared:
                scored.append((shared, candidate))
        scored.sort(key=lambda x: (-x[0], -(x[1].publish_date or datetime.date.min).toordinal()))
        review.related_reviews = [r for _, r in scored[:4]]


def _journal_article_actions_context(request, article):
    user_state = None
    session_starred = False
    session_recommended = False
    if request.user.is_authenticated:
        user_state = PubmedArticleUserState.objects.filter(user=request.user, article=article).first()
    else:
        session_starred = article.pk in request.session.get("starred_article_ids", [])
        session_recommended = article.pk in request.session.get("recommended_article_ids", [])
    star_count = PubmedArticleUserState.objects.filter(article=article, starred_at__isnull=False).count()
    review = Review.objects.filter(active=True, article=article).select_related("author").first()
    return {
        "article": article,
        "user_state": user_state,
        "session_starred": session_starred,
        "session_recommended": session_recommended,
        "star_count": star_count,
        "review": review,
        "can_recommend": can_recommend_pubmed_articles(request.user),
        "next_url": request.POST.get("next") or request.GET.get("next") or request.get_full_path(),
    }


class JournalListView(AnonymousCacheMixin, TemplateView):
    template_name = "submissions/journal_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_journal_browser_context(self.request))
        context["page_title"] = "Journals | Journal Watch"
        context["page_meta_description"] = (
            "Browse recent paediatric anaesthesia articles from the journals SPANZA Journal Watch follows, "
            "month by month, with filters and community recommendations for further reading."
        )
        context["canonical_url"] = build_request_absolute_url(self.request, reverse("submissions:journal_list"))
        # Every journal, month and filter combination is reachable by link and
        # shares this one canonical; keep crawlers from indexing the permutations.
        if any(key in self.request.GET for key in ("journal", "month", "paediatric", "has_abstract", "page")):
            context["meta_robots"] = "noindex,follow"
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": "Journal browser",
                "url": context["canonical_url"],
                "description": context["page_meta_description"],
            }
        )
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get("HX-Request") == "true":
            context["is_htmx"] = True
            return render(self.request, "submissions/fragments/journal_article_list.html", context, **response_kwargs)
        return super().render_to_response(context, **response_kwargs)


@require_POST
def journal_article_toggle_star(request, article_id):
    article = get_object_or_404(PubmedArticle, pk=article_id)

    if request.user.is_authenticated:
        state, _ = PubmedArticleUserState.objects.get_or_create(user=request.user, article=article)
        state.starred_at = None if state.starred_at else timezone.now()
        state.save(update_fields=["starred_at", "modified"])
    else:
        starred = request.session.get("starred_article_ids", [])
        if article.pk in starred:
            starred.remove(article.pk)
        else:
            starred.append(article.pk)
        request.session["starred_article_ids"] = starred

    if request.headers.get("HX-Request") == "true":
        source = request.POST.get("source", "")
        triggers = {}

        if source == "reading_list":
            # Return empty response to remove the card via outerHTML swap
            response = HttpResponse("")
        elif source == "review":
            # Return the review star button fragment
            star_target_id = request.POST.get("star_target_id", "")
            star_count = PubmedArticleUserState.objects.filter(article=article, starred_at__isnull=False).count()
            ctx = {"pubmed_article": article, "star_count": star_count}
            if request.POST.get("hide_star_count"):
                ctx["hide_star_count"] = True
            if star_target_id:
                ctx["star_target_id"] = star_target_id
            if request.user.is_authenticated:
                ctx["pubmed_user_state"] = PubmedArticleUserState.objects.filter(
                    user=request.user, article=article
                ).first()
            else:
                ctx["pubmed_session_starred"] = article.pk in request.session.get("starred_article_ids", [])
            response = render(request, "submissions/fragments/review_star_button.html", ctx)
        else:
            response = render(
                request,
                "submissions/fragments/journal_article_actions.html",
                _journal_article_actions_context(request, article),
            )

        if not request.user.is_authenticated:
            starred_count = len(request.session.get("starred_article_ids", []))
            triggers["showLoginPrompt"] = {"count": starred_count}

        # Notify reading list dot indicator
        triggers["starChanged"] = True
        if triggers:
            response["HX-Trigger"] = json.dumps(triggers)

        return response
    return redirect(request.POST.get("next") or reverse("submissions:journal_list"))


@csrf_exempt
@require_POST
def journal_article_mark_fulltext(request, article_id):
    """Record that a user clicked full text. Works for both authenticated and anonymous users."""
    try:
        article = PubmedArticle.objects.get(pk=article_id)
    except PubmedArticle.DoesNotExist:
        return JsonResponse({"ok": False}, status=404)

    if request.user.is_authenticated:
        state, _ = PubmedArticleUserState.objects.get_or_create(user=request.user, article=article)
        if not state.full_text_clicked_at:
            state.full_text_clicked_at = timezone.now()
            state.save(update_fields=["full_text_clicked_at"])
    else:
        clicked = request.session.get("fulltext_clicked_ids", [])
        if article.pk not in clicked:
            clicked.append(article.pk)
            request.session["fulltext_clicked_ids"] = clicked

    return JsonResponse({"ok": True})


@noindex_response
def journal_fulltext_ids(request):
    """Return article IDs the current user/session has clicked full text on."""
    if request.user.is_authenticated:
        ids = list(
            PubmedArticleUserState.objects.filter(user=request.user, full_text_clicked_at__isnull=False).values_list(
                "article_id", flat=True
            )
        )
    else:
        ids = request.session.get("fulltext_clicked_ids", [])
    return JsonResponse({"ids": ids})


@csrf_exempt
@require_POST
def journal_shelf_hide(request, journal_id):
    """Add a journal to the hidden shelf list (stored in session)."""
    hidden = request.session.get("hidden_shelf_journals", [])
    if journal_id not in hidden:
        hidden.append(journal_id)
        request.session["hidden_shelf_journals"] = hidden
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def journal_shelf_show_all(request):
    """Clear the hidden shelf list."""
    request.session.pop("hidden_shelf_journals", None)
    return JsonResponse({"ok": True})


@require_POST
def journal_article_toggle_recommend(request, article_id):
    """Recommend an article for review, signed in or not.

    Members are recorded on their PubmedArticleUserState. Visitors are recorded
    per browser session in PubmedArticleVisitorRecommendation and counted
    separately for coordinators; requests that look automated are ignored so
    crawlers cannot pad the tally. Visitor recommendations are attributed to
    the account if the person later signs in.
    """
    article = get_object_or_404(PubmedArticle, pk=article_id)

    if request.user.is_authenticated:
        if not can_recommend_pubmed_articles(request.user):
            messages.error(request, "You do not have permission to recommend articles yet.")
            return redirect(request.POST.get("next") or reverse("submissions:journal_list"))
        state, _ = PubmedArticleUserState.objects.get_or_create(user=request.user, article=article)
        state.recommended_at = None if state.recommended_at else timezone.now()
        state.save(update_fields=["recommended_at", "modified"])
    else:
        _toggle_visitor_recommendation(request, article)

    if request.headers.get("HX-Request") == "true":
        response = render(
            request,
            "submissions/fragments/journal_article_actions.html",
            _journal_article_actions_context(request, article),
        )
        if not request.user.is_authenticated and article.pk in request.session.get("recommended_article_ids", []):
            response["HX-Trigger"] = json.dumps(
                {
                    "showLoginPrompt": {
                        "text": "Thanks, your recommendation has been counted.",
                        "link": "Sign in to keep it with your account",
                    }
                }
            )
        return response
    return redirect(request.POST.get("next") or reverse("submissions:journal_list"))


def _toggle_visitor_recommendation(request, article):
    """Record or withdraw a signed-out recommendation for this session."""
    from spanza_journal_watch.analytics.middleware import VISITOR_COOKIE_NAME
    from spanza_journal_watch.analytics.utils import is_probable_automated_event

    if is_probable_automated_event(request, event_type="recommend"):
        return
    if not request.session.session_key:
        request.session.create()
    session_key = request.session.session_key
    recommended = request.session.get("recommended_article_ids", [])
    if article.pk in recommended:
        recommended.remove(article.pk)
        PubmedArticleVisitorRecommendation.objects.filter(article=article, session_key=session_key).delete()
    else:
        recommended.append(article.pk)
        PubmedArticleVisitorRecommendation.objects.get_or_create(
            article=article,
            session_key=session_key,
            defaults={"visitor_id": (request.COOKIES.get(VISITOR_COOKIE_NAME) or "")[:64]},
        )
    request.session["recommended_article_ids"] = recommended


@noindex_response
def journal_search(request):
    """Live search across all journals, rendered in the search drawer."""
    query = (request.GET.get("q") or "").strip()
    journal_filter = request.GET.get("journal") or ""
    active_journals = list(WatchedJournal.objects.filter(active=True).order_by("name"))

    results = []
    if len(query) >= 2:
        qs = (
            WatchedJournalArticle.objects.select_related("article", "watched_journal")
            .annotate(
                recommendation_count=Count(
                    "article__user_states",
                    filter=Q(article__user_states__recommended_at__isnull=False),
                    distinct=True,
                ),
                visitor_recommendation_count=Count("article__visitor_recommendations", distinct=True),
            )
            .filter(
                Q(article__title__icontains=query)
                | Q(article__abstract__icontains=query)
                | Q(article__doi__icontains=query)
                | Q(article__pmid__icontains=query)
            )
            .order_by("-article__publication_date", "-article__publication_month", "article__title")
        )
        if journal_filter and str(journal_filter).isdigit():
            qs = qs.filter(watched_journal_id=int(journal_filter))

        seen = set()
        for link in qs[:80]:
            if link.article_id in seen:
                continue
            seen.add(link.article_id)
            results.append(link)
            if len(results) >= 50:
                break

    context = {
        "query": query,
        "results": results,
        "active_journals": active_journals,
        "journal_filter": journal_filter,
    }
    return render(request, "submissions/fragments/journal_search_results.html", context)


@noindex_response
def journal_reading_list(request):
    """Full-width reading list with active/archived tabs, search, and journal filter."""
    from itertools import groupby

    tab = request.GET.get("tab", "active")
    query = (request.GET.get("q") or "").strip()
    journal_filter = (request.GET.get("journal") or "").strip()

    active_count = 0
    archived_count = 0
    items = []
    journal_names = set()

    if request.user.is_authenticated:
        base_qs = PubmedArticleUserState.objects.filter(user=request.user, starred_at__isnull=False).select_related(
            "article"
        )

        active_count = base_qs.filter(read_at__isnull=True).count()
        archived_count = base_qs.filter(read_at__isnull=False).count()

        if tab == "archived":
            qs = base_qs.filter(read_at__isnull=False).order_by("-read_at")
        else:
            qs = base_qs.filter(read_at__isnull=True).order_by("-starred_at")

        if query:
            qs = qs.filter(Q(article__title__icontains=query) | Q(article__abstract__icontains=query))
        if journal_filter:
            qs = qs.filter(article__source_journal_name=journal_filter)

        for state in qs:
            date_key = state.read_at if tab == "archived" else state.starred_at
            items.append(
                {
                    "article": state.article,
                    "state": state,
                    "group_key": date_key.strftime("%B %Y") if date_key else "Unknown",
                }
            )
            if state.article.source_journal_name:
                journal_names.add(state.article.source_journal_name)

        # Also get journal names from the full unfiltered set for the dropdown
        all_names = base_qs.values_list("article__source_journal_name", flat=True).distinct().order_by()
        journal_names = sorted(n for n in all_names if n)
    else:
        starred_ids = request.session.get("starred_article_ids", [])
        if starred_ids and tab != "archived":
            articles_qs = PubmedArticle.objects.filter(pk__in=starred_ids)
            if query:
                articles_qs = articles_qs.filter(Q(title__icontains=query) | Q(abstract__icontains=query))
            if journal_filter:
                articles_qs = articles_qs.filter(source_journal_name=journal_filter)

            articles_qs = articles_qs.order_by("-publication_date", "-publication_month")
            active_count = len(starred_ids)

            for article in articles_qs:
                month = article.publication_month or article.publication_date
                items.append(
                    {
                        "article": article,
                        "state": None,
                        "group_key": month.strftime("%B %Y") if month else "Unknown date",
                    }
                )
                if article.source_journal_name:
                    journal_names.add(article.source_journal_name)
            journal_names = sorted(journal_names)
        elif starred_ids:
            active_count = len(starred_ids)

    # Build star count + review lookup for reading list items
    reading_list_pubmed_ids = [item["article"].pk for item in items]
    star_count_map = {}
    if reading_list_pubmed_ids:
        star_counts = (
            PubmedArticleUserState.objects.filter(
                article_id__in=reading_list_pubmed_ids,
                starred_at__isnull=False,
            )
            .values("article_id")
            .annotate(count=Count("id"))
        )
        star_count_map = {row["article_id"]: row["count"] for row in star_counts}
    for item in items:
        item["star_count"] = star_count_map.get(item["article"].pk, 0)

    review_map = {}
    if reading_list_pubmed_ids:
        reviewed = Review.objects.filter(active=True, article_id__in=reading_list_pubmed_ids).select_related("author")
        for rev in reviewed:
            review_map.setdefault(rev.article_id, rev)
    for item in items:
        item["review"] = review_map.get(item["article"].pk)

    grouped = []
    for key, group in groupby(items, key=lambda x: x["group_key"]):
        grouped.append((key, list(group)))

    context = {
        "grouped_items": grouped,
        "total_count": len(items),
        "active_count": active_count,
        "archived_count": archived_count,
        "tab": tab,
        "query": query,
        "journal_filter": journal_filter,
        "journal_names": journal_names,
        "is_reading_list": True,
        "can_recommend": can_recommend_pubmed_articles(request.user),
        "current_view": "archive" if tab == "archived" else "reading_list",
    }

    if request.headers.get("HX-Request") == "true":
        return render(request, "submissions/fragments/journal_reading_list.html", context)

    # Full-page request (direct navigation / refresh) — render inside the journal shell
    context.update(_journal_browser_context(request))
    context["reading_list_fragment"] = True
    return render(request, "submissions/journal_list.html", context)


@login_required
@require_POST
def journal_article_toggle_archive(request, article_id):
    """Toggle read_at (archive/unarchive) on a starred article."""
    state = get_object_or_404(
        PubmedArticleUserState, user=request.user, article_id=article_id, starred_at__isnull=False
    )
    state.read_at = None if state.read_at else timezone.now()
    state.save(update_fields=["read_at", "modified"])

    if request.headers.get("HX-Request") == "true":
        # After toggle the card no longer belongs on the current tab — remove it.
        # Also update the tab counts via OOB swap.
        base_qs = PubmedArticleUserState.objects.filter(user=request.user, starred_at__isnull=False)
        active_count = base_qs.filter(read_at__isnull=True).count()
        archived_count = base_qs.filter(read_at__isnull=False).count()
        oob_html = (
            f'<span class="journal-reading-list__tab-count" '
            f'id="reading-list-active-count" hx-swap-oob="innerHTML:#reading-list-active-count">'
            f"{active_count}</span>"
            f'<span class="journal-reading-list__tab-count" '
            f'id="reading-list-archived-count" hx-swap-oob="innerHTML:#reading-list-archived-count">'
            f"{archived_count}</span>"
        )
        return HttpResponse(oob_html)
    return redirect(reverse("submissions:journal_list"))
