from regina.subagents import build_roster


def test_email_inbox_brief_follows_the_skill_contract():
    email = build_roster()["email"]
    reply = email.run("inbox digest")
    brief = reply.data["brief"]
    assert reply.data["counts"] == {"total": 10, "unread": 6, "needs_reply": 4, "high_importance": 4}
    assert len(brief["summary_bullets"]) == 3
    assert [a["rank"] for a in brief["priority_actions"]] == [1, 2, 3]
    assert [a["source_email_id"] for a in brief["priority_actions"]] == ["em-003", "em-001", "em-007"]
    assert all(a["draft_id"] and a["draft_id"] == f"draft-{a['source_email_id']}" for a in brief["priority_actions"])
    assert {d["draft_id"] for d in brief["drafts_created"]} == {"draft-em-003", "draft-em-001", "draft-em-007"}
    assert brief["drafts_created"][1]["subject"].startswith("RE: Proposal")  # no "RE: Re:"
    assert brief["window"] == {"since_hours": 24, "includes_unresolved": True}
    assert any("nothing was sent" in n for n in brief["notes"])
    assert "```json" in reply.text and "Drafts created (not sent)" in reply.text


def test_email_triage_tiers_from_metadata():
    email = build_roster()["email"]
    tiers = {t["id"]: t["tier"] for t in email.triage()}
    assert tiers["em-001"] == "A"  # flagged
    assert tiers["em-007"] == "A"  # flagged, high, to-me
    assert tiers["em-002"] == "A"  # unread + focused + to-me + ask
    assert tiers["em-006"] == "C"  # cc-only notification, read
    assert tiers["em-009"] == "B"  # unread newsletter, cc
    assert tiers["em-010"] == "C"


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


def test_calendar_today_and_tomorrow_with_conflicts_mentioned_returns_both_days():
    cal = build_roster()["calendar"]
    reply = cal.run("What is on the user's calendar for today and tomorrow, flagging any conflicts?")
    ids = {e["id"] for e in reply.data["events"]}
    assert {"cal-001", "cal-004", "cal-008", "cal-009"} <= ids
    assert "CONFLICT" in reply.text


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


def test_system_prompt_embeds_fixture_or_points_at_mcp():
    roster = build_roster()
    email = roster["email"]
    assert '"em-001"' in email.system_prompt()
    assert "EXACTLY 3 bullets" in email.system_prompt()  # the skill is in the prompt
    live = email.system_prompt(live_mcp=True)
    assert '"em-001"' not in live and "list-mail-folder-messages" in live
    assert "2026-09-16" in roster["calendar"].system_prompt()
