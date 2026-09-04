"""Signed-in state in the masthead, the anonymous recommend prompt, and sign-in by code."""

import re

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.users.context_processors import user_initials, user_masthead_label, user_short_name
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


class TestInitials:
    @pytest.mark.parametrize(
        "name,email,initials,short,label",
        [
            ("Priya Nair", "p@example.org", "PN", "Priya", "Priya"),
            ("Dr Tom Whitlock", "t@example.org", "TW", "Dr", "Dr"),
            ("Eamonn", "e@example.org", "E", "Eamonn", "Eamonn"),
            ("", "andrew.hughes@example.org", "A", "andrew.hughes", "Signed in"),
        ],
    )
    def test_initials_and_labels(self, name, email, initials, short, label):
        user = UserFactory(name=name, email=email)
        assert user_initials(user) == initials
        assert user_short_name(user) == short
        assert user_masthead_label(user) == label


class TestMasthead:
    def test_anonymous_sees_sign_in(self, client):
        body = client.get("/").content.decode()
        assert 'aria-label="Sign in or subscribe"' in body
        assert "jw-profile-btn__initials" not in body

    def test_subscriber_cookie_alone_does_not_look_signed_in(self, client):
        client.cookies["jw_sub"] = "1"
        body = client.get("/").content.decode()
        assert "jw-profile-btn__initials" not in body
        assert ">Sign in<" in body

    def test_signed_in_shows_initials_and_first_name(self, client):
        user = UserFactory(name="Priya Nair")
        client.force_login(user)
        body = client.get("/").content.decode()
        assert 'class="jw-profile-btn__icon jw-profile-btn__initials" aria-hidden="true">PN<' in body
        assert ">Priya<" in body

    def test_signed_in_without_a_name_reads_signed_in(self, client):
        client.force_login(UserFactory(name="", email="andrew.hughes@example.org"))
        body = client.get("/").content.decode()
        assert 'jw-profile-btn__initials" aria-hidden="true">A<' in body
        assert ">Signed in<" in body


class TestVisitorRecommendations:
    """Recommending works without an account; visitors are counted separately and folded in on sign-in."""

    def _article(self, pmid="90001", title="An article"):
        from spanza_journal_watch.backend.models import PubmedArticle

        return PubmedArticle.objects.create(pmid=pmid, title=title)

    def _toggle(self, client, article, **headers):
        return client.post(
            reverse("submissions:journal_article_toggle_recommend", kwargs={"article_id": article.pk}),
            {"next": "/journals/"},
            HTTP_HX_REQUEST="true",
            **headers,
        )

    def test_visitor_can_recommend_and_withdraw(self, client):
        from spanza_journal_watch.backend.models import PubmedArticleVisitorRecommendation

        article = self._article()
        response = self._toggle(client, article)
        assert response.status_code == 200
        assert ">Recommended<" in response.content.decode()
        assert "recommendation has been counted" in response["HX-Trigger"]
        assert PubmedArticleVisitorRecommendation.objects.filter(article=article).count() == 1
        assert client.session["recommended_article_ids"] == [article.pk]

        response = self._toggle(client, article)
        assert ">Recommend for review<" in response.content.decode()
        assert "HX-Trigger" not in response
        assert not PubmedArticleVisitorRecommendation.objects.filter(article=article).exists()

    def test_one_recommendation_per_session(self, client):
        from django.test import Client

        from spanza_journal_watch.backend.models import PubmedArticleVisitorRecommendation

        article = self._article()
        self._toggle(client, article)
        assert PubmedArticleVisitorRecommendation.objects.filter(article=article).count() == 1
        # A different browser (new session) counts once more. Real browsers always send a UA;
        # an empty one is treated as automated and ignored.
        Client(HTTP_USER_AGENT="Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Safari/605.1.15").post(
            reverse("submissions:journal_article_toggle_recommend", kwargs={"article_id": article.pk}),
            {"next": "/journals/"},
        )
        assert PubmedArticleVisitorRecommendation.objects.filter(article=article).count() == 2

    def test_bots_are_not_counted(self, client):
        from spanza_journal_watch.backend.models import PubmedArticleVisitorRecommendation

        article = self._article()
        response = self._toggle(client, article, HTTP_USER_AGENT="Mozilla/5.0 (compatible; SemrushBot/7~bl)")
        assert response.status_code == 200
        assert not PubmedArticleVisitorRecommendation.objects.filter(article=article).exists()
        assert ">Recommend for review<" in response.content.decode()

    def test_counts_are_split_for_coordinators(self, client):
        from django.contrib.auth.models import Permission
        from django.test import Client

        from spanza_journal_watch.backend.models import (
            PubmedArticleUserState,
            PubmedBatchArticle,
            PubmedImportBatch,
            WatchedJournal,
        )
        from spanza_journal_watch.submissions.models import Issue

        article = self._article()
        self._toggle(client, article)  # one visitor
        PubmedArticleUserState.objects.create(user=UserFactory(), article=article, recommended_at=timezone.now())

        editor = UserFactory()
        editor.user_permissions.add(
            Permission.objects.get(codename="manage_issue_builder"),
            Permission.objects.get(codename="chief_editor"),
        )
        staff = Client()
        staff.force_login(editor)
        issue = Issue.objects.create(name="Counts issue", body="body")
        journal = WatchedJournal.objects.create(name="Journal", active=True)
        today = timezone.localdate().replace(day=1)
        batch = PubmedImportBatch.objects.create(issue=issue, created_by=editor, from_month=today, to_month=today)
        batch.watched_journals.add(journal)
        PubmedBatchArticle.objects.create(batch=batch, article=article, watched_journal=journal, issue=issue)

        response = staff.get(
            reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}), {"paediatric_only": "0"}
        )
        row = next(r for r in response.context["result_rows"] if r.article_id == article.pk)
        assert row.recommendation_count == 1
        assert row.visitor_recommendation_count == 1
        assert "1 visitor" in response.content.decode()

    def test_visitor_recommendation_is_attributed_on_sign_in(self, client):
        from allauth.account.models import EmailAddress

        from spanza_journal_watch.backend.models import PubmedArticleUserState, PubmedArticleVisitorRecommendation

        article = self._article()
        self._toggle(client, article)
        user = UserFactory(email="visitor.turned.member@example.org")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

        mail.outbox.clear()
        client.post(reverse("users:start"), {"email": user.email})
        code = _extract_code(mail.outbox[0].body)
        client.post(reverse("account_confirm_login_code"), {"code": code})

        state = PubmedArticleUserState.objects.get(user=user, article=article)
        assert state.recommended_at is not None
        assert not PubmedArticleVisitorRecommendation.objects.filter(article=article).exists()
        assert "recommended_article_ids" not in client.session


class TestNudges:
    def test_toast_is_available_on_every_page_for_visitors(self, client):
        body = client.get("/").content.decode()
        assert 'id="login-prompt-toast"' in body
        assert "data-prompt-text" in body

    def test_toast_absent_when_signed_in(self, client):
        client.force_login(UserFactory())
        assert 'id="login-prompt-toast"' not in client.get("/").content.decode()

    def test_login_page_explains_cpd(self, client):
        body = client.get(reverse("account_login") + "?next=/cpd/report/").content.decode()
        assert "CPD report" in body


class TestSignInByCode:
    def test_login_page_leads_with_code_request(self, client):
        body = client.get(reverse("account_login")).content.decode()
        assert reverse("users:start") in body
        assert "Email me a code" in body
        assert "Sign in with a password instead" in body

    def test_signup_has_no_password_fields(self, client):
        body = client.get(reverse("account_signup")).content.decode()
        assert 'name="password1"' not in body
        assert 'name="name"' not in body
        assert 'name="email"' in body
        assert "Email me a code" in body

    def test_existing_user_can_sign_in_with_emailed_code(self, client):
        from allauth.account.models import EmailAddress

        user = UserFactory(email="member@example.org")
        # Real accounts have a verified address; without one allauth asks for a second, verification code.
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        mail.outbox.clear()
        response = client.post(reverse("account_request_login_code"), {"email": user.email})
        assert response.status_code == 302
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.subject == "Your Journal Watch sign-in code"
        assert message.to == [user.email]
        code = _extract_code(message.body)
        assert re.fullmatch(r"\d{6}", code), code

        page = client.get(reverse("account_confirm_login_code")).content.decode()
        assert "Enter your sign-in code" in page

        response = client.post(reverse("account_confirm_login_code"), {"code": code})
        assert response.status_code == 302
        assert client.get("/").context["request"].user.is_authenticated

    def test_passwordless_signup_sends_verification_code_and_signs_in(self, client):
        mail.outbox.clear()
        response = client.post(
            reverse("account_signup"),
            {"email": "new.member@example.org", "subscribe_to_newsletter": "on"},
        )
        assert response.status_code == 302
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert "Confirm your email address" in message.subject
        code = _extract_code(message.body)

        page = client.get(reverse("account_email_verification_sent")).content.decode()
        assert "Confirm your email address" in page

        response = client.post(reverse("account_email_verification_sent"), {"code": code})
        assert response.status_code == 302
        request_user = client.get("/").context["request"].user
        assert request_user.is_authenticated
        assert request_user.email == "new.member@example.org"
        assert not request_user.has_usable_password()

    def test_start_sends_sign_in_code_to_existing_account(self, client):
        from allauth.account.models import EmailAddress

        user = UserFactory(email="known@example.org")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        mail.outbox.clear()
        response = client.post(reverse("users:start"), {"email": "Known@Example.org"})
        assert response.status_code == 302
        assert response.url == reverse("account_confirm_login_code")
        assert [m.subject for m in mail.outbox] == ["Your Journal Watch sign-in code"]

    def test_start_creates_account_for_new_address(self, client):
        from django.contrib.auth import get_user_model

        mail.outbox.clear()
        response = client.post(reverse("users:start"), {"email": "first.timer@example.org"})
        assert response.status_code == 302
        assert response.url == reverse("account_email_verification_sent")
        assert get_user_model().objects.filter(email="first.timer@example.org").exists()
        assert [m.subject for m in mail.outbox] == ["Confirm your email address for Journal Watch"]
        code = _extract_code(mail.outbox[0].body)
        client.post(reverse("account_email_verification_sent"), {"code": code})
        assert client.get("/").context["request"].user.is_authenticated

    def test_start_rejects_invalid_email(self, client):
        response = client.post(reverse("users:start"), {"email": "not-an-email"})
        assert response.status_code == 302
        assert response.url == reverse("account_login")


def _extract_code(body):
    """allauth prints the code on its own line in the plain-text email."""
    for line in body.splitlines():
        token = line.strip()
        if re.fullmatch(r"\d{6}", token):
            return token
    raise AssertionError(f"no code found in email body:\n{body}")
