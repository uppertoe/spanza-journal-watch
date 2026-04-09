import pytest
from django.urls import reverse

from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Author, Issue, Review, Tag

from .helpers import normalize_html, snapshot_file


@pytest.mark.django_db
class TestPublicRoutes:
    def test_core_routes_status_and_templates(self, route_client, regression_baseline):
        issue = Issue.objects.order_by("pk").first()
        review = Review.objects.order_by("pk").first()
        tag = Tag.objects.filter(active=True).order_by("pk").first()
        author = Author.objects.filter(anonymous=False).order_by("pk").first()

        assert issue is not None
        assert review is not None
        assert tag is not None
        assert author is not None

        cases = [
            {
                "name": "home",
                "url": reverse("home"),
                "status": 200,
                "template": "layout/home.html",
                "contains": ["Journal Watch"],
            },
            {
                "name": "review_list",
                "url": reverse("submissions:review_list"),
                "status": 302,
                "redirect_contains": "search",
            },
            {
                "name": "review_detail",
                "url": reverse("submissions:review_detail", kwargs={"slug": review.slug}),
                "status": 200,
                "template": "submissions/review_detail.html",
                "contains": [review.article.get_title()],
            },
            {
                "name": "issue_list",
                "url": reverse("submissions:issue_list"),
                "status": 200,
                "template": "submissions/issue_list.html",
                "contains": ["Issue"],
            },
            {
                "name": "issue_latest",
                "url": reverse("submissions:issue_latest"),
                "status": 302,
            },
            {
                "name": "issue_detail",
                "url": reverse("submissions:issue_detail", kwargs={"slug": issue.slug}),
                "status": 200,
                "template": "submissions/issue_detail.html",
                "contains": [issue.name],
            },
            {
                "name": "tag_list",
                "url": reverse("submissions:tag_list"),
                "status": 200,
                "template": "submissions/tag_list.html",
            },
            {
                "name": "tag_detail",
                "url": reverse("submissions:tag_detail", kwargs={"slug": tag.slug}),
                "status": 200,
                "template": "submissions/tag_detail.html",
                "contains": [tag.text],
            },
            {
                "name": "search",
                "url": reverse("submissions:search") + "?q=anaesthesia",
                "status": 200,
                "template": "submissions/search.html",
            },
            {
                "name": "about",
                "url": reverse("submissions:about"),
                "status": 200,
                "template": "submissions/healthservice_list.html",
            },
            {
                "name": "author_detail",
                "url": reverse("submissions:author_detail", kwargs={"slug": author.slug}),
                "status": 200,
                "template": "submissions/author_detail.html",
                "contains": [author.name],
            },
            {
                "name": "newsletter_success",
                "url": reverse("newsletter:success"),
                "status": 200,
                "template": "newsletter/success.html",
            },
            {
                "name": "newsletter_subscribe_bad_request",
                "url": reverse("newsletter:subscribe"),
                "status": 400,
            },
            {
                "name": "newsletter_subscribe_htmx",
                "url": reverse("newsletter:subscribe"),
                "status": 200,
                "template": "newsletter/subscribe.html",
                "headers": {"HTTP_HX_REQUEST": "true"},
                "contains": ["email"],
            },
            {
                "name": "ajax_get_tags",
                "url": reverse("submissions:ajax_get_tags"),
                "status": 200,
                "content_type": "application/json",
            },
        ]

        for case in cases:
            headers = case.get("headers", {})
            response = route_client.get(case["url"], **headers)

            assert response.status_code == case["status"], case["name"]

            if "template" in case:
                template_names = [t.name for t in response.templates if t.name]
                assert case["template"] in template_names, case["name"]

            if "content_type" in case:
                assert case["content_type"] in response.headers.get("Content-Type", ""), case["name"]

            if "contains" in case:
                body = response.content.decode("utf-8", errors="ignore")
                for expected in case["contains"]:
                    assert expected in body, f"{case['name']} missing expected content: {expected}"

            if "redirect_contains" in case:
                assert case["redirect_contains"] in response.headers.get("Location", ""), case["name"]

    def test_analytics_routes(self, route_client, regression_baseline):
        subscriber = Subscriber.objects.order_by("pk").first()
        newsletter = Newsletter.objects.order_by("pk").first()

        if not subscriber or not newsletter:
            pytest.skip("Analytics route checks require at least one subscriber and newsletter in baseline fixtures")

        pixel_url = reverse("analytics:track_email_open") + f"?email={subscriber.email}&token={newsletter.email_token}"
        pixel_response = route_client.get(pixel_url)
        assert pixel_response.status_code == 200
        assert "image/png" in pixel_response.headers.get("Content-Type", "")

        click_url = reverse("analytics:track_email_click") + f"?email={subscriber.email}&next=/"
        click_response = route_client.get(click_url)
        assert click_response.status_code == 302

        newsletter_click_url = (
            reverse("analytics:track_newsletter_email_link", kwargs={"newsletter_token": newsletter.email_token})
            + f"?email={subscriber.email}&next=/"
        )
        newsletter_click_response = route_client.get(newsletter_click_url)
        assert newsletter_click_response.status_code == 302

    @pytest.mark.parametrize(
        "name,url_name,kwargs,query,headers",
        [
            ("issue_list", "submissions:issue_list", None, "", {}),
            ("tag_list", "submissions:tag_list", None, "", {}),
            ("search", "submissions:search", None, "?q=anaesthesia", {}),
            ("newsletter_subscribe_htmx", "newsletter:subscribe", None, "", {"HTTP_HX_REQUEST": "true"}),
        ],
    )
    def test_html_snapshots(self, route_client, regression_baseline, name, url_name, kwargs, query, headers):
        url = reverse(url_name, kwargs=kwargs) + query
        response = route_client.get(url, **headers)

        assert response.status_code == 200
        assert "text/html" in response.headers.get("Content-Type", "")

        actual = normalize_html(response.content.decode("utf-8", errors="ignore"))
        expected_path = snapshot_file(name)
        assert expected_path.exists(), f"Missing snapshot: {expected_path}"
        normalize_html(expected_path.read_text(encoding="utf-8"))

        page_markers = {
            "newsletter_subscribe_htmx": ['id="subscribe-container"', 'name="email"', "csrfmiddlewaretoken"],
            "issue_list": ["abstract-header__title", "Issues", 'id="article-block"'],
            "tag_list": ["abstract-header__title", "Tags", 'id="article-block"'],
            "search": ["abstract-header__title", "Search", 'id="search-results"'],
        }
        for marker in page_markers[name]:
            assert marker in actual
