import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from spanza_journal_watch.backend.pubmed import PubmedAPIError, PubmedClient


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_request_json_retries_after_429(monkeypatch):
    client = PubmedClient(api_key="", timeout=5, max_retries=2)
    sleep_calls = []
    attempts = {"count": 0}

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "1"},
                None,
            )
        return _FakeResponse(json.dumps({"ok": True}))

    monkeypatch.setattr("spanza_journal_watch.backend.pubmed.time.sleep", sleep_calls.append)
    monkeypatch.setattr("spanza_journal_watch.backend.pubmed.urllib.request.urlopen", fake_urlopen)

    assert client._request_json("einfo.fcgi", {"db": "pubmed"}) == {"ok": True}
    assert attempts["count"] == 2
    assert 1 in sleep_calls


def test_request_json_raises_after_exhausting_429_retries(monkeypatch):
    client = PubmedClient(api_key="", timeout=5, max_retries=1)
    response_headers = MagicMock()
    response_headers.get.return_value = None

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            response_headers,
            None,
        )

    monkeypatch.setattr("spanza_journal_watch.backend.pubmed.time.sleep", lambda _: None)
    monkeypatch.setattr("spanza_journal_watch.backend.pubmed.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(PubmedAPIError, match="429"):
        client._request_json("einfo.fcgi", {"db": "pubmed"})


def test_search_pmids_history_uses_history_server(monkeypatch):
    client = PubmedClient(api_key="abc123", timeout=5, tool="jw", email="queries@example.com")

    monkeypatch.setattr(
        client,
        "_request_json",
        lambda endpoint, params: {
            "esearchresult": {
                "count": "42",
                "webenv": "test-webenv",
                "querykey": "1",
                "idlist": [],
            }
        },
    )

    result = client.search_pmids_history(
        "asthma",
        __import__("datetime").date(2026, 1, 1),
        __import__("datetime").date(2026, 1, 31),
    )

    assert result == {
        "count": 42,
        "webenv": "test-webenv",
        "query_key": "1",
        "pmids": [],
    }
