"""The public review page."""

import json

from django.templatetags.static import static
from django.urls import reverse
from django.utils.functional import cached_property
from django.views.generic import DetailView
from view_breadcrumbs import BaseBreadcrumbMixin

from spanza_journal_watch.backend.models import (
    PubmedArticleUserState,
)
from spanza_journal_watch.layout.models import PageHeader
from spanza_journal_watch.utils.functions import shorten_text
from spanza_journal_watch.utils.mixins import AnonymousCacheMixin, HitMixin, HtmxMixin, SidebarMixin

from ..models import Review
from .shared import SEO_TITLE_MAX_LENGTH, build_request_absolute_url, build_review_meta_description, build_share_urls


class ReviewDetailView(AnonymousCacheMixin, HitMixin, SidebarMixin, HtmxMixin, BaseBreadcrumbMixin, DetailView):
    model = Review
    context_object_name = "review"
    template_name = "submissions/review_detail.html"

    def get_queryset(self):
        return super().get_queryset().select_related("article__journal", "author")

    # Breadcrumb
    @cached_property
    def crumbs(self):
        issue = self.object.issues.only("id", "name", "date", "slug").order_by("-created").first()
        if issue is None:
            return [("Issues", reverse("submissions:issue_list")), (self.object, "")]
        return [("Issues", reverse("submissions:issue_list")), (issue, issue.get_absolute_url()), (self.object, "")]

    # HTMX
    htmx_templates = ["layout/fragments/card_modal.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        canonical_url = build_request_absolute_url(self.request, self.object.get_absolute_url())

        article_title = self.object.article.get_title().strip()
        headline = self.object.get_display_headline()
        share_title = f"SPANZA Journal Watch - {headline}"
        seo_title = shorten_text(headline.rstrip(".").strip(), SEO_TITLE_MAX_LENGTH)
        context["page_title"] = f"{seo_title} | SPANZA Journal Watch"
        context["display_headline"] = headline
        context["has_editorial_headline"] = bool((self.object.editorial_headline or "").strip())
        bottom_line = (self.object.bottom_line or "").strip()
        share_description = bottom_line or self.object.get_truncated_body().strip()
        context["page_meta_description"] = build_review_meta_description(
            shorten_text(share_description, 300), self.object.article
        )
        share_email_summary = self.object.get_plain_body().strip()
        self.object.display_review_date = self.object.get_review_date()
        review_date = self.object.display_review_date
        share_context = build_share_urls(
            share_title,
            canonical_url,
            share_description,
            journal_name=str(self.object.article.journal),
            email_summary=share_email_summary,
        )

        context["canonical_url"] = canonical_url
        og_image = (
            self.request.build_absolute_uri(self.object.feature_image.url)
            if self.object.feature_image
            else self.request.build_absolute_uri(static("images/logo/spanza-logo-blue.png"))
        )
        article = self.object.article
        item_reviewed = {
            "@type": "ScholarlyArticle",
            "name": article.title or article_title,
        }
        if article.doi:
            item_reviewed["identifier"] = {
                "@type": "PropertyValue",
                "propertyID": "DOI",
                "value": article.doi,
            }
        if article.pmid:
            item_reviewed.setdefault("identifier", [])
            if isinstance(item_reviewed["identifier"], dict):
                item_reviewed["identifier"] = [item_reviewed["identifier"]]
            item_reviewed["identifier"].append({"@type": "PropertyValue", "propertyID": "PMID", "value": article.pmid})
        journal_name = article.source_journal_name or (str(article.journal) if article.journal else "")
        if journal_name:
            item_reviewed["isPartOf"] = {"@type": "Periodical", "name": journal_name}
        if article.publication_date:
            item_reviewed["datePublished"] = article.publication_date.isoformat()
        source_url = article.pubmed_url or article.article_url
        if source_url:
            item_reviewed["url"] = source_url
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": shorten_text(headline, 110),
                "alternativeHeadline": shorten_text(article_title, 150),
                "description": share_description,
                "url": canonical_url,
                "image": og_image,
                "datePublished": review_date.isoformat() if review_date else "",
                "author": {
                    "@type": "Person",
                    "name": str(self.object.author),
                },
                "publisher": {
                    "@type": "Organization",
                    "name": "Journal Watch",
                    "logo": {
                        "@type": "ImageObject",
                        "url": self.request.build_absolute_uri(static("images/logo/spanza-logo-blue.png")),
                    },
                },
                "about": item_reviewed,
            }
        )
        context["share_title"] = share_title
        context["share_description"] = share_description
        context["share_email_summary"] = share_email_summary
        context["share_text"] = share_context["share_text"]
        context["share_image_url"] = (
            self.request.build_absolute_uri(self.object.feature_image.url) if self.object.feature_image else ""
        )
        context["bluesky_share_url"] = share_context["bluesky_share_url"]
        context["x_share_url"] = share_context["x_share_url"]
        context["facebook_share_url"] = share_context["facebook_share_url"]
        context["email_share_url"] = share_context["email_share_url"]

        # Override header
        override = {"title": self.object.get_full_name()}
        header = PageHeader.get_active_for(PageHeader.PageType.REVIEW_DETAIL)
        context["page_header"] = header.collate_fields(**override) if header else override

        # Related reviews
        pubmed_article = self.object.article
        context["related_reviews"] = pubmed_article.get_related_reviews(limit=4)

        # Star button context — review.article IS the PubmedArticle after merge
        context["pubmed_article"] = pubmed_article
        context["star_count"] = PubmedArticleUserState.objects.filter(
            article=pubmed_article, starred_at__isnull=False
        ).count()
        if self.request.user.is_authenticated:
            context["pubmed_user_state"] = PubmedArticleUserState.objects.filter(
                user=self.request.user, article=pubmed_article
            ).first()
        else:
            context["pubmed_session_starred"] = pubmed_article.pk in self.request.session.get(
                "starred_article_ids", []
            )

        return context
