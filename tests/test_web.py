"""Offline smoke test for the web UI server: events stream before the answer."""

import json
import threading
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest

from regina import Regina
from regina_web import build_server


@pytest.fixture
def base_url():
    server = build_server(Regina(mode="mock"), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def read_events(url):
    events = []
    with urllib.request.urlopen(url, timeout=10) as resp:
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        for raw in resp:
            line = raw.decode().strip()
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def test_info_reports_mode_and_roster(base_url):
    with urllib.request.urlopen(f"{base_url}/api/info", timeout=10) as resp:
        info = json.load(resp)
    assert info["mode"] == "mock"
    assert [a["name"] for a in info["roster"]] == ["Email Agent", "Calendar Agent", "Anthropic News Agent"]


def test_ask_streams_delegations_then_the_answer(base_url):
    events = read_events(f"{base_url}/api/ask?q={quote('Do I have any conflicts today?')}")
    kinds = [e["kind"] for e in events]
    assert "delegate" in kinds and "reply" in kinds
    assert kinds[-1] == "answer"
    assert kinds.index("delegate") < kinds.index("answer")
    assert "CONFLICT" in events[-1]["text"] or "conflict" in events[-1]["text"].lower()


def test_briefing_fans_out_to_all_three(base_url):
    events = read_events(f"{base_url}/api/briefing")
    delegated = sorted(e["agent"] for e in events if e["kind"] == "delegate")
    assert delegated == ["Anthropic News Agent", "Calendar Agent", "Email Agent"]
    assert "## Needs your attention" in events[-1]["text"]


def test_index_page_is_served(base_url):
    with urllib.request.urlopen(f"{base_url}/", timeout=10) as resp:
        assert "Regina" in resp.read().decode()


# ---- deployment: optional password and health check ---------------------------


@pytest.fixture
def locked_url():
    server = build_server(Regina(mode="mock"), host="127.0.0.1", port=0, password="s3cret")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def basic_auth(password):
    import base64

    return {"Authorization": "Basic " + base64.b64encode(f"demo:{password}".encode()).decode()}


def status_of(url, headers=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


def test_password_protects_every_page(locked_url):
    assert status_of(f"{locked_url}/") == 401
    assert status_of(f"{locked_url}/api/info") == 401
    assert status_of(f"{locked_url}/api/info", basic_auth("wrong")) == 401
    assert status_of(f"{locked_url}/api/info", basic_auth("s3cret")) == 200


def test_health_check_is_open_even_with_a_password(locked_url):
    assert status_of(f"{locked_url}/healthz") == 200
