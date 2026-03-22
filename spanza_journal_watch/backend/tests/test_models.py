"""
Tests for backend model methods and save() hooks.

Covers:
1. PlankaIntegrationCredential — encryption, masking, save() auto-encrypt
2. PubmedIntegrationCredential — same pattern
3. WatchedJournal.save() — name stripping, auto-Journal creation
4. IssueContributor.save() — email lowercasing
5. IssueContributorInvite — token generation, hashing, is_active()
6. PlankaIssueBinding — get_list_id(), get_custom_field_id()
"""

import datetime

import pytest
from django.utils import timezone

from spanza_journal_watch.backend.models import (
    IssueContributor,
    IssueContributorInvite,
    PlankaIntegrationCredential,
    PlankaIssueBinding,
    PubmedIntegrationCredential,
    WatchedJournal,
)
from spanza_journal_watch.submissions.models import Issue, Journal

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_issue(name="Test Issue"):
    return Issue.objects.create(name=name, active=False)


# ---------------------------------------------------------------------------
# 1. PlankaIntegrationCredential
# ---------------------------------------------------------------------------


class TestPlankaIntegrationCredential:
    def _make_cred(self, api_key=""):
        cred = PlankaIntegrationCredential(
            auth_mode=PlankaIntegrationCredential.AuthMode.API_KEY,
            api_key=api_key,
        )
        cred.save()
        return cred

    def test_set_and_get_api_key_roundtrip(self):
        cred = self._make_cred()
        cred.set_api_key("my-secret-key")
        cred.save()
        cred.refresh_from_db()
        assert cred.get_api_key() == "my-secret-key"

    def test_stored_value_is_not_plaintext(self):
        cred = self._make_cred()
        cred.set_api_key("my-secret-key")
        cred.save()
        cred.refresh_from_db()
        assert cred.api_key != "my-secret-key"

    def test_plain_value_auto_encrypted_on_save(self):
        """If a plain key is stored then saved, save() re-encrypts it."""
        cred = self._make_cred()
        # Bypass set_api_key and store a plain value directly:
        cred.api_key = "plain-value"
        cred.save()
        cred.refresh_from_db()
        # After save(), the stored value should be encrypted (not plain)
        assert cred.api_key != "plain-value"
        # And we can still retrieve the original value
        assert cred.get_api_key() == "plain-value"

    def test_empty_api_key(self):
        cred = self._make_cred()
        cred.set_api_key("")
        cred.save()
        cred.refresh_from_db()
        assert cred.get_api_key() == ""

    def test_get_masked_api_key_long_key(self):
        cred = self._make_cred()
        cred.set_api_key("abcdefghijklmnopqrstuvwxyz")
        cred.save()
        masked = cred.get_masked_api_key()
        assert masked.startswith("abcdef")
        assert masked.endswith("wxyz")
        assert "…" in masked

    def test_get_masked_api_key_short_key(self):
        cred = self._make_cred()
        cred.set_api_key("abc")
        cred.save()
        masked = cred.get_masked_api_key()
        assert masked == "***"

    def test_get_masked_api_key_empty(self):
        cred = self._make_cred()
        assert cred.get_masked_api_key() == ""

    def test_get_solo_returns_first(self):
        cred = self._make_cred()
        solo = PlankaIntegrationCredential.get_solo()
        assert solo.pk == cred.pk

    def test_get_solo_returns_none_when_empty(self):
        assert PlankaIntegrationCredential.get_solo() is None


# ---------------------------------------------------------------------------
# 2. PubmedIntegrationCredential
# ---------------------------------------------------------------------------


class TestPubmedIntegrationCredential:
    def _make_cred(self, api_key=""):
        cred = PubmedIntegrationCredential(api_key=api_key)
        cred.save()
        return cred

    def test_set_and_get_api_key_roundtrip(self):
        cred = self._make_cred()
        cred.set_api_key("pubmed-key-1234")
        cred.save()
        cred.refresh_from_db()
        assert cred.get_api_key() == "pubmed-key-1234"

    def test_stored_value_is_not_plaintext(self):
        cred = self._make_cred()
        cred.set_api_key("pubmed-key-1234")
        cred.save()
        cred.refresh_from_db()
        assert cred.api_key != "pubmed-key-1234"

    def test_get_masked_api_key_long_key(self):
        cred = self._make_cred()
        cred.set_api_key("1234567890abcdef")
        cred.save()
        masked = cred.get_masked_api_key()
        assert masked.startswith("1234")
        assert masked.endswith("cdef")
        assert "…" in masked

    def test_get_masked_api_key_short_key(self):
        """Keys ≤ 8 chars are fully masked."""
        cred = self._make_cred()
        cred.set_api_key("short")
        cred.save()
        masked = cred.get_masked_api_key()
        assert set(masked) == {"*"}

    def test_get_masked_api_key_empty(self):
        cred = self._make_cred()
        assert cred.get_masked_api_key() == ""

    def test_get_solo_returns_none_when_empty(self):
        assert PubmedIntegrationCredential.get_solo() is None

    def test_get_solo_returns_first(self):
        cred = self._make_cred()
        assert PubmedIntegrationCredential.get_solo().pk == cred.pk


# ---------------------------------------------------------------------------
# 3. WatchedJournal.save()
# ---------------------------------------------------------------------------


class TestWatchedJournalSave:
    def test_name_stripped_on_save(self):
        wj = WatchedJournal.objects.create(name="  Paediatric Anaesthesia  ")
        assert wj.name == "Paediatric Anaesthesia"

    def test_auto_creates_journal_when_none_linked(self):
        wj = WatchedJournal.objects.create(name="New Test Journal")
        assert wj.journal is not None
        assert Journal.objects.filter(name__iexact="New Test Journal").exists()

    def test_auto_reuses_existing_journal_by_name(self):
        journal = Journal.objects.create(name="Existing Journal", active=True)
        wj = WatchedJournal.objects.create(name="Existing Journal")
        assert wj.journal_id == journal.pk
        # No duplicate created
        assert Journal.objects.filter(name__iexact="Existing Journal").count() == 1

    def test_case_insensitive_journal_lookup(self):
        journal = Journal.objects.create(name="CASE Journal", active=True)
        wj = WatchedJournal.objects.create(name="case journal")
        assert wj.journal_id == journal.pk

    def test_does_not_create_journal_if_already_linked(self):
        journal = Journal.objects.create(name="Pre-linked", active=True)
        wj = WatchedJournal.objects.create(name="Something Different", journal=journal)
        # Should not create a new journal for "Something Different"
        assert not Journal.objects.filter(name__iexact="Something Different").exists()
        assert wj.journal_id == journal.pk

    def test_active_true_by_default(self):
        wj = WatchedJournal.objects.create(name="Active Journal")
        assert wj.active is True


# ---------------------------------------------------------------------------
# 4. IssueContributor.save()
# ---------------------------------------------------------------------------


class TestIssueContributorSave:
    def test_email_lowercased_on_save(self):
        issue = make_issue()
        contributor = IssueContributor.objects.create(
            issue=issue,
            email="REVIEWER@Example.COM",
            role=IssueContributor.Role.REVIEWER,
        )
        assert contributor.email == "reviewer@example.com"

    def test_email_stripped_on_save(self):
        issue = make_issue()
        contributor = IssueContributor.objects.create(
            issue=issue,
            email="  alice@example.com  ",
            role=IssueContributor.Role.REVIEWER,
        )
        assert contributor.email == "alice@example.com"

    def test_unique_email_per_issue_constraint(self):
        from django.db import IntegrityError

        issue = make_issue()
        IssueContributor.objects.create(issue=issue, email="dup@example.com")
        with pytest.raises(IntegrityError):
            IssueContributor.objects.create(issue=issue, email="dup@example.com")

    def test_same_email_allowed_on_different_issues(self):
        issue_a = make_issue("Issue A")
        issue_b = make_issue("Issue B")
        IssueContributor.objects.create(issue=issue_a, email="shared@example.com")
        IssueContributor.objects.create(issue=issue_b, email="shared@example.com")
        assert IssueContributor.objects.filter(email="shared@example.com").count() == 2


# ---------------------------------------------------------------------------
# 5. IssueContributorInvite
# ---------------------------------------------------------------------------


class TestIssueContributorInvite:
    def _make_contributor(self):
        issue = make_issue()
        return IssueContributor.objects.create(
            issue=issue,
            email="reviewer@example.com",
        )

    def test_generate_raw_token_is_string(self):
        token = IssueContributorInvite.generate_raw_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_generate_raw_token_unique(self):
        t1 = IssueContributorInvite.generate_raw_token()
        t2 = IssueContributorInvite.generate_raw_token()
        assert t1 != t2

    def test_hash_token_deterministic(self):
        raw = "test-token-abc"
        h1 = IssueContributorInvite.hash_token(raw)
        h2 = IssueContributorInvite.hash_token(raw)
        assert h1 == h2

    def test_hash_token_is_64_hex_chars(self):
        raw = IssueContributorInvite.generate_raw_token()
        h = IssueContributorInvite.hash_token(raw)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_token_different_inputs_differ(self):
        assert IssueContributorInvite.hash_token("a") != IssueContributorInvite.hash_token("b")

    def test_is_active_true_for_fresh_invite(self):
        contributor = self._make_contributor()
        raw = IssueContributorInvite.generate_raw_token()
        invite = IssueContributorInvite.objects.create(
            contributor=contributor,
            token_hash=IssueContributorInvite.hash_token(raw),
            expires_at=timezone.now() + datetime.timedelta(days=7),
        )
        assert invite.is_active() is True

    def test_is_active_false_when_consumed(self):
        contributor = self._make_contributor()
        raw = IssueContributorInvite.generate_raw_token()
        invite = IssueContributorInvite.objects.create(
            contributor=contributor,
            token_hash=IssueContributorInvite.hash_token(raw),
            expires_at=timezone.now() + datetime.timedelta(days=7),
            consumed_at=timezone.now(),
        )
        assert invite.is_active() is False

    def test_is_active_false_when_expired(self):
        contributor = self._make_contributor()
        raw = IssueContributorInvite.generate_raw_token()
        invite = IssueContributorInvite.objects.create(
            contributor=contributor,
            token_hash=IssueContributorInvite.hash_token(raw),
            expires_at=timezone.now() - datetime.timedelta(seconds=1),
        )
        assert invite.is_active() is False

    def test_token_hash_unique_constraint(self):
        from django.db import IntegrityError

        contributor = self._make_contributor()
        raw = IssueContributorInvite.generate_raw_token()
        h = IssueContributorInvite.hash_token(raw)
        IssueContributorInvite.objects.create(
            contributor=contributor,
            token_hash=h,
            expires_at=timezone.now() + datetime.timedelta(days=7),
        )
        with pytest.raises(IntegrityError):
            IssueContributorInvite.objects.create(
                contributor=contributor,
                token_hash=h,
                expires_at=timezone.now() + datetime.timedelta(days=7),
            )


# ---------------------------------------------------------------------------
# 6. PlankaIssueBinding helpers
# ---------------------------------------------------------------------------


class TestPlankaIssueBindingHelpers:
    def _make_binding(self):
        issue = make_issue()
        return PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="proj-abc",
            project_name="Test",
            board_id="board-abc",
            lists={"candidates": "list-1", "under_review": "list-2"},
            custom_fields={"pmid": "cf-1", "doi": "cf-2"},
        )

    def test_get_list_id_existing_key(self):
        binding = self._make_binding()
        assert binding.get_list_id("candidates") == "list-1"
        assert binding.get_list_id("under_review") == "list-2"

    def test_get_list_id_missing_key_returns_none(self):
        binding = self._make_binding()
        assert binding.get_list_id("nonexistent") is None

    def test_get_custom_field_id_existing_key(self):
        binding = self._make_binding()
        assert binding.get_custom_field_id("pmid") == "cf-1"
        assert binding.get_custom_field_id("doi") == "cf-2"

    def test_get_custom_field_id_missing_key_returns_none(self):
        binding = self._make_binding()
        assert binding.get_custom_field_id("missing") is None

    def test_get_list_id_empty_lists(self):
        issue = make_issue()
        binding = PlankaIssueBinding.objects.create(issue=issue, project_id="p2", project_name="P", board_id="b2")
        assert binding.get_list_id("candidates") is None
