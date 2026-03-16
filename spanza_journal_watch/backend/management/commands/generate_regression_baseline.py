import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.core.management import BaseCommand, call_command
from django.db.models import Model
from django.test import Client

from spanza_journal_watch.newsletter.models import Subscriber
from spanza_journal_watch.submissions.models import Author, Issue, Review, Tag

MODEL_LABELS = [
    "users.user",
    "submissions.healthservice",
    "submissions.author",
    "submissions.tag",
    "submissions.journal",
    "submissions.article",
    "submissions.review",
    "submissions.issue",
    "layout.featurearticle",
    "layout.pageheader",
    "layout.homepage",
    "backend.subscribercsv",
    "newsletter.subscriber",
    "newsletter.newsletter",
]

TOKEN_FIELDS = {
    "newsletter.subscriber": ["unsubscribe_token"],
    "newsletter.newsletter": ["email_token", "send_token"],
    "backend.subscribercsv": ["save_token"],
}

EMAIL_FIELDS = {
    "users.user": ["email"],
    "newsletter.subscriber": ["email"],
}


class Command(BaseCommand):
    help = "Generate anonymized regression fixtures and HTML snapshots from the current local DB"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture-output",
            default="spanza_journal_watch/fixtures/regression_baseline.json",
            help="Path (relative to repo root) for fixture output JSON",
        )
        parser.add_argument(
            "--snapshot-dir",
            default="tests/regression/snapshots",
            help="Directory (relative to repo root) for HTML snapshots",
        )
        parser.add_argument(
            "--manifest-output",
            default="tests/regression/snapshots/manifest.json",
            help="Path (relative to repo root) for snapshot manifest JSON",
        )

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        fixture_output = base_dir / options["fixture_output"]
        snapshot_dir = base_dir / options["snapshot_dir"]
        manifest_output = base_dir / options["manifest_output"]

        fixture_output.parent.mkdir(parents=True, exist_ok=True)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        manifest_output.parent.mkdir(parents=True, exist_ok=True)

        self.stdout.write("Dumping fixture data...")
        fixture_data = self._dump_fixture_data()

        self.stdout.write("Anonymizing fixture data...")
        fixture_data = self._anonymize_fixture_data(fixture_data)

        fixture_output.write_text(json.dumps(fixture_data, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote fixture file: {fixture_output}"))

        self.stdout.write("Rendering route snapshots...")
        manifest = self._write_snapshots(snapshot_dir)
        manifest_output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote snapshot manifest: {manifest_output}"))

    def _dump_fixture_data(self) -> list[dict]:
        with NamedTemporaryFile(mode="w+", suffix=".json") as tmp_file:
            call_command(
                "dumpdata",
                *MODEL_LABELS,
                indent=2,
                stdout=tmp_file,
            )
            tmp_file.flush()
            tmp_file.seek(0)
            return json.load(tmp_file)

    def _anonymize_fixture_data(self, rows: list[dict]) -> list[dict]:
        for row in rows:
            model_label = row.get("model")
            pk = row.get("pk")
            fields = row.get("fields", {})

            for field in EMAIL_FIELDS.get(model_label, []):
                if fields.get(field):
                    fields[field] = self._anonymized_email(model_label, pk)

            for field in TOKEN_FIELDS.get(model_label, []):
                if field in fields and fields.get(field):
                    fields[field] = self._stable_token(model_label, field, pk)

            if model_label == "users.user" and fields.get("username"):
                fields["username"] = f"user-{pk}"

        return rows

    def _anonymized_email(self, model_label: str, pk: int) -> str:
        prefix = "subscriber" if model_label == "newsletter.subscriber" else "user"
        return f"{prefix}{pk}@example.test"

    def _stable_token(self, model_label: str, field: str, pk: int) -> str:
        value = f"{model_label}-{field}-{pk}"
        return re.sub(r"[^a-zA-Z0-9_-]", "-", value)[:64]

    def _write_snapshots(self, snapshot_dir: Path) -> dict:
        client = Client(HTTP_HOST="127.0.0.1:3000", raise_request_exception=False)
        routes = self._snapshot_routes()

        manifest: dict[str, dict] = {}
        for name, route in routes.items():
            path = route["path"]
            method = route.get("method", "get")
            kwargs = route.get("kwargs", {})

            response = getattr(client, method)(path, **kwargs)
            content_type = response.headers.get("Content-Type", "")
            status_code = response.status_code

            manifest[name] = {
                "path": path,
                "status_code": status_code,
                "content_type": content_type,
            }

            if "text/html" not in content_type:
                continue

            html = response.content.decode("utf-8", errors="ignore")
            normalized = self._normalize_html(html)
            snapshot_path = snapshot_dir / f"{name}.html"
            snapshot_path.write_text(normalized, encoding="utf-8")
            manifest[name]["snapshot_file"] = str(snapshot_path.relative_to(settings.BASE_DIR))

        return manifest

    def _snapshot_routes(self) -> dict[str, dict]:
        issue = self._first(Issue)
        review = self._first(Review)
        tag = self._first(Tag)
        author = self._first(Author, anonymous=False)
        subscriber = self._first(Subscriber)

        routes = {
            "home": {"path": "/"},
            "review_list_redirect": {"path": "/reviews"},
            "review_detail": {"path": f"/reviews/{review.slug}"} if review else {"path": "/reviews"},
            "issue_list": {"path": "/issues"},
            "issue_latest": {"path": "/issues/latest"},
            "issue_detail": {"path": f"/issues/{issue.slug}"} if issue else {"path": "/issues"},
            "tag_list": {"path": "/tags"},
            "tag_detail": {"path": f"/tags/{tag.slug}"} if tag else {"path": "/tags"},
            "search": {"path": "/search?q=anaesthesia"},
            "about": {"path": "/about"},
            "author_detail": {"path": f"/about/{author.slug}"} if author else {"path": "/about"},
            "newsletter_subscribe_htmx": {
                "path": "/newsletter/subscribe",
                "kwargs": {"HTTP_HX_REQUEST": "true"},
            },
            "newsletter_success": {"path": "/newsletter/success"},
            "newsletter_unsubscribe": (
                {"path": f"/newsletter/unsubscribe/{subscriber.unsubscribe_token}"}
                if subscriber
                else {"path": "/newsletter/success"}
            ),
        }
        return routes

    def _normalize_html(self, html: str) -> str:
        normalized = html
        normalized = re.sub(r"csrfmiddlewaretoken[^\"]+\"", 'csrfmiddlewaretoken" value="__CSRF__"', normalized)
        normalized = re.sub(
            r"name=['\"]csrfmiddlewaretoken['\"] value=['\"][^'\"]+['\"]",
            'name="csrfmiddlewaretoken" value="__CSRF__"',
            normalized,
        )
        normalized = re.sub(
            r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2})?",
            "__DATETIME__",
            normalized,
        )
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized + "\n"

    def _first(self, model: type[Model], **filters):
        queryset = model.objects.filter(**filters) if filters else model.objects.all()
        return queryset.order_by("pk").first()
