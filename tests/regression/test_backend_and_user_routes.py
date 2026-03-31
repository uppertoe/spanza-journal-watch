import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen
from spanza_journal_watch.backend.models import (
    BackendPreference,
    PlankaCardImport,
    PlankaIssueBinding,
    PubmedBatchArticle,
    PubmedImportBatch,
    PubmedIntegrationCredential,
    SubscriberCSV,
    WatchedJournal,
)
from spanza_journal_watch.backend.planka import PlankaAPIError
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Article, Author, Issue, Journal, Review

User = get_user_model()


@pytest.mark.django_db
class TestUserRoutes:
    def test_users_redirect_requires_login(self, route_client, regression_baseline):
        response = route_client.get(reverse("users:redirect"))
        assert response.status_code == 302
        assert reverse("account_login") in response.headers.get("Location", "")

    def test_users_redirect_for_authenticated_user(self, route_client, regression_baseline):
        user = User.objects.order_by("pk").first()
        assert user is not None

        route_client.force_login(user)
        response = route_client.get(reverse("users:redirect"))

        assert response.status_code == 302
        assert reverse("users:detail", kwargs={"pk": user.pk}) in response.headers.get("Location", "")

    def test_user_detail_only_for_self(self, route_client, regression_baseline):
        users = list(User.objects.order_by("pk")[:2])
        assert len(users) == 2
        user, other = users[0], users[1]

        route_client.force_login(user)

        own_response = route_client.get(reverse("users:detail", kwargs={"pk": user.pk}))
        assert own_response.status_code == 200

        other_response = route_client.get(reverse("users:detail", kwargs={"pk": other.pk}))
        assert other_response.status_code == 403

    def test_user_update_page_for_authenticated_user(self, route_client, regression_baseline):
        user = User.objects.order_by("pk").first()
        assert user is not None

        route_client.force_login(user)
        response = route_client.get(reverse("users:update"))

        assert response.status_code == 200


@pytest.mark.django_db
class TestBackendRoutes:
    def test_backend_routes_require_login(self, route_client, regression_baseline):
        newsletter = Newsletter.objects.order_by("pk").first()
        assert newsletter is not None

        urls = [
            reverse("backend:dashboard"),
            reverse("backend:upload_subscribers"),
            reverse("backend:final_newsletter", kwargs={"send_token": newsletter.send_token}),
            reverse("backend:newsletter_stats_detail", kwargs={"pk": newsletter.pk}),
        ]

        for url in urls:
            response = route_client.get(url)
            assert response.status_code == 302
            assert reverse("account_login") in response.headers.get("Location", "")
            assert "next=" in response.headers.get("Location", "")

    def test_backend_manage_csv_permission_guard(self, route_client, regression_baseline):
        regular_user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert regular_user is not None

        route_client.force_login(regular_user)
        response = route_client.get(reverse("backend:dashboard"))

        assert response.status_code == 403

    def test_backend_superuser_access(self, route_client, regression_baseline):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        newsletter = Newsletter.objects.order_by("pk").first()

        assert admin_user is not None
        assert newsletter is not None

        route_client.force_login(admin_user)

        dashboard_response = route_client.get(reverse("backend:dashboard"))
        assert dashboard_response.status_code == 200

        upload_response = route_client.get(reverse("backend:upload_subscribers"))
        assert upload_response.status_code == 200
        assert "Subscriber list" in upload_response.content.decode("utf-8", errors="ignore")

        final_response = route_client.get(
            reverse("backend:final_newsletter", kwargs={"send_token": newsletter.send_token})
        )
        assert final_response.status_code == 200
        final_body = final_response.content.decode("utf-8", errors="ignore")
        assert "Newsletter release check" in final_body

        stats_detail_response = route_client.get(
            reverse("backend:newsletter_stats_detail", kwargs={"pk": newsletter.pk})
        )
        assert stats_detail_response.status_code == 200
        assert "Newsletter stats" in stats_detail_response.content.decode("utf-8", errors="ignore")

    def test_htmx_only_backend_endpoints(self, route_client, regression_baseline):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        newsletter = Newsletter.objects.order_by("pk").first()
        assert admin_user is not None
        assert newsletter is not None

        route_client.force_login(admin_user)

        fake_token = "token-does-not-matter-without-htmx"
        edit_response = route_client.get(reverse("backend:edit_csv_header", kwargs={"save_token": fake_token}))
        process_response = route_client.get(reverse("backend:process_csv", kwargs={"save_token": fake_token}))

        assert edit_response.status_code == 400
        assert process_response.status_code == 400

        send_response = route_client.get(
            reverse("backend:send_final_newsletter", kwargs={"send_token": newsletter.send_token})
        )
        assert send_response.status_code == 200


@pytest.mark.django_db
class TestBackendWorkflows:
    def test_create_newsletter_defaults_to_latest_issue(self, route_client, regression_baseline):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        assert admin_user is not None
        route_client.force_login(admin_user)

        latest_issue = Issue.objects.exclude(active=False).order_by("-date", "-pk").first()
        assert latest_issue is not None

        response = route_client.post(
            reverse("backend:create_newsletter"),
            data={
                "subject": "Regression created newsletter",
                "content_heading": "Heading",
                "content": "Body content",
                "non_featured_review_count": 5,
                "ready_to_send": False,
            },
        )

        assert response.status_code == 302
        newsletter = Newsletter.objects.get(subject="Regression created newsletter")
        assert newsletter.issue_id == latest_issue.pk
        location = response.headers.get("Location", "")
        assert reverse("backend:newsletter_release_list") in location
        assert f"issue={newsletter.issue_id}" in location

    def test_upload_subscriber_csv_preview(self, route_client, regression_baseline):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        assert admin_user is not None
        route_client.force_login(admin_user)

        csv_bytes = b"email\nworkflow-user@example.test\n"
        upload = SimpleUploadedFile("subscribers.csv", csv_bytes, content_type="text/csv")

        response = route_client.post(
            reverse("backend:upload_subscribers"),
            data={"name": "Workflow CSV", "file": upload},
        )

        assert response.status_code == 200
        template_names = [t.name for t in response.templates if t.name]
        assert "backend/preview_csv.html" in template_names
        body = response.content.decode("utf-8", errors="ignore")
        assert "Submit CSV for processing" in body
        assert "workflow-user@example.test" in body
        assert SubscriberCSV.objects.filter(name="Workflow CSV").exists()

    def test_edit_csv_header_with_htmx(self, route_client, regression_baseline):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        assert admin_user is not None
        route_client.force_login(admin_user)

        csv_upload = SimpleUploadedFile(
            "header-test.csv",
            b"email\nheader-check@example.test\n",
            content_type="text/csv",
        )
        csv_obj = SubscriberCSV.objects.create(name="Header test", file=csv_upload, header=True)

        response = route_client.post(
            reverse("backend:edit_csv_header", kwargs={"save_token": csv_obj.save_token}),
            data={"header": False},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        template_names = [t.name for t in response.templates if t.name]
        assert "backend/preview_csv_htmx.html" in template_names
        body = response.content.decode("utf-8", errors="ignore")
        assert "Submit CSV for processing" in body

    def test_process_csv_sets_confirmed_and_returns_summary(self, route_client, regression_baseline, monkeypatch):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        assert admin_user is not None
        route_client.force_login(admin_user)

        csv_upload = SimpleUploadedFile(
            "process-test.csv",
            b"email\nprocess-check@example.test\n",
            content_type="text/csv",
        )
        csv_obj = SubscriberCSV.objects.create(name="Process test", file=csv_upload, header=True)

        def _fake_process(_pk):
            return {
                "email_column": "email",
                "rows_parsed": 1,
                "records_added": 1,
                "records_skipped": 0,
                "invalid_email_count": 0,
                "duplicate_in_file_count": 0,
                "already_subscribed_count": 0,
            }

        monkeypatch.setattr("spanza_journal_watch.backend.views.process_subscriber_csv", _fake_process)

        response = route_client.get(
            reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token}),
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Rows parsed:" in body
        assert "Added:" in body
        csv_obj.refresh_from_db()
        assert csv_obj.confirmed is True
        assert csv_obj.processed is False

    def test_subscriber_list_page_filters(self, route_client, regression_baseline):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        assert admin_user is not None
        route_client.force_login(admin_user)

        Subscriber.objects.create(email="active@example.test", subscribed=True, bounced=False, complained=False)
        Subscriber.objects.create(email="bounced@example.test", subscribed=True, bounced=True, complained=False)

        response = route_client.get(reverse("backend:subscriber_list"), data={"bounced": "true"})

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Mailing List" in body
        assert "bounced@example.test" in body

    def test_send_final_newsletter_queues_task_when_ready(self, route_client, regression_baseline, monkeypatch):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        newsletter = Newsletter.objects.order_by("pk").first()
        assert admin_user is not None
        assert newsletter is not None

        newsletter.ready_to_send = True
        newsletter.is_test_sent = True
        newsletter.is_sent = False
        newsletter.save()

        route_client.force_login(admin_user)

        called = {"value": False}

        def _fake_apply_async(*args, **kwargs):
            called["value"] = True

        monkeypatch.setattr("spanza_journal_watch.backend.views.send_newsletter.apply_async", _fake_apply_async)

        response = route_client.get(
            reverse("backend:send_final_newsletter", kwargs={"send_token": newsletter.send_token})
        )

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Newsletter send" in body
        assert "currently selected newsletter" in body
        assert called["value"] is True

    def test_newsletter_resend_requires_explicit_enable(self, route_client, regression_baseline, monkeypatch):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        newsletter = Newsletter.objects.order_by("pk").first()
        assert admin_user is not None
        assert newsletter is not None

        newsletter.ready_to_send = True
        newsletter.is_test_sent = True
        newsletter.is_sent = True
        newsletter.resend_enabled = False
        newsletter.save()

        route_client.force_login(admin_user)

        called = {"value": False}

        def _fake_apply_async(*args, **kwargs):
            called["value"] = True

        monkeypatch.setattr("spanza_journal_watch.backend.views.send_newsletter.apply_async", _fake_apply_async)

        blocked_response = route_client.get(
            reverse("backend:send_final_newsletter", kwargs={"send_token": newsletter.send_token})
        )
        assert blocked_response.status_code == 200
        assert called["value"] is False

        enable_response = route_client.post(
            reverse("backend:enable_newsletter_resend", kwargs={"send_token": newsletter.send_token})
        )
        assert enable_response.status_code == 302

        newsletter.refresh_from_db()
        assert newsletter.resend_enabled is True

        resend_response = route_client.get(
            reverse("backend:send_final_newsletter", kwargs={"send_token": newsletter.send_token})
        )
        assert resend_response.status_code == 200
        assert called["value"] is True

    def test_newsletter_stats_detail_math(self, route_client, regression_baseline):
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
        newsletter = Newsletter.objects.order_by("pk").first()
        subscribers = list(Subscriber.objects.order_by("pk")[:2])

        assert admin_user is not None
        assert newsletter is not None
        assert len(subscribers) == 2

        newsletter.emails_sent = 10
        newsletter.is_sent = True
        newsletter.save(update_fields=["emails_sent", "is_sent"])

        NewsletterOpen.objects.filter(newsletter=newsletter).delete()
        NewsletterClick.objects.filter(newsletter=newsletter).delete()

        NewsletterOpen.objects.create(newsletter=newsletter, subscriber=subscribers[0])
        NewsletterOpen.objects.create(newsletter=newsletter, subscriber=subscribers[0])
        NewsletterOpen.objects.create(newsletter=newsletter, subscriber=subscribers[1])

        NewsletterClick.objects.create(newsletter=newsletter, subscriber=subscribers[0])
        NewsletterClick.objects.create(newsletter=newsletter, subscriber=subscribers[0])

        route_client.force_login(admin_user)
        response = route_client.get(reverse("backend:newsletter_stats_detail", kwargs={"pk": newsletter.pk}))

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Distinct (total) emails opened:" in body
        assert "Distinct (total) links clicked:" in body
        assert "20%" in body
        assert "50%" in body
        assert response.context["total_opens"] == 3
        assert response.context["opens"] == 2
        assert response.context["total_clicks"] == 2
        assert response.context["clicks"] == 1
        assert response.context["open_rate"] == "20%"
        assert response.context["click_through_rate"] == "50%"


@pytest.mark.django_db
class TestIssueBuilderWorkflow:
    def test_issue_builder_permission_guard(self, route_client, regression_baseline):
        regular_user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert regular_user is not None

        route_client.force_login(regular_user)
        response = route_client.get(reverse("backend:issue_builder"))
        assert response.status_code == 403

    def test_issue_builder_create_add_publish(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(
            Permission.objects.get(codename="manage_issue_builder"),
            Permission.objects.get(codename="chief_editor"),
        )
        route_client.force_login(user)

        issue_response = route_client.post(
            reverse("backend:save_issue_draft"),
            data={
                "name": "Regression Draft Issue",
                "date": "2026-03-15",
                "body": "Issue summary body",
            },
        )
        assert issue_response.status_code == 302

        issue = Issue.objects.get(name="Regression Draft Issue")
        assert issue.active is False

        author = Author.objects.order_by("pk").first()
        if not author:
            author = Author.objects.create(name="Regression Author", title="Dr")

        journal = Journal.objects.order_by("pk").first()
        if not journal:
            journal = Journal.objects.create(name="Regression Journal")

        add_review_response = route_client.post(
            reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk}),
            data={
                "article_mode": "new",
                "article_name": "Regression Article",
                "article_journal": journal.pk,
                "article_year": 2026,
                "article_citation": "A citation",
                "article_url": "https://example.test/article",
                "article_tags_string": "#regression",
                "author": author.pk,
                "body": "Review body",
                "is_featured": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        assert add_review_response.status_code == 200

        issue.refresh_from_db()
        review = issue.reviews.first()
        assert review is not None
        assert review.author_id == author.pk
        assert review.article.name == "Regression Article"
        assert review.active is False
        assert review.article.active is False

        publish_response = route_client.post(
            reverse("backend:publish_issue_bundle", kwargs={"issue_id": issue.pk}),
            HTTP_HX_REQUEST="true",
        )
        assert publish_response.status_code in (200, 302)

        issue.refresh_from_db()
        review.refresh_from_db()
        review.article.refresh_from_db()
        assert issue.active is True
        assert review.active is True
        assert review.article.active is True

    def test_issue_builder_featured_limit_enforced(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Featured limit issue", body="Body")

        author = Author.objects.order_by("pk").first()
        if not author:
            author = Author.objects.create(name="Regression Author", title="Dr")

        article_one = Article.objects.create(name="Featured One")
        article_two = Article.objects.create(name="Featured Two")
        article_three = Article.objects.create(name="Featured Three")

        review_one = Review.objects.create(article=article_one, author=author, body="One", is_featured=True)
        review_two = Review.objects.create(article=article_two, author=author, body="Two", is_featured=True)
        issue.reviews.add(review_one, review_two)

        response = route_client.post(
            reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk}),
            data={
                "article_mode": "existing",
                "existing_article": article_three.pk,
                "author": author.pk,
                "body": "Three",
                "is_featured": "on",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        # The panel re-renders without adding the review (form errors are
        # returned in context but the panel template doesn't display the form)
        assert issue.reviews.count() == 2


@pytest.mark.django_db
class TestIssueBuilderPlankaIntegration:
    def test_planka_import_page_renders_for_issue_builder_user(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Planka Page Issue", body="Issue body")
        response = route_client.get(reverse("backend:issue_planka_import") + f"?issue={issue.pk}")

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Planka Sync" in body
        assert "Select an issue to use Planka sync." not in body

    def test_planka_import_page_preloads_publish_cards(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Planka Preload Issue", body="Issue body")
        PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="project-1",
            project_name="Issue 2026-03",
            board_id="board-1",
            board_name="Reviews",
            lists={"publish_ready": "list-publish-ready"},
            custom_fields={},
            custom_field_group_id="cfg-1",
        )

        monkeypatch.setattr(
            "spanza_journal_watch.backend.views._extract_board_cards",
            lambda _binding: [
                {
                    "id": "card-42",
                    "name": "Preloaded card",
                    "description": "",
                    "schema": {"article_url": "https://example.test/preloaded"},
                    "missing_required": [],
                    "is_valid": True,
                    "already_imported": False,
                    "has_associated_review": False,
                    "associated_review_id": None,
                    "sync_blocked_reason": "",
                    "list_id": "list-publish-ready",
                    "list_name": "Publish Ready",
                    "list_type": "active",
                    "in_publish_ready": True,
                }
            ],
        )

        response = route_client.get(reverse("backend:issue_planka_import") + f"?issue={issue.pk}")

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Preloaded card" in body
        assert "card-42" in body

    def test_planka_setup_issue_project_creates_binding(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Planka Setup Issue", body="Issue body")

        class FakePlankaClient:
            def create_project(self, name):
                return {"id": "project-1", "name": name}

            def upload_project_background_image(
                self, project_id, file_obj, filename="background.webp", content_type="image/webp"
            ):
                return {"id": "background-1"}

            def update_project_background(self, project_id, background_type="image", background_image_id=None):
                return {"id": project_id}

            def create_board(self, project_id, name="Reviews", position=65536):
                return {"id": "board-1" if name == "Reviews" else "board-2", "name": name}

            def create_list(self, board_id, name, position, list_type="active"):
                return {"id": f"list-{name.lower().replace(' ', '-')}", "name": name}

            def update_list(self, list_id, **kwargs):
                return {"id": list_id}

            def create_card(self, list_id, name, description="", position=65536, card_type="story"):
                return {"id": f"card-{list_id}-{position}", "name": name}

            def list_webhooks(self):
                return []

            def create_webhook(self, url, events=None, access_token=None):
                return {"id": "webhook-1"}

            def get_board(self, board_id):
                return None, {"labels": [], "lists": [], "cards": []}

        monkeypatch.setattr("spanza_journal_watch.backend.views._build_planka_client", lambda: FakePlankaClient())

        response = route_client.post(
            reverse("backend:planka_setup_issue_project", kwargs={"issue_id": issue.pk}),
            data={"project_name": "Issue 2026-03"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        binding = PlankaIssueBinding.objects.get(issue=issue)
        assert binding.project_id == "project-1"
        assert binding.board_id == "board-1"
        assert binding.instructions_board_id == "board-2"
        assert binding.get_list_id("publish_ready") == "list-publish-ready"
        assert binding.instructions_lists.get("reviewers") == "list-reviewers"

    def test_planka_update_project_name_via_htmx(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Planka Rename Issue", body="Issue body")
        binding = PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="project-rename-1",
            project_name="Old Project Name",
            board_id="board-rename-1",
            board_name="Reviews",
            lists={"publish_ready": "list-publish-ready"},
            custom_fields={},
            custom_field_group_id="cfg-1",
        )

        class FakeRenameClient:
            def update_project_name(self, project_id, name):
                assert project_id == binding.project_id
                return {"id": project_id, "name": name}

        monkeypatch.setattr("spanza_journal_watch.backend.views._build_planka_client", lambda: FakeRenameClient())

        response = route_client.post(
            reverse("backend:planka_update_project_name", kwargs={"issue_id": issue.pk}),
            data={"project_name": "New Project Name"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Project name updated." in body
        assert "New Project Name" in body

        binding.refresh_from_db()
        assert binding.project_name == "New Project Name"

    def test_planka_import_publish_card_creates_review(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Planka Import Issue", body="Issue body")
        binding = PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="project-1",
            project_name="Issue 2026-03",
            board_id="board-1",
            board_name="Reviews",
            lists={"publish_ready": "list-publish-ready"},
            custom_fields={},
            custom_field_group_id="cfg-1",
        )

        author = Author.objects.create(name="Integration Author", title="Dr", email="author@example.test")

        class FakePlankaImportClient:
            def get_card_members(self, card_id):
                return [{"userId": "user-1"}], {
                    "user-1": {"email": "author@example.test", "name": "Integration Author"}
                }

            def get_card_description_editor_ids(self, card_id):
                return []

        monkeypatch.setattr(
            "spanza_journal_watch.backend.views._build_planka_client", lambda: FakePlankaImportClient()
        )
        monkeypatch.setattr(
            "spanza_journal_watch.backend.views._extract_board_cards",
            lambda _binding: [
                {
                    "id": "card-1",
                    "name": "Review card",
                    "schema": {
                        "review_body_markdown": "Imported body",
                        "is_featured": "true",
                    },
                    "missing_required": [],
                    "is_valid": True,
                    "already_imported": False,
                    "has_associated_review": False,
                    "associated_review_id": None,
                    "in_publish_ready": True,
                    "list_id": "list-publish-ready",
                    "list_name": "Publish Ready",
                    "list_type": "active",
                    "description": "",
                    "sync_blocked_reason": "",
                }
            ],
        )

        response = route_client.post(
            reverse("backend:planka_import_publish_card", kwargs={"issue_id": issue.pk}),
            data={"card_id": "card-1"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Review created from Planka card." in body

        review = issue.reviews.select_related("article", "author").first()
        assert review is not None
        assert review.body == "Imported body"
        assert review.is_featured is True
        assert review.active is False
        assert review.author_id == author.pk
        # New code uses card name as article name when no PubmedBatchArticle is linked
        assert review.article.name == "Review card"
        assert review.article.active is False

        assert PlankaCardImport.objects.filter(binding=binding, issue=issue, card_id="card-1", review=review).exists()

    def test_planka_sync_allows_missing_required_fields(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Planka Missing Fields Issue", body="Issue body")
        binding = PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="project-1",
            project_name="Issue 2026-03",
            board_id="board-1",
            board_name="Reviews",
            lists={"publish_ready": "list-publish-ready"},
            custom_fields={},
            custom_field_group_id="cfg-1",
        )

        class FakePlankaImportClientMinimal:
            def get_card_members(self, card_id):
                return [], {}

            def get_card_description_editor_ids(self, card_id):
                return []

        monkeypatch.setattr(
            "spanza_journal_watch.backend.views._build_planka_client", lambda: FakePlankaImportClientMinimal()
        )
        monkeypatch.setattr(
            "spanza_journal_watch.backend.views._extract_board_cards",
            lambda _binding: [
                {
                    "id": "card-2",
                    "name": "Incomplete review card",
                    "schema": {
                        "review_body_markdown": "",
                    },
                    "missing_required": ["Article URL", "Author Name", "Review Body Markdown"],
                    "is_valid": False,
                    "already_imported": False,
                    "has_associated_review": False,
                    "associated_review_id": None,
                    "in_publish_ready": True,
                    "list_id": "list-publish-ready",
                    "list_name": "Publish Ready",
                    "list_type": "active",
                    "description": "",
                    "sync_blocked_reason": "",
                }
            ],
        )

        response = route_client.post(
            reverse("backend:planka_import_publish_card", kwargs={"issue_id": issue.pk}),
            data={"card_id": "card-2"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Review created from Planka card." in body
        review = issue.reviews.select_related("article").first()
        assert review is not None
        # New code uses card name as article name when no PubmedBatchArticle is linked
        assert review.article.name == "Incomplete review card"
        assert review.article.url in (None, "")
        assert PlankaCardImport.objects.filter(binding=binding, issue=issue, card_id="card-2", review=review).exists()

    def test_planka_sync_post_failure_is_rendered_in_ui(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Planka Sync Failure Message Issue", body="Issue body")
        PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="project-1",
            project_name="Issue 2026-03",
            board_id="board-1",
            board_name="Reviews",
            lists={"publish_ready": "list-publish-ready"},
            custom_fields={},
            custom_field_group_id="cfg-1",
        )

        def _raise_fetch_error(_binding):
            raise PlankaAPIError("Planka API 500: Internal Server Error")

        monkeypatch.setattr("spanza_journal_watch.backend.views._extract_board_cards", _raise_fetch_error)

        response = route_client.post(
            reverse("backend:planka_import_publish_card", kwargs={"issue_id": issue.pk}),
            data={"card_id": "card-9"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Could not fetch Planka cards:" in body

    def test_planka_refresh_disconnected_shows_not_connected_message(
        self, route_client, regression_baseline, monkeypatch
    ):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Planka Disconnect Issue", body="Issue body")
        PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="project-1",
            project_name="Issue 2026-03",
            board_id="board-1",
            board_name="Reviews",
            lists={"publish_ready": "list-publish-ready"},
            custom_fields={},
            custom_field_group_id="cfg-1",
        )

        def _raise_connect_error(_binding):
            raise PlankaAPIError("Could not connect to Planka at http://planka:1337: Connection refused")

        monkeypatch.setattr("spanza_journal_watch.backend.views._extract_board_cards", _raise_connect_error)

        response = route_client.get(
            reverse("backend:planka_refresh_publish_cards", kwargs={"issue_id": issue.pk}),
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Not connected to Planka. Retrying in background…" in body
        assert 'data-planka-disconnected="true"' in body

    def test_planka_resync_blocked_after_local_review_edit(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        user.user_permissions.add(Permission.objects.get(codename="chief_editor"))
        route_client.force_login(user)

        issue = Issue.objects.create(name="Planka Resync Blocked Issue", body="Issue body")
        article = Article.objects.create(name="Local Article", url="https://example.test/local")
        review = Review.objects.create(article=article, body="Local edited body", active=False)
        issue.reviews.add(review)

        binding = PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="project-1",
            project_name="Issue 2026-03",
            board_id="board-1",
            board_name="Reviews",
            lists={"publish_ready": "list-publish-ready"},
            custom_fields={},
            custom_field_group_id="cfg-1",
        )

        sync_record = PlankaCardImport.objects.create(
            issue=issue,
            binding=binding,
            card_id="card-3",
            card_name="Previously Synced",
            review=review,
            imported_by=user,
            last_review_modified_at=review.created,
            last_card_payload_hash="abc",
        )

        review.body = "Locally changed after sync"
        review.save()
        sync_record.refresh_from_db()

        monkeypatch.setattr(
            "spanza_journal_watch.backend.views._extract_board_cards",
            lambda _binding: [
                {
                    "id": "card-3",
                    "name": "Previously Synced",
                    "schema": {
                        "article_url": "https://example.test/local",
                        "article_name": "Local Article",
                        "review_body_markdown": "From planka",
                    },
                    "missing_required": [],
                    "is_valid": True,
                    "already_imported": True,
                    "has_associated_review": True,
                    "associated_review_id": review.pk,
                    "in_publish_ready": True,
                    "list_id": "list-publish-ready",
                    "list_name": "Publish Ready",
                    "list_type": "active",
                    "description": "",
                    "sync_blocked_reason": "Review already created from this card.",
                }
            ],
        )

        response = route_client.post(
            reverse("backend:planka_import_publish_card", kwargs={"issue_id": issue.pk}),
            data={"card_id": "card-3"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Review already created from this card." in body
        review.refresh_from_db()
        assert review.body == "Locally changed after sync"


@pytest.mark.django_db
class TestArticleIntakeWorkflow:
    def test_watched_journals_add_and_toggle(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        create_response = route_client.post(
            reverse("backend:watched_journals"),
            data={
                "name": "Journal of Test Anaesthesia",
                "issn_print": "1234-5678",
                "issn_electronic": "8765-4321",
                "active": True,
            },
        )

        assert create_response.status_code == 302
        watched = WatchedJournal.objects.get(name="Journal of Test Anaesthesia")
        assert watched.active is True
        assert watched.journal is not None
        assert watched.journal.name == "Journal of Test Anaesthesia"

        toggle_response = route_client.post(
            reverse("backend:watched_journal_toggle_active", kwargs={"watched_journal_id": watched.pk})
        )
        assert toggle_response.status_code == 302
        watched.refresh_from_db()
        assert watched.active is False

    def test_watched_journal_search_endpoint(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        class _FakePubmedClient:
            def search_journals(self, query, retmax=20):
                assert query == "anesthesiology"
                assert retmax == 20
                return [
                    {
                        "nlm_id": "12345",
                        "name": "Anesthesiology",
                        "medline_ta": "Anesthesiology",
                        "issn_print": "0003-3022",
                        "issn_electronic": "1528-1175",
                    }
                ]

        monkeypatch.setattr(
            "spanza_journal_watch.backend.views._build_pubmed_client",
            lambda **kwargs: _FakePubmedClient(),
        )

        short_query_response = route_client.get(reverse("backend:watched_journal_search"), data={"q": "an"})
        assert short_query_response.status_code == 200
        assert short_query_response.json() == {"results": []}

        response = route_client.get(reverse("backend:watched_journal_search"), data={"q": "anesthesiology"})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["results"]) == 1
        assert payload["results"][0]["name"] == "Anesthesiology"
        assert payload["results"][0]["issn_print"] == "0003-3022"
        assert payload["results"][0]["issn_electronic"] == "1528-1175"

    def test_pubmed_save_api_key(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        class _Validator:
            def ping(self):
                return None

        monkeypatch.setattr("spanza_journal_watch.backend.views._build_pubmed_client", lambda **kwargs: _Validator())

        response = route_client.post(
            reverse("backend:pubmed_save_api_key"),
            data={"api_key": "test-pubmed-key-1234"},
        )

        assert response.status_code == 302
        assert reverse("backend:article_intake") in response.headers.get("Location", "")

        credential = PubmedIntegrationCredential.objects.get()
        assert credential.get_api_key() == "test-pubmed-key-1234"
        assert credential.configured_by_id == user.pk
        assert credential.last_validated_at is not None

    def test_article_intake_month_inputs_render_valid_defaults(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        default_issue = Issue.objects.create(
            name="Default Intake Issue", body="Issue body", date="2030-06-01", active=True
        )

        response = route_client.get(reverse("backend:article_intake"))
        assert response.status_code == 200

        body = response.content.decode("utf-8", errors="ignore")
        assert default_issue.pk is not None

        # Defaults: issue month -4 to issue month -2 (2030-06 -> 2030-02 and 2030-04)
        assert 'name="from_month_month"' in body
        assert 'name="from_month_year"' in body
        assert 'name="to_month_month"' in body
        assert 'name="to_month_year"' in body
        assert '<option value="2" selected>February</option>' in body
        assert '<option value="4" selected>April</option>' in body
        assert '<option value="2030" selected>2030</option>' in body

    def test_article_intake_rejects_more_than_12_months(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        watched = WatchedJournal.objects.create(name="Range Watch", issn_print="1212-3434")

        response = route_client.post(
            reverse("backend:article_intake"),
            data={
                "action": "fetch",
                "watched_journals": [watched.pk],
                "from_month": "2025-01",
                "to_month": "2026-02",
            },
        )
        assert response.status_code == 200

        body = response.content.decode("utf-8", errors="ignore")
        assert "Date range cannot exceed 12 months." in body

    def test_article_intake_fetch_creates_batch_and_results(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        credential = PubmedIntegrationCredential(singleton=1)
        credential.set_api_key("pubmed-valid-key")
        credential.configured_by = user
        credential.save()

        issue = Issue.objects.create(name="Intake Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="Watched Journal", issn_print="1234-5678")

        def _fake_import(batch, watched_journals):
            assert watched in watched_journals
            batch.result_count = 1
            batch.selected_count = 0
            batch.save(update_fields=["result_count", "selected_count", "modified"])

        monkeypatch.setattr("spanza_journal_watch.backend.views._import_pubmed_batch", _fake_import)

        response = route_client.post(
            reverse("backend:article_intake"),
            data={
                "action": "fetch",
                "issue": issue.pk,
                "watched_journals": [watched.pk],
                "from_month": "2026-01",
                "to_month": "2026-02",
            },
        )

        assert response.status_code == 302
        batch = PubmedImportBatch.objects.order_by("-pk").first()
        assert batch is not None
        assert batch.issue_id == issue.pk
        assert batch.from_month.strftime("%Y-%m") == "2026-01"
        assert batch.to_month.strftime("%Y-%m") == "2026-02"
        assert batch.keyword_query == ""
        assert batch.watched_journals.filter(pk=watched.pk).exists()

    def test_article_intake_fetch_without_api_key(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        issue = Issue.objects.create(name="Intake No Key Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="No Key Watched", issn_print="9999-0000")

        called = {"value": False}

        def _fake_import(batch, watched_journals):
            called["value"] = True
            assert watched in watched_journals
            batch.result_count = 0
            batch.selected_count = 0
            batch.save(update_fields=["result_count", "selected_count", "modified"])

        monkeypatch.setattr("spanza_journal_watch.backend.views._import_pubmed_batch", _fake_import)

        response = route_client.post(
            reverse("backend:article_intake"),
            data={
                "action": "fetch",
                "issue": issue.pk,
                "watched_journals": [watched.pk],
                "from_month": "2026-01",
                "to_month": "2026-01",
            },
        )

        assert response.status_code == 302
        assert called["value"] is True
        batch = PubmedImportBatch.objects.order_by("-pk").first()
        assert batch is not None
        assert batch.watched_journals.filter(pk=watched.pk).exists()

    def test_article_intake_remembers_watched_journal_selection(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        watched_one = WatchedJournal.objects.create(name="Remember Watch A", issn_print="9090-1111")
        watched_two = WatchedJournal.objects.create(name="Remember Watch B", issn_print="9090-2222")

        monkeypatch.setattr(
            "spanza_journal_watch.backend.views._import_pubmed_batch", lambda batch, watched_journals: None
        )

        create_response = route_client.post(
            reverse("backend:article_intake"),
            data={
                "action": "fetch",
                "watched_journals": [watched_one.pk, watched_two.pk],
                "from_month": "2026-01",
                "to_month": "2026-02",
            },
        )
        assert create_response.status_code == 302

        preference = BackendPreference.objects.get()
        remembered_ids = set(preference.default_watched_journals.values_list("pk", flat=True))
        assert remembered_ids == {watched_one.pk, watched_two.pk}

        response = route_client.get(reverse("backend:article_intake"))
        body = response.content.decode("utf-8", errors="ignore")
        # Checkboxes render as <input ... value="{pk}" ...> with checked attribute
        assert re.search(rf'<input[^>]*\bchecked\b[^>]*value="{watched_one.pk}"', body) or re.search(
            rf'value="{watched_one.pk}"[^>]*\bchecked\b', body
        )
        assert re.search(rf'<input[^>]*\bchecked\b[^>]*value="{watched_two.pk}"', body) or re.search(
            rf'value="{watched_two.pk}"[^>]*\bchecked\b', body
        )

    def test_article_intake_defaults_watched_journals_to_all_active(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        BackendPreference.objects.all().delete()
        watched_one = WatchedJournal.objects.create(name="Default Select A", issn_print="7171-1111", active=True)
        watched_two = WatchedJournal.objects.create(name="Default Select B", issn_print="7171-2222", active=True)

        response = route_client.get(reverse("backend:article_intake"))
        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")

        assert re.search(rf'<input[^>]*\bchecked\b[^>]*value="{watched_one.pk}"', body) or re.search(
            rf'value="{watched_one.pk}"[^>]*\bchecked\b', body
        )
        assert re.search(rf'<input[^>]*\bchecked\b[^>]*value="{watched_two.pk}"', body) or re.search(
            rf'value="{watched_two.pk}"[^>]*\bchecked\b', body
        )

    def test_article_intake_bulk_select_all(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        issue = Issue.objects.create(name="Selection Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="Watch A", issn_print="1111-2222")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            created_by=user,
            from_month=issue.date or issue.created.date().replace(day=1),
            to_month=issue.date or issue.created.date().replace(day=1),
        )
        batch.watched_journals.add(watched)

        from spanza_journal_watch.backend.models import PubmedArticle

        pubmed_one = PubmedArticle.objects.create(pmid="1001", title="Airway study")
        pubmed_two = PubmedArticle.objects.create(pmid="1002", title="Cardiac study")

        PubmedBatchArticle.objects.create(batch=batch, article=pubmed_one, watched_journal=watched, issue=issue)
        PubmedBatchArticle.objects.create(batch=batch, article=pubmed_two, watched_journal=watched, issue=issue)

        response = route_client.post(
            reverse("backend:article_intake_bulk_selection", kwargs={"batch_id": batch.pk}),
            data={
                "bulk_action": "select_all",
                "q": "",
                "journal": "",
                "filter_selected": "",
                "paediatric_only": "0",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert batch.batch_articles.filter(is_selected=True).count() == 2

    def test_article_intake_stage_checked_includes_persisted_rows(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        issue = Issue.objects.create(name="Persisted Stage Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="Watch Persisted", issn_print="2121-3434")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            created_by=user,
            from_month=issue.created.date().replace(day=1),
            to_month=issue.created.date().replace(day=1),
        )
        batch.watched_journals.add(watched)

        from spanza_journal_watch.backend.models import PubmedArticle

        pubmed_one = PubmedArticle.objects.create(pmid="9001", title="Visible page article")
        pubmed_two = PubmedArticle.objects.create(pmid="9002", title="Persisted page article")

        row_one = PubmedBatchArticle.objects.create(
            batch=batch, article=pubmed_one, watched_journal=watched, issue=issue
        )
        row_two = PubmedBatchArticle.objects.create(
            batch=batch, article=pubmed_two, watched_journal=watched, issue=issue
        )

        response = route_client.post(
            reverse("backend:article_intake_bulk_selection", kwargs={"batch_id": batch.pk}),
            data={
                "bulk_action": "stage_checked",
                "row_ids": [str(row_one.pk)],
                "persisted_row_ids": str(row_two.pk),
                "q": "",
                "journal": "",
                "filter_selected": "",
                "page": "2",
                "paediatric_only": "0",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        row_one.refresh_from_db()
        row_two.refresh_from_db()
        assert row_one.is_selected is True
        assert row_two.is_selected is True

    def test_article_intake_toggle_selection_persists_across_pages(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        issue = Issue.objects.create(name="Toggle Persist Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="Watch Toggle Persist", issn_print="5151-6262")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            created_by=user,
            from_month=issue.created.date().replace(day=1),
            to_month=issue.created.date().replace(day=1),
        )
        batch.watched_journals.add(watched)

        from spanza_journal_watch.backend.models import PubmedArticle

        pubmed_one = PubmedArticle.objects.create(pmid="9101", title="Page one article")
        pubmed_two = PubmedArticle.objects.create(pmid="9102", title="Page two article")

        row_one = PubmedBatchArticle.objects.create(
            batch=batch, article=pubmed_one, watched_journal=watched, issue=issue
        )
        row_two = PubmedBatchArticle.objects.create(
            batch=batch, article=pubmed_two, watched_journal=watched, issue=issue
        )

        toggle_one = route_client.post(
            reverse("backend:article_intake_toggle_selection", kwargs={"batch_id": batch.pk, "item_id": row_one.pk}),
            data={"selected": "1", "page": "1", "q": "", "journal": "", "filter_selected": ""},
            HTTP_HX_REQUEST="true",
        )
        toggle_two = route_client.post(
            reverse("backend:article_intake_toggle_selection", kwargs={"batch_id": batch.pk, "item_id": row_two.pk}),
            data={"selected": "1", "page": "2", "q": "", "journal": "", "filter_selected": ""},
            HTTP_HX_REQUEST="true",
        )

        assert toggle_one.status_code == 200
        assert toggle_two.status_code == 200

        stage_response = route_client.post(
            reverse("backend:article_intake_bulk_selection", kwargs={"batch_id": batch.pk}),
            data={
                "bulk_action": "stage_checked",
                "row_ids": [str(row_two.pk)],
                "q": "",
                "journal": "",
                "filter_selected": "",
                "page": "2",
                "paediatric_only": "0",
            },
            HTTP_HX_REQUEST="true",
        )

        assert stage_response.status_code == 200
        row_one.refresh_from_db()
        row_two.refresh_from_db()
        assert row_one.is_selected is True
        assert row_two.is_selected is True

    def test_article_intake_paediatric_filter_is_opt_in(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        issue = Issue.objects.create(name="Peds Filter Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="Watch Peds", issn_print="3333-4444")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            created_by=user,
            from_month=issue.created.date().replace(day=1),
            to_month=issue.created.date().replace(day=1),
        )
        batch.watched_journals.add(watched)

        from spanza_journal_watch.backend.models import PubmedArticle

        peds = PubmedArticle.objects.create(
            pmid="2001",
            title="Paediatric airway paper",
            metadata_json={"mesh_terms": ["Pediatrics"], "keywords": [], "publication_types": ["Journal Article"]},
        )
        adult = PubmedArticle.objects.create(
            pmid="2002",
            title="Adult perioperative paper",
            metadata_json={"mesh_terms": ["Adults"], "keywords": [], "publication_types": ["Journal Article"]},
        )

        PubmedBatchArticle.objects.create(batch=batch, article=peds, watched_journal=watched, issue=issue)
        PubmedBatchArticle.objects.create(batch=batch, article=adult, watched_journal=watched, issue=issue)

        response = route_client.get(reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}))

        assert response.status_code == 200
        body = response.content.decode("utf-8", errors="ignore")
        assert "Paediatric airway paper" in body
        assert "Adult perioperative paper" in body

        filtered_response = route_client.get(
            reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}),
            data={"paediatric_only": "1"},
        )
        assert filtered_response.status_code == 200
        filtered_body = filtered_response.content.decode("utf-8", errors="ignore")
        assert "Paediatric airway paper" in filtered_body
        assert "Adult perioperative paper" not in filtered_body

    def test_article_intake_paediatric_filter_matches_text_without_mesh(self, route_client, regression_baseline):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        issue = Issue.objects.create(name="Peds Keyword Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="Watch Peds Keyword", issn_print="3333-4455")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            created_by=user,
            from_month=issue.created.date().replace(day=1),
            to_month=issue.created.date().replace(day=1),
        )
        batch.watched_journals.add(watched)

        from spanza_journal_watch.backend.models import PubmedArticle

        peds_keyword = PubmedArticle.objects.create(
            pmid="2010",
            title="Airway rescue in paediatric critical care",
            metadata_json={"mesh_terms": [], "keywords": ["paediatric"], "publication_types": ["Journal Article"]},
        )
        adult = PubmedArticle.objects.create(
            pmid="2011",
            title="Adult perioperative paper",
            metadata_json={"mesh_terms": [], "keywords": ["adults"], "publication_types": ["Journal Article"]},
        )

        PubmedBatchArticle.objects.create(batch=batch, article=peds_keyword, watched_journal=watched, issue=issue)
        PubmedBatchArticle.objects.create(batch=batch, article=adult, watched_journal=watched, issue=issue)

        filtered_response = route_client.get(
            reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}),
            data={"paediatric_only": "1"},
        )

        assert filtered_response.status_code == 200
        filtered_body = filtered_response.content.decode("utf-8", errors="ignore")
        assert "Airway rescue in paediatric critical care" in filtered_body
        assert "Adult perioperative paper" not in filtered_body

    def test_article_intake_refresh_batch(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        credential = PubmedIntegrationCredential(singleton=1)
        credential.set_api_key("pubmed-valid-key")
        credential.configured_by = user
        credential.save()

        issue = Issue.objects.create(name="Refresh Batch Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="Watch Refresh", issn_print="5555-6666")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            created_by=user,
            from_month=issue.created.date().replace(day=1),
            to_month=issue.created.date().replace(day=1),
        )
        batch.watched_journals.add(watched)

        called = {"value": False}

        def _fake_import(target_batch, watched_journals):
            called["value"] = True
            assert target_batch.pk == batch.pk
            assert watched in watched_journals

        monkeypatch.setattr("spanza_journal_watch.backend.views._import_pubmed_batch", _fake_import)

        response = route_client.post(reverse("backend:article_intake_refresh_batch", kwargs={"batch_id": batch.pk}))

        assert response.status_code == 302
        assert called["value"] is True
        assert f"batch={batch.pk}" in response.headers.get("Location", "")

    def test_article_intake_refresh_batch_without_api_key(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        issue = Issue.objects.create(name="Refresh No Key Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="Watch Refresh No Key", issn_print="1010-2020")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            created_by=user,
            from_month=issue.created.date().replace(day=1),
            to_month=issue.created.date().replace(day=1),
        )
        batch.watched_journals.add(watched)

        called = {"value": False}

        def _fake_import(target_batch, watched_journals):
            called["value"] = True
            assert target_batch.pk == batch.pk
            assert watched in watched_journals

        monkeypatch.setattr("spanza_journal_watch.backend.views._import_pubmed_batch", _fake_import)

        response = route_client.post(reverse("backend:article_intake_refresh_batch", kwargs={"batch_id": batch.pk}))

        assert response.status_code == 302
        assert called["value"] is True
        assert f"batch={batch.pk}" in response.headers.get("Location", "")

    def test_article_intake_push_to_planka_stores_card_id(self, route_client, regression_baseline, monkeypatch):
        user = User.objects.filter(is_superuser=False).order_by("pk").first()
        assert user is not None

        permission = Permission.objects.get(codename="manage_issue_builder")
        user.user_permissions.add(permission)
        route_client.force_login(user)

        issue = Issue.objects.create(name="Push Batch Issue", body="Issue body")
        watched = WatchedJournal.objects.create(name="Watch Push", issn_print="7777-8888")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            created_by=user,
            from_month=issue.created.date().replace(day=1),
            to_month=issue.created.date().replace(day=1),
        )
        batch.watched_journals.add(watched)

        from spanza_journal_watch.backend.models import PubmedArticle

        article = PubmedArticle.objects.create(
            pmid="3001",
            title="Push this paper",
            metadata_json={"mesh_terms": ["Pediatrics"], "keywords": [], "publication_types": ["Journal Article"]},
        )
        row = PubmedBatchArticle.objects.create(
            batch=batch,
            article=article,
            watched_journal=watched,
            issue=issue,
            is_selected=True,
        )

        PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="project-push-1",
            project_name="Push Project",
            board_id="board-push-1",
            board_name="Reviews",
            lists={"candidates": "list-candidates", "publish_ready": "list-publish"},
            custom_fields={},
            custom_field_group_id="cfg-1",
        )

        class FakePlankaClient:
            def get_board(self, board_id):
                return None, {"labels": [], "lists": []}

            def create_card(self, list_id, name, description="", position=65536, card_type="story"):
                assert list_id == "list-candidates"
                assert "Push this paper" in name
                return {"id": "card-xyz"}

        monkeypatch.setattr("spanza_journal_watch.backend.views._build_planka_client", lambda: FakePlankaClient())

        response = route_client.post(
            reverse("backend:article_intake_push_to_planka", kwargs={"batch_id": batch.pk}),
            data={"push_scope": "selected"},
        )

        assert response.status_code == 302
        row.refresh_from_db()
        assert row.planka_card_id == "card-xyz"
        assert row.planka_pushed_at is not None
