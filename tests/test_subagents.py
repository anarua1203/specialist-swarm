from regina.subagents import build_roster


def test_email_digest_counts_and_drafts():
    email = build_roster()["email"]
    reply = email.run("inbox digest")
    assert reply.data["counts"]["total"] == 10
    assert reply.data["counts"]["needs_reply"] == 4
    assert "Priya Natarajan" in reply.text
    assert "Draft reply" in reply.text


def test_email_filters_by_sender_and_reply():
    email = build_roster()["email"]
    assert [e["id"] for e in email.run("emails from Marcus Lee").data["matched"]] == ["em-002"]
    needs = email.run("what needs a reply?").data["matched"]
    assert {e["id"] for e in needs} == {"em-001", "em-002", "em-003", "em-007"}
    assert email.run("emails from Nobody Here").data["matched"] == []


def test_calendar_today_flags_conflict_and_free_block():
    cal = build_roster()["calendar"]
    reply = cal.run("today's full schedule with conflicts flagged, largest free block, and deadlines in the next 2 days")
    assert reply.data["offset"] == 0
    assert len(reply.data["conflicts"]) == 1
    a, b = reply.data["conflicts"][0]
    assert {a["id"], b["id"]} == {"cal-004", "cal-005"}
    assert ("14:15", "16:30") in reply.data["free"]
    assert [d["id"] for d in reply.data["deadlines"]] == ["cal-010", "cal-012"]
    assert "CONFLICT" in reply.text


def test_calendar_tomorrow_and_week():
    cal = build_roster()["calendar"]
    tomorrow = cal.run("what's on my schedule tomorrow?")
    assert {e["id"] for e in tomorrow.data["events"]} == {"cal-008", "cal-009"}
    week = cal.run("show me the week ahead")
    assert len(week.data["events"]) == 12


def test_calendar_next_meeting_is_after_pinned_now():
    cal = build_roster()["calendar"]
    reply = cal.run("what's my next meeting?")
    assert reply.data["next"]["id"] == "cal-003"  # 11:30 1:1, pinned now is 10:00


def test_news_last_week_and_topic():
    news = build_roster()["news"]
    week = news.run("Top 3 Anthropic announcements from the last 7 days, each with a one-line 'why it matters' for someone building a multi-agent assistant.")
    assert [i["id"] for i in week.data["items"]] == ["news-001", "news-002", "news-003"]
    topical = news.run("What did Anthropic ship about agents?")
    assert {i["id"] for i in topical.data["items"]} == {"news-002"}
    month = news.run("everything from the last 30 days")
    assert len(month.data["items"]) == 5  # default limit
    assert news.run("anything in the last 0 days?").data["items"] == []


def test_tool_definitions_are_strict_and_unique():
    roster = build_roster()
    tools = [a.tool_definition() for a in roster.values()]
    assert {t["name"] for t in tools} == {"ask_email_agent", "ask_calendar_agent", "ask_news_agent"}
    for t in tools:
        assert t["strict"] is True
        assert t["input_schema"]["required"] == ["brief"]


def test_system_prompt_embeds_fixture():
    roster = build_roster()
    assert '"em-001"' in roster["email"].system_prompt()
    assert "2026-09-16" in roster["calendar"].system_prompt()
