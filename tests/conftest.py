import os

import pytest


@pytest.fixture(autouse=True)
def pinned_day(monkeypatch):
    """Pin the demo day (a Wednesday) so fixture offsets are reproducible."""
    monkeypatch.setenv("REGINA_TODAY", "2026-09-16")
    monkeypatch.setenv("REGINA_MODE", "mock")
    monkeypatch.setenv("REGINA_SUBAGENTS", "mock")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
