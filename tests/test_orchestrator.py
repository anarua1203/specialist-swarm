from regina import Regina
from regina.orchestrator import compose_briefing


def collect():
    events = []
    return events, (lambda kind, payload: events.append((kind, payload)))


def test_briefing_fans_out_to_all_three_agents_and_follows_skill_sections():
    events, handler = collect()
    regina = Regina(mode="mock", on_event=handler)
    text = regina.briefing()
    delegated = [p["agent"] for k, p in events if k == "delegate"]
    assert sorted(delegated) == ["Anthropic News Agent", "Calendar Agent", "Email Agent"]
    assert [k for k, _ in events][0] == "fan_out"
    for section in ["## Needs your attention", "## Today's schedule", "## Anthropic news worth 30 seconds", "## Suggested replies"]:
        assert section in text
    assert "Your Majesty" in text
    assert "CONFLICT" in text
    assert "Biggest free block: 14:15–16:30" in text
    assert "Priya" in text and "Sofia" in text
    assert len(text.split()) < 450


def test_router_picks_the_right_agents():
    regina = Regina(mode="mock")
    assert regina.route("Any emails I need to reply to?") == ["email"]
    assert regina.route("Do I have any conflicts today?") == ["calendar"]
    assert regina.route("What did Anthropic ship this week?") == ["news"]
    assert regina.route("What's on my plate today?") == ["email", "calendar", "news"]
    assert regina.route("Marcus wants to move our 1:1 to 3pm, any email about it and is the slot free?") == ["email", "calendar"]


def test_chat_keeps_history_and_reset_clears_it():
    regina = Regina(mode="mock")
    answer = regina.ask("Do I have any conflicts today?")
    assert "Northwind" in answer and "Design review" in answer
    assert len(regina.history) == 2
    regina.reset()
    assert regina.history == []


def test_briefing_falls_back_to_mock_when_no_credentials(monkeypatch):
    monkeypatch.setenv("REGINA_MODE", "auto")
    monkeypatch.setattr("regina.config.has_credentials", lambda: False)
    regina = Regina()
    assert regina.mode == "mock"
    assert regina.roster["email"].backend == "mock"


def test_compose_briefing_handles_empty_reports():
    from regina.subagents.base import SubagentReply

    replies = {
        "email": SubagentReply("Email Agent", "b", "", {"matched": [], "quiet": []}),
        "calendar": SubagentReply("Calendar Agent", "b", "", {"events": [], "conflicts": [], "free": [], "deadlines": []}),
        "news": SubagentReply("Anthropic News Agent", "b", "", {"items": []}),
    }
    text = compose_briefing(replies)
    assert "A quiet day" in text
    assert "Nothing urgent." in text and "Nothing needs a reply." in text
