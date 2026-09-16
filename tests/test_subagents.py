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
    assert tiers["em-009"] == "C"  # newsletter: bulk sender is Tier C even when unread
    assert tiers["em-010"] == "C"


def test_email_brief_generated_at_is_real_utc():
    from datetime import datetime, timezone
    from regina import config

    email = build_roster()["email"]
    stamp = email.run("inbox digest").data["brief"]["generated_at"]
    parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert parsed == config.now().astimezone(timezone.utc).replace(microsecond=0)


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


def test_calendar_conflicts_question_mentioning_meetings_stays_conflicts_view():
    cal = build_roster()["calendar"]
    reply = cal.run("Any conflicts in my meetings today?")
    assert "conflicts" in reply.data and "events" not in reply.data


def test_calendar_next_meeting_is_after_pinned_now():
    cal = build_roster()["calendar"]
    reply = cal.run("what's my next meeting?")
    assert reply.data["next"]["id"] == "cal-003"  # 11:30 1:1, pinned now is 10:00


def test_news_digest_follows_the_skill_contract():
    news = build_roster()["news"]
    reply = news.run("Morning digest of AI news from the latest fetch, scored against the user's profile, as the news-agent JSON contract.")
    env = reply.data
    assert reply.text.startswith("```json") and reply.text.rstrip().endswith("```")  # JSON only, no prose
    assert set(env) == {"generated_at", "preset", "lookback_hours", "items_considered", "items_returned", "quiet_period", "items"}
    assert env["preset"] == "morning" and env["lookback_hours"] == 24
    assert env["items_returned"] == len(env["items"]) <= 3 + 4  # full_items + mention_items
    assert env["items_considered"] >= env["items_returned"]
    fixture_urls = {i["url"] for i in news.raw["items"]}
    for n, item in enumerate(env["items"]):
        assert set(item) == {"headline", "why_it_matters", "tier", "relevance", "score", "event_date", "entities", "sources"}
        assert item["tier"] in {"T1", "T2"} and item["relevance"] in {"direct", "adjacent", "domain"}
        assert item["score"] >= news.brief_cfg["length"]["min_score_floor"]
        assert item["entities"] and item["sources"] and all(s["url"] in fixture_urls for s in item["sources"])  # rule 1 & 2
        assert len(item["headline"].split()) <= news.brief_cfg["length"]["headline_max_words"]
        if n < 3:
            assert item["why_it_matters"] and len(item["why_it_matters"].split()) <= news.brief_cfg["length"]["why_it_matters_max_words"]
        else:
            assert item["why_it_matters"] is None  # mention-only
    scores = [i["score"] for i in env["items"]]
    assert scores == sorted(scores, reverse=True)  # by score, never grouped by tier
    assert news._words(env["items"]) <= news.brief_cfg["length"]["total_word_budget"]


def test_news_presets_query_and_quiet_period():
    news = build_roster()["news"]
    prep = news.run("Prep me for the meeting: latest AI news").data
    assert prep["preset"] == "meeting_prep" and prep["items_returned"] <= 2
    assert all(i["why_it_matters"] for i in prep["items"])
    slack = news.run("Anything for a Slack reply?").data
    assert slack["preset"] == "slack_reply" and slack["items_returned"] <= 3
    query = news.run("Everything about Anthropic from the last month").data
    assert query["preset"] == "query:Anthropic" and query["lookback_hours"] == 720
    assert query["items_returned"] >= 1
    assert all("anthropic" in " ".join([i["headline"], *i["entities"], *(s["url"] for s in i["sources"])]).lower() for i in query["items"])
    nothing = news.run("Everything about Zorbulon Dynamics").data
    assert nothing["items"] == [] and nothing["quiet_period"] is True  # rule 4: never pad


def test_news_pipeline_drops_t3_and_caps_per_entity():
    news = build_roster()["news"]
    items, reference = news.items_for(24)
    ranked = news._pipeline(items, reference)
    assert ranked and all(i["tier"] != "T3" for i in ranked)
    cap = news.brief_cfg["ordering"]["max_per_entity"]
    from collections import Counter
    assert max(Counter(i["primary_entity"] for i in ranked).values()) <= cap
    tiers = {i["title"]: i["tier"] for i in (news._tier(i) for i in items)}
    assert any(t == "T3" for t in tiers.values())  # the fixture has tooling/commentary items that must be dropped



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
    assert "## Output contract" in email.system_prompt()  # the skill body is in the prompt
    live = email.system_prompt(live_mcp=True)
    assert '"em-001"' not in live and "list-mail-folder-messages" in live
    attached = email.system_prompt(include_skill=False)  # Managed Agents: skill attached, not inlined
    assert "## Output contract" not in attached and '"em-001"' in attached
    assert "2026-09-16" in roster["calendar"].system_prompt()
    news = roster["news"].system_prompt()
    assert "## Hard Rules" in news and "profile.yaml" in news and '"publisher"' in news  # skill + config + fetch output
    assert "## Hard Rules" not in roster["news"].system_prompt(include_skill=False)
