"""
Shared fixtures for integration tests.

These tests hit real external services and are excluded from the default
test run. Run them explicitly with: pytest -m integration
"""

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

CASSETTES_DIR = Path(__file__).parent / "cassettes"

# ---------------------------------------------------------------------------
# PubMed cassette helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def pubmed_cassette_dir():
    """Return the cassettes directory path for PubMed recorded responses."""
    return CASSETTES_DIR


def load_cassette(name):
    """Load a JSON cassette file by name (without extension)."""
    path = CASSETTES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"Cassette {name}.json not found — run with --record to capture")
    with open(path) as f:
        return json.load(f)


def save_cassette(name, data):
    """Save data to a JSON cassette file."""
    path = CASSETTES_DIR / f"{name}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Planka fixtures
# ---------------------------------------------------------------------------

PLANKA_ADMIN_EMAIL = "demo@demo.demo"

# Inside Docker: planka:1337 (service name). Outside Docker: localhost:3001 (port mapping).
PLANKA_URLS = ["http://planka:1337", "http://localhost:3001"]

# Planka's DB URL mirrors the compose config (planka_postgres service, trust auth).
PLANKA_DB_URLS = [
    "postgresql://postgres@planka_postgres/planka",
    "postgresql://postgres@localhost:5433/planka",
]


def _find_planka_url():
    for url in PLANKA_URLS:
        try:
            resp = requests.get(f"{url}/api/bootstrap", timeout=3)
            if resp.status_code == 200:
                return url
        except (requests.ConnectionError, requests.Timeout):
            continue
    return None


def _get_planka_api_key():
    """Generate a Planka API key via direct DB write, same as setup_planka_api_key.

    This bypasses the terms-acceptance requirement by writing directly to
    Planka's Postgres, exactly as the management command does.
    """
    try:
        import psycopg2
    except ImportError:
        return None

    conn = None
    for db_url in PLANKA_DB_URLS:
        try:
            conn = psycopg2.connect(db_url, connect_timeout=3)
            break
        except Exception:
            continue

    if conn is None:
        return None

    try:
        plain_key = secrets.token_hex(32)
        key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
        key_prefix = plain_key[:8]
        now = datetime.now(timezone.utc)

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM user_account WHERE email = %s",
                    (PLANKA_ADMIN_EMAIL,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                user_id = row[0]

                cur.execute(
                    """
                    UPDATE user_account
                    SET terms_accepted_at  = %s,
                        terms_signature    = %s,
                        api_key_prefix     = %s,
                        api_key_hash       = %s,
                        api_key_created_at = %s
                    WHERE id = %s
                    """,
                    (now, "accepted-via-integration-tests", key_prefix, key_hash, now, user_id),
                )
        return plain_key
    except Exception:
        return None
    finally:
        conn.close()


@pytest.fixture(scope="session")
def _planka_api_key():
    """Session-scoped API key — generated once, reused across all Planka tests."""
    base_url = _find_planka_url()
    if not base_url:
        pytest.skip("Local Planka is not reachable")

    api_key = _get_planka_api_key()
    if not api_key:
        pytest.skip("Could not generate Planka API key (DB not reachable or psycopg2 missing)")

    return base_url, api_key


@pytest.fixture
def planka_client(_planka_api_key):
    """Return a PlankaClient authenticated against the local Planka instance."""
    base_url, api_key = _planka_api_key

    from spanza_journal_watch.backend.planka import PlankaClient

    return PlankaClient(base_url=base_url, api_key=api_key)


@pytest.fixture
def planka_test_project(planka_client):
    """Create and return a disposable Planka project for testing.

    The project is deleted after the test completes.
    """
    project = planka_client.create_project("__test_integration__")
    project_id = project["id"]
    yield project

    try:
        planka_client._request("DELETE", f"/projects/{project_id}")
    except Exception:
        pass
