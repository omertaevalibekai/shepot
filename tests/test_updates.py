"""Проверка обновлений без сети: python -m pytest tests/test_updates.py"""

import io
import json

from shepot import updates


def test_versions_compare_numerically():
    assert updates.is_newer("v1.10.0", "1.9.3")
    assert updates.is_newer("Шёпот 1.3.1", "1.3.0")
    assert not updates.is_newer("1.3", "1.3.0")
    assert not updates.is_newer("1.2.0", "1.3.0")
    assert not updates.is_newer("nightly", "1.3.0")


def _fake_github(monkeypatch, payload=None, error=None):
    def urlopen(request, timeout):
        assert request.full_url == updates.API_URL
        if error:
            raise error
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)


def test_latest_release_is_read(monkeypatch):
    _fake_github(monkeypatch, {"tag_name": "v2.0.0", "html_url": "https://example/v2"})
    assert updates.fetch_latest() == {"version": "2.0.0", "url": "https://example/v2"}


def test_prerelease_is_ignored(monkeypatch):
    _fake_github(monkeypatch, {"tag_name": "v2.0.0", "prerelease": True})
    assert updates.fetch_latest() is None


def test_network_error_is_silent(monkeypatch):
    _fake_github(monkeypatch, error=OSError("нет сети"))
    assert updates.fetch_latest() is None
