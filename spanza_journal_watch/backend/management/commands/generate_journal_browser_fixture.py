import datetime
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, call_command
from django.utils import timezone

from spanza_journal_watch.backend.models import (
    BackendPreference,
    PubmedArticle,
    PubmedArticleUserState,
    WatchedJournal,
    WatchedJournalArticle,
)

User = get_user_model()

MODEL_LABELS = [
    "users.user",
    "submissions.journal",
    "backend.backendpreference",
    "backend.watchedjournal",
    "backend.pubmedarticle",
    "backend.watchedjournalarticle",
    "backend.pubmedarticleuserstate",
]


class Command(BaseCommand):
    help = "Generate a reusable sample fixture for the journals browser and cached PubMed workflow."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture-output",
            default="spanza_journal_watch/fixtures/journal_browser_sample.json",
            help="Path (relative to repo root) for fixture output JSON",
        )

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        fixture_output = base_dir / options["fixture_output"]
        fixture_output.parent.mkdir(parents=True, exist_ok=True)

        self.sample_ids = {label: set() for label in MODEL_LABELS}
        self._seed_sample_data()

        with NamedTemporaryFile(mode="w+", suffix=".json") as tmp_file:
            call_command("dumpdata", *MODEL_LABELS, indent=2, stdout=tmp_file)
            tmp_file.flush()
            tmp_file.seek(0)
            rows = json.load(tmp_file)

        rows = [row for row in rows if row["model"] in self.sample_ids and row["pk"] in self.sample_ids[row["model"]]]

        fixture_output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote fixture file: {fixture_output}"))

    def _seed_sample_data(self):
        now = timezone.now()
        self._seed_user()
        watched_journals = self._seed_watched_journals()
        self._seed_articles(watched_journals, now)
        self._seed_backend_preferences(watched_journals)

    def _seed_user(self):
        user, _ = User.objects.update_or_create(
            email="journal-browser-demo@example.test",
            defaults={
                "name": "Journal Browser Demo",
                "is_active": True,
            },
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        self.sample_ids["users.user"].add(user.pk)
        return user

    def _seed_watched_journals(self):
        journal_specs = [
            {
                "name": "Pediatric Anesthesia",
                "issn_print": "1155-5645",
                "issn_electronic": "1460-9592",
            },
            {
                "name": "British Journal of Anaesthesia",
                "issn_print": "0007-0912",
                "issn_electronic": "1471-6771",
            },
            {
                "name": "Anesthesiology",
                "issn_print": "0003-3022",
                "issn_electronic": "1528-1175",
            },
            {
                "name": "Paediatric & Neonatal Pain",
                "issn_print": "2634-4891",
                "issn_electronic": "2634-4905",
            },
            {
                "name": "Anaesthesia",
                "issn_print": "0003-2409",
                "issn_electronic": "1365-2044",
            },
            {
                "name": "Regional Anesthesia and Pain Medicine",
                "issn_print": "1098-7339",
                "issn_electronic": "1532-8651",
            },
        ]

        watched_journals = []
        for spec in journal_specs:
            watched, _ = WatchedJournal.objects.update_or_create(
                name=spec["name"],
                defaults={
                    "issn_print": spec["issn_print"],
                    "issn_electronic": spec["issn_electronic"],
                    "active": True,
                },
            )
            watched_journals.append(watched)
            self.sample_ids["backend.watchedjournal"].add(watched.pk)
            if watched.journal_id:
                self.sample_ids["submissions.journal"].add(watched.journal_id)

        demo_user = User.objects.get(email="journal-browser-demo@example.test")
        demo_user.watched_journals.set(watched_journals[:3])
        return watched_journals

    def _seed_articles(self, watched_journals, now):
        month_starts = [
            datetime.date(now.year, now.month, 1),
            self._shift_month(datetime.date(now.year, now.month, 1), -1),
            self._shift_month(datetime.date(now.year, now.month, 1), -2),
            self._shift_month(datetime.date(now.year, now.month, 1), -3),
        ]
        publication_types = [
            "Journal Article",
            "Review",
            "Clinical Trial",
            "Editorial",
            "Practice Guideline",
        ]
        topic_pairs = [
            ("airway", "Airway Management"),
            ("sedation", "Conscious Sedation"),
            ("regional", "Regional Anesthesia"),
            ("pain", "Acute Pain"),
            ("neonatal", "Infant, Newborn"),
            ("simulation", "Simulation Training"),
        ]

        for journal_index, watched in enumerate(watched_journals, start=1):
            for month_index, publication_month in enumerate(month_starts, start=1):
                for article_number in range(1, 3):
                    topic_slug, mesh_term = topic_pairs[
                        (journal_index + month_index + article_number) % len(topic_pairs)
                    ]
                    publication_date = publication_month + datetime.timedelta(days=min(6 + article_number * 5, 26))
                    pmid = f"950{journal_index}{month_index}{article_number:02d}"
                    doi = f"10.5555/jw.{publication_month:%Y%m}.{journal_index}{article_number}"
                    article, _ = PubmedArticle.objects.update_or_create(
                        pmid=pmid,
                        defaults={
                            "doi": doi,
                            "title": (
                                f"{watched.name}: {mesh_term.lower()} update {article_number} "
                                f"for {publication_month:%B %Y}"
                            ),
                            "abstract": (
                                f"Synthetic fixture abstract for {watched.name} in {publication_month:%B %Y}. "
                                f"This sample article focuses on {topic_slug}, perioperative care, "
                                f"and journal browser filtering."
                            ),
                            "source_journal_name": watched.name,
                            "publication_date": publication_date,
                            "publication_month": publication_month,
                            "article_url": f"https://doi.org/{doi}",
                            "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                            "metadata_json": {
                                "publication_types": [
                                    "Journal Article",
                                    publication_types[
                                        (journal_index + article_number + month_index) % len(publication_types)
                                    ],
                                ],
                                "mesh_terms": [
                                    mesh_term,
                                    "Child",
                                    "Humans",
                                ],
                                "keywords": [
                                    topic_slug,
                                    "journal-browser",
                                    publication_month.strftime("%Y-%m"),
                                ],
                            },
                        },
                    )
                    self.sample_ids["backend.pubmedarticle"].add(article.pk)
                    link, _ = WatchedJournalArticle.objects.update_or_create(
                        watched_journal=watched,
                        article=article,
                        defaults={
                            "publication_month": publication_month,
                            "first_seen_at": now - datetime.timedelta(days=30 + month_index),
                            "last_seen_at": now - datetime.timedelta(days=article_number),
                        },
                    )
                    self.sample_ids["backend.watchedjournalarticle"].add(link.pk)

                    demo_user = User.objects.get(email="journal-browser-demo@example.test")
                    if watched.name in {"Pediatric Anesthesia", "British Journal of Anaesthesia"} and month_index <= 2:
                        state, _ = PubmedArticleUserState.objects.get_or_create(user=demo_user, article=article)
                        state.starred_at = now - datetime.timedelta(days=article_number)
                        if article_number == 1:
                            state.recommended_at = now - datetime.timedelta(days=month_index)
                        else:
                            state.recommended_at = None
                        state.save(update_fields=["starred_at", "recommended_at", "modified"])
                        self.sample_ids["backend.pubmedarticleuserstate"].add(state.pk)
                    else:
                        PubmedArticleUserState.objects.filter(user=demo_user, article=article).delete()

    def _seed_backend_preferences(self, watched_journals):
        prefs, _ = BackendPreference.objects.get_or_create(singleton=1)
        prefs.default_watched_journals.set(watched_journals[:4])
        prefs.frontend_banner_enabled = True
        prefs.frontend_banner_title = "New journals browser"
        prefs.frontend_banner_text = "Browse cached NIH articles by journal and month, then star or recommend papers."
        prefs.frontend_banner_link_text = "Open journals"
        prefs.frontend_banner_link_url = "/journals"
        prefs.frontend_banner_tone = BackendPreference.BannerTone.PRIMARY
        prefs.save(
            update_fields=[
                "frontend_banner_enabled",
                "frontend_banner_title",
                "frontend_banner_text",
                "frontend_banner_link_text",
                "frontend_banner_link_url",
                "frontend_banner_tone",
                "modified",
            ]
        )
        self.sample_ids["backend.backendpreference"].add(prefs.pk)

    def _shift_month(self, month_start, delta):
        year = month_start.year + ((month_start.month - 1 + delta) // 12)
        month = ((month_start.month - 1 + delta) % 12) + 1
        return datetime.date(year, month, 1)
