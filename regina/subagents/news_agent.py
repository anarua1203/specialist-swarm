"""
News Agent — AI news specialist that follows the `news-agent` skill.

The skill (skills/news-agent/SKILL.md) defines the contract: fetch RSS items
(fetch.py), then Dedup -> Tier (drop T3) -> Relevance against profile.yaml ->
Score with brief.yaml weights -> Select by verbosity preset -> return
**structured JSON only**. The orchestrator writes the prose.

Backends:
- mock: the pipeline applied deterministically to the skill's saved fetch
  (skills/news-agent/fixtures/snapshot.json), or to a live fetch when
  REGINA_NEWS_LIVE=1 (runs the skill's own fetch.py; needs feedparser + network)
- llm:  Claude with the skill, profile, weights and the fetch output in its prompt
- Managed Agents: the same persona with the skill attached via the Skills API
  (see managed_agents/create_subagents.py)

Entry points from the skill, chosen from the brief:
- digest(preset)          "morning digest", "meeting prep", "for a slack reply"
- query(entity, lookback) "everything about OpenAI", "news mentioning Gemini"
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from .. import config
from .base import Subagent

SKILL_DIR = config.ROOT / "skills" / "news-agent"
FIXTURE_PATH = SKILL_DIR / "fixtures" / "snapshot.json"
FETCH_SCRIPT = SKILL_DIR / "fetch.py"

# Mock stand-ins for the judgement calls SKILL.md asks the model to make in
# step 2 (Tier). Publisher and keyword heuristics, tuned for the demo feeds.
T1_PATTERNS = re.compile(
    r"\b(launch(?:es|ed)?|releas(?:es|ed)|unveil(?:s|ed)?|introduc(?:es|ed)|rolls? out|"
    r"generally available|new (?:frontier )?models?|gpt-?\d|claude|gemini|llama|"
    r"regulation|regulat(?:es|ed|ors|ory)|ai act|new law|signed into law|lawmakers|ban(?:s|ned)?|executive order|"
    r"lawsuit|sues|acqui(?:res|red|sition)|merger|\$\d+(?:\.\d+)?\s?(?:b|bn|billion)|"
    r"outage|breach|safety incident|deepfakes?)\b",
    re.IGNORECASE,
)
T2_PATTERNS = re.compile(
    r"\b(papers?|research(?:ers)?|study|arxiv|preprint|open[- ]source|open[- ]weights?|"
    r"ceo|cto|chief|hires?|appoint(?:s|ed)|resign(?:s|ed)|steps? down|departs?|"
    r"hearing|policy|proposal|bill|senate|congress|committee|task force|framework|"
    r"guidelines|partnership|funding|raises|evaluators?|safety)\b",
    re.IGNORECASE,
)
RESEARCH_PUBLISHERS = ("arxiv",)  # preprints are T2 at most: 'we introduce' is not a product launch

# brief.yaml -> relevance.suppress, expressed as patterns the mock can check.
SUPPRESS_PATTERNS = [
    ("stock price movements", re.compile(r"\b(stock|shares?) (?:price )?(?:rose|rises|fell|falls|jump(?:s|ed)|drop(?:s|ped)|surg(?:es|ed)|plung(?:es|ed)|slid(?:es)?|ralli(?:es|ed)|tumbl(?:es|ed))\b|\bmarket cap\b", re.IGNORECASE)),
    ("executive drama with no policy or product consequence", re.compile(r"\b(feud|spat|drama|rift)\b", re.IGNORECASE)),
    ("benchmark disputes between vendors", re.compile(r"\bbenchmark\w*\b.*\b(disput|accus|contest|cheat|gam(?:ed|ing))", re.IGNORECASE)),
]
SMALL_FUNDING = re.compile(r"raises? \$?(\d+(?:\.\d+)?)\s?(?:m|mn|million)\b", re.IGNORECASE)

HEADLINE_SUFFIX = re.compile(r"\s+[-|–—]\s+[A-Z][\w .&']{1,40}$")  # "… - DevOps.com"
PROPER_NOUN_RUN = re.compile(r"\b([A-Z][A-Za-z0-9.&'-]+(?:\s+[A-Z][A-Za-z0-9.&'-]+)*)")
PROPER_NOUN_STOP = {"The", "A", "An", "How", "Why", "What", "When", "Where", "Who", "After", "New", "In", "On", "Is", "Are", "Will", "Can", "This", "These", "Its", "Your", "Our", "Political", "Look", "AI", "LLM", "LLMs"}

PRESET_HINTS = [
    ("meeting_prep", re.compile(r"\bmeeting[- ]prep\b|\bbefore (?:my|the) (?:meeting|call)\b|\bprep(?:are)? (?:me )?for\b", re.IGNORECASE)),
    ("slack_reply", re.compile(r"\bslack\b", re.IGNORECASE)),
]
QUERY_HINT = re.compile(r"\b(?:about|regarding|mentioning|concerning|related to|on the topic of) (.+?)(?:[.?!]|$| in the last| from the last| this week| this month| today)", re.IGNORECASE)


def _yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((SKILL_DIR / name).read_text())


def _skill_body() -> str:
    text = (SKILL_DIR / "SKILL.md").read_text()
    return text.split("---", 2)[2].strip() if text.startswith("---") else text.strip()


def _term_pattern(term: str) -> re.Pattern[str]:
    """Word-bounded match. Short all-caps tokens (AWS, RAG, FAIR) stay case-sensitive
    so 'fair' in prose does not count as Meta FAIR."""
    flags = 0 if (len(term) <= 4 and term.isupper()) else re.IGNORECASE
    return re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", flags)


def _expand(term: str) -> list[str]:
    """'Meta AI (FAIR)' -> ['Meta AI', 'FAIR']; 'AI ROI / cost optimization' -> both halves."""
    out: list[str] = []
    for part in re.split(r"\s*/\s*", term):
        m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", part)
        out += [m.group(1), m.group(2)] if m else [part]
    return [t.strip() for t in out if t.strip()]


class NewsAgent(Subagent):
    key = "news"
    name = "News Agent"
    tool_name = "ask_news_agent"
    fixture_file = "news-agent/fixtures/snapshot.json"  # informational; loaded from the skill dir
    description = (
        "AI news specialist following the news-agent skill: RSS items from lab "
        "blogs, arXiv, trade press and policy feeds, deduplicated, tiered (T1/T2, "
        "T3 dropped), graded for relevance against the user's profile and scored. "
        "Ask for a digest (presets: morning, meeting_prep, slack_reply) or for "
        "everything about an entity (query mode, 30-day lookback). Returns the "
        "skill's JSON contract only; you write the prose."
    )
    persona = (
        "You are the News Agent, the news-agent specialist sub-agent of Regina "
        "(team Prompt Queens). You know the latest RSS fetch and nothing else. "
        "Follow the news-agent skill exactly: dedup by event, tier and drop T3, "
        "grade relevance against the profile, score with the brief weights, "
        "select by preset, and return structured JSON only. Every item must "
        "trace to a fetched entry with its URL; never add items from background "
        "knowledge, never guess an event date, never pad to fill the preset."
    )

    def __init__(self, backend: str | None = None, client: Any = None) -> None:
        self.backend = (backend or config.SUBAGENT_BACKEND).lower()
        self._client = client
        self.profile = _yaml("profile.yaml")
        self.brief_cfg = _yaml("brief.yaml")
        self.live = os.environ.get("REGINA_NEWS_LIVE", "").strip() in {"1", "true", "yes"}
        self._live_cache: dict[int, list[dict[str, Any]]] = {}
        self.raw = {"source": "fixture", "items": json.loads(FIXTURE_PATH.read_text())}
        self._watchlist = self._build_watchlist()

    # ---- prompts ----------------------------------------------------------------

    def system_prompt(self, include_skill: bool = True) -> str:
        """Persona + the news-agent skill + profile/weights + the fetch output.
        Pass include_skill=False when the skill is attached via the Skills API
        (Managed Agents) so it is not duplicated in the prompt."""
        head = (
            f"{self.persona}\n\n"
            f"Today's date is {config.today().isoformat()} and the current time is "
            f"{config.now().strftime('%H:%M')}.\n\n"
            "You answer briefs from Regina, the orchestrator. Reply with the JSON "
            "output contract only: no preamble, no prose, no questions back.\n\n"
        )
        if include_skill:
            head += "# The news-agent skill\n\n" + _skill_body() + "\n\n"
        else:
            head += "# The news-agent skill\n\nAttached as a skill; load and follow it exactly.\n\n"
        head += (
            "# profile.yaml\n\n```yaml\n" + (SKILL_DIR / "profile.yaml").read_text().strip() + "\n```\n\n"
            "# brief.yaml\n\n```yaml\n" + (SKILL_DIR / "brief.yaml").read_text().strip() + "\n```\n\n"
            "# Your data (output of fetch.py --fixture, the latest fetch)\n\n"
            "Treat the JSON below as the result of step 1 of each entry point. "
            f"Recency is relative to the newest item's published_at.\n\n"
            f"```json\n{json.dumps(self.raw['items'], indent=2, ensure_ascii=False)}\n```"
        )
        return head

    # ---- data -------------------------------------------------------------------

    def items_for(self, lookback_hours: int) -> tuple[list[dict[str, Any]], datetime]:
        """Fetched items plus the reference 'now' used for recency and lookback.
        Fixture: the saved fetch is 'the latest fetch', so recency is measured from
        its newest item (deterministic, timezone-independent). Live: real UTC now."""
        if self.live:
            items = self._fetch_live(lookback_hours)
            return items, datetime.now(timezone.utc)
        items = list(self.raw["items"])
        stamps = [self._parse_dt(i["published_at"]) for i in items if i.get("published_at")]
        reference = max(stamps) if stamps else datetime.now(timezone.utc)
        cutoff = reference - timedelta(hours=lookback_hours)
        kept = [i for i in items if not i.get("published_at") or self._parse_dt(i["published_at"]) >= cutoff]
        return kept, reference

    def _fetch_live(self, lookback_hours: int) -> list[dict[str, Any]]:
        if lookback_hours not in self._live_cache:
            proc = subprocess.run(
                [sys.executable, str(FETCH_SCRIPT), "--lookback-hours", str(lookback_hours)],
                capture_output=True, text=True, timeout=90, check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                raise RuntimeError(f"fetch.py failed: {proc.stderr.strip()[:300]}")
            self._live_cache[lookback_hours] = json.loads(proc.stdout)
            self.raw = {"source": "live", "items": self._live_cache[lookback_hours]}
        return self._live_cache[lookback_hours]

    @staticmethod
    def _parse_dt(value: str) -> datetime:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    # ---- mock backend -------------------------------------------------------------

    def answer(self, brief: str) -> tuple[str, dict[str, Any]]:
        low = brief.lower()
        query = QUERY_HINT.search(brief)
        lookback = self._lookback(low, default=720 if query else 24)
        if query:
            envelope = self.query(query.group(1).strip(" '\""), lookback_hours=lookback)
        else:
            preset = next((name for name, pat in PRESET_HINTS if pat.search(brief)), "morning")
            top = re.search(r"\btop (\d+)\b", low)
            envelope = self.digest(preset=preset, lookback_hours=lookback, full_items=int(top.group(1)) if top else None)
        text = "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False) + "\n```"
        return text, envelope

    @staticmethod
    def _lookback(low: str, default: int) -> int:
        m = re.search(r"(\d+)\s*(hours?|days?|weeks?)\b", low)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            return n if unit.startswith("hour") else n * 24 if unit.startswith("day") else n * 24 * 7
        if "month" in low:
            return 720
        if "week" in low:
            return 168
        return default

    # ---- the skill's entry points ----------------------------------------------------

    def digest(self, preset: str = "morning", lookback_hours: int = 24, full_items: int | None = None) -> dict[str, Any]:
        length = dict(self.brief_cfg["length"])
        length.update(self.brief_cfg.get("verbosity_presets", {}).get(preset, {}))
        if full_items is not None:
            length["full_items"] = full_items
        items, reference = self.items_for(lookback_hours)
        ranked = self._pipeline(items, reference)
        selected = self._select(ranked, length)
        return self._envelope(selected, preset, lookback_hours, len(items), reference, quiet=len(selected) < length["full_items"])

    def query(self, entity: str, lookback_hours: int = 720) -> dict[str, Any]:
        items, reference = self.items_for(lookback_hours)
        pat = _term_pattern(entity)
        hits = [i for i in items if pat.search(f"{i['title']} {i.get('summary', '')} {i['url']}")]
        ranked = self._pipeline(hits, reference)  # no full/mention caps in query mode
        selected = [self._format(i, full=True, length=self.brief_cfg["length"]) for i in ranked]
        return self._envelope(selected, f"query:{entity}", lookback_hours, len(items), reference, quiet=not selected)

    # ---- pipeline: Dedup -> Tier -> Relevance -> Score ----------------------------------

    def _pipeline(self, items: list[dict[str, Any]], reference: datetime) -> list[dict[str, Any]]:
        deduped = self._dedup(items)
        scored = []
        for i in deduped:
            i = self._tier(i)
            if i["tier"] == "T3":
                continue
            i = self._relevance(i)
            if i.get("suppressed"):
                continue
            i["recency_bonus"] = self._recency(i, reference)
            i["score"] = self.brief_cfg["ordering"]["tier_weight"][i["tier"]] + self.brief_cfg["relevance"][i["relevance"]] + i["recency_bonus"]
            scored.append(i)
        # Order by score descending (never grouped by tier); newest first on ties.
        scored.sort(key=lambda i: (-i["score"], i["published_at"] or ""), reverse=False)
        scored.sort(key=lambda i: i["published_at"] or "", reverse=True)
        scored.sort(key=lambda i: -i["score"])
        # ordering.max_per_entity: keep the top-scoring items per primary entity.
        cap = self.brief_cfg["ordering"]["max_per_entity"]
        seen: dict[str, int] = {}
        kept = []
        for i in scored:
            seen[i["primary_entity"]] = seen.get(i["primary_entity"], 0) + 1
            if seen[i["primary_entity"]] <= cap:
                kept.append(i)
        floor = self.brief_cfg["length"]["min_score_floor"]
        return [i for i in kept if i["score"] >= floor and i["url"]]  # hard rule 1: no URL, no item

    def _dedup(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Same event ~ same normalised title (the mock's proxy for event clustering).
        Keep the earliest / most primary; attach the others as extra sources."""
        primary_rank = {"lab": 0, "wire": 1, "trade": 2, "aggregator": 3}
        clusters: dict[str, dict[str, Any]] = {}
        for raw in items:
            i = {**raw, "sources": [{"url": raw["url"], "publisher": raw["publisher"]}]}
            key = re.sub(r"[^a-z0-9 ]", "", HEADLINE_SUFFIX.sub("", i["title"]).lower())
            key = " ".join(key.split()[:8])
            current = clusters.get(key)
            if current is None:
                clusters[key] = i
                continue
            a, b = self._publisher_kind(current["publisher"]), self._publisher_kind(i["publisher"])
            if (primary_rank[b], i["published_at"] or "") < (primary_rank[a], current["published_at"] or ""):
                i["sources"] += current["sources"]
                clusters[key] = i
            else:
                current["sources"] += i["sources"]
        return list(clusters.values())

    @staticmethod
    def _publisher_kind(publisher: str) -> str:
        p = publisher.lower()
        if "blog" in p or "anthropic" in p or "openai" in p or "arxiv" in p:
            return "lab"
        if "google news" in p or "aggregat" in p:
            return "aggregator"
        if "reuters" in p or "ap " in p or "bloomberg" in p:
            return "wire"
        return "trade"

    def _tier(self, i: dict[str, Any]) -> dict[str, Any]:
        text = f"{i['title']} {i.get('summary', '')}"
        research = any(p in i["publisher"].lower() for p in RESEARCH_PUBLISHERS)
        if T1_PATTERNS.search(text) and not research:
            tier = "T1"
        elif research or T2_PATTERNS.search(text):
            tier = "T2"
        else:
            tier = "T3"
        return {**i, "tier": tier}

    def _build_watchlist(self) -> list[tuple[str, re.Pattern[str], str]]:
        """(name, pattern, kind) for every profile entity the skill grades 'direct'."""
        out: list[tuple[str, re.Pattern[str], str]] = []
        if self.profile.get("employer"):
            for t in _expand(self.profile["employer"]):
                out.append((t, _term_pattern(t), "employer"))
        for kind, names in (self.profile.get("watchlist") or {}).items():
            for name in names or []:
                for t in _expand(name):
                    out.append((t, _term_pattern(t), kind))
        return out

    def _relevance(self, i: dict[str, Any]) -> dict[str, Any]:
        text = f"{i['title']} {i.get('summary', '')}"
        if SMALL_FUNDING.search(text) and float(SMALL_FUNDING.search(text).group(1)) < 50 and not T1_PATTERNS.search(i["title"]):
            return {**i, "suppressed": "funding rounds under $50M"}
        for reason, pat in SUPPRESS_PATTERNS:
            if pat.search(text) and i["tier"] != "T1":
                return {**i, "suppressed": reason}
        direct = [name for name, pat, kind in self._watchlist if kind != "topics" and pat.search(text)]
        topics = [name for name, pat, kind in self._watchlist if kind == "topics" and pat.search(text)]
        stack = [t for t in self.profile.get("tech_stack", []) for x in _expand(t) if _term_pattern(x).search(text)]
        industry = [t for t in _expand(self.profile.get("industry", "")) if _term_pattern(t.split()[-1]).search(text)]
        if direct or topics:
            grade, hit = "direct", (direct or topics)[0]
        elif stack or industry:
            grade, hit = "adjacent", (stack or industry)[0]
        else:
            grade, hit = "domain", ""
        entities = list(dict.fromkeys(direct + self._proper_nouns(i["title"])))[:5] or [i["publisher"]]
        # ordering.max_per_entity keys on the most central named entity; items with no
        # watchlist entity are capped per publisher instead so one feed cannot flood.
        primary = direct[0] if direct else i["publisher"]
        return {**i, "relevance": grade, "relevance_hit": hit, "entities": entities, "primary_entity": primary}

    @staticmethod
    def _proper_nouns(title: str) -> list[str]:
        """Capitalised runs from the title. A sentence-initial word only counts when
        it is an acronym (EU, SUPERWISE) or starts a multi-word name."""
        clean = HEADLINE_SUFFIX.sub("", title)
        out: list[str] = []
        for m in PROPER_NOUN_RUN.finditer(clean):
            tokens = [t.removesuffix("'s").removesuffix("\u2019s") for t in m.group(1).split() if t not in PROPER_NOUN_STOP]
            if m.start() == 0 and tokens and not tokens[0].isupper() and len(tokens) == 1:
                continue
            run = " ".join(tokens)
            if run and (len(run) > 2 or run.isupper()):
                out.append(run)
        return out[:3]

    def _recency(self, i: dict[str, Any], reference: datetime) -> int:
        if not i.get("published_at"):
            return 0
        age = reference - self._parse_dt(i["published_at"])
        bonus = self.brief_cfg["ordering"]["recency_bonus"]
        if age < timedelta(hours=6):
            return bonus["under_6h"]
        if age < timedelta(hours=24):
            return bonus["under_24h"]
        return 0

    # ---- Select -> Format ---------------------------------------------------------------

    def _select(self, ranked: list[dict[str, Any]], length: dict[str, Any]) -> list[dict[str, Any]]:
        full = [self._format(i, full=True, length=length) for i in ranked[: length["full_items"]]]
        mention = [self._format(i, full=False, length=length) for i in ranked[length["full_items"]: length["full_items"] + length["mention_items"]]]
        # Hard rule 5: trim mention items from the bottom until the word budget fits.
        while mention and self._words(full + mention) > length["total_word_budget"]:
            mention.pop()
        return full + mention

    @staticmethod
    def _words(items: list[dict[str, Any]]) -> int:
        return sum(len(i["headline"].split()) + len((i["why_it_matters"] or "").split()) for i in items)

    def _format(self, i: dict[str, Any], full: bool, length: dict[str, Any]) -> dict[str, Any]:
        headline = self._headline(i["title"], length["headline_max_words"])
        return {
            "headline": headline,
            "why_it_matters": self._why(i, length["why_it_matters_max_words"]) if full else None,
            "tier": i["tier"],
            "relevance": i["relevance"],
            "score": i["score"],
            "event_date": i["published_at"],  # hard rule 3: never guess; publication date is acceptable
            "entities": i["entities"],
            "sources": i["sources"],
        }

    @staticmethod
    def _headline(title: str, max_words: int) -> str:
        """The mock keeps the source title (the llm backend rewrites it); when it must
        be shortened, cut at the last clause break rather than mid-phrase."""
        words = HEADLINE_SUFFIX.sub("", title).strip().split()
        if len(words) <= max_words:
            return " ".join(words)
        kept = words[:max_words]
        for n in range(len(kept) - 1, max(5, len(kept) // 2), -1):
            if kept[n - 1].endswith((":", ",", ";", "—", "–", "?", ".", "\"", "\u201d")):
                kept = kept[:n]
                break
        return " ".join(kept).rstrip(",;:—–")

    def _why(self, i: dict[str, Any], max_words: int) -> str:
        role, employer, industry = self.profile.get("role", "your"), self.profile.get("employer", "your company"), self.profile.get("industry", "your field")
        if i["relevance"] == "direct":
            text = f"Names {i['relevance_hit']} from your watchlist; directly relevant to your {role} work at {employer}."
        elif i["relevance"] == "adjacent":
            text = f"Touches {i['relevance_hit']} in your stack; worth knowing for {industry} engagements."
        else:
            text = f"General AI/ML development ({i['tier']}); background context for your {industry} work."
        return " ".join(text.split()[:max_words])

    def _envelope(self, items: list[dict[str, Any]], preset: str, lookback_hours: int, considered: int, reference: datetime, quiet: bool) -> dict[str, Any]:
        return {
            "generated_at": reference.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "preset": preset,
            "lookback_hours": lookback_hours,
            "items_considered": considered,
            "items_returned": len(items),
            "quiet_period": quiet,  # hard rule 4: fewer items is correct, never pad
            "items": items,
        }
