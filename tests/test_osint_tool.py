import json

import pytest

from tools import osint_tool as ot


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ot, "_cache", ot.DiskCache("osint", 3600, root=tmp_path))
    monkeypatch.setattr(ot, "_reddit_blocked", False)


# ── Funcții pure ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, short", [
    ("Chegg, Inc.", "Chegg"), ("Alphabet Inc", "Alphabet"), ("Shell Plc ADR", "Shell"),
    ("Meta Platforms Inc", "Meta Platforms"), ("Toronto Dominion Bank", "Toronto Dominion Bank"),
    ("Alibaba Group Holding Ltd ADR", "Alibaba"),
])
def test_short_name(raw, short):
    assert ot.short_name(raw) == short


@pytest.mark.parametrize("text, expected", [
    ("Chegg to lay off 22% of workforce", {"distress"}),
    ("Customers are leaving for a free open-source alternative", {"customer_churn", "open_source_clone"}),
    ("SEC opens investigation; class action lawsuit filed", {"regulator", "litigation"}),
    ("Chegg is on its last legs after ChatGPT", {"distress", "ai_disruption"}),
    ("Tariffs and the end of EV tax credits hit margins", {"policy_subsidy"}),
    ("Chinese rival undercuts prices", {"china_competition"}),
    ("The product works fine, I'm happy", set()),
])
def test_tag_text(text, expected):
    assert set(ot.tag_text(text)) == expected


def test_is_relevant():
    hit = ot._hit("ddg_news", "MakeMyTrip short seller report", "https://x.com/a")
    assert not ot.is_relevant(hit, "Chegg")
    assert ot.is_relevant(ot._hit("hackernews", "Chegg layoffs", "https://x.com/b"), "Chegg")
    assert ot.is_relevant(ot._hit("hackernews", "Show HN: open homework solver", "u", snippet="homework help"),
                          "Chegg", "homework help")
    assert ot.is_relevant(ot._hit("github", "someone/repo", "https://github.com/x"), "Chegg")


def test_rank_orders_by_risk_then_signal_and_dedupes():
    hits = [
        ot._hit("hackernews", "Nice product launch", "u1", signal=500),
        ot._hit("hackernews", "Judge dismisses Chegg antitrust lawsuit over Google AI", "u2", signal=5),
        ot._hit("ddg_news", "Judge dismisses Chegg antitrust lawsuit over Google AI Overviews", "u3"),
        ot._hit("ddg_news", "Chegg layoffs", "u2"),  # același URL
        ot._hit("hackernews", "Chegg to lay off staff", "u4", signal=100),
    ]
    ranked = ot.rank(hits)
    urls = [h["url"] for h in ranked]
    assert urls == ["u2", "u4", "u1"]  # u3 = aceeași știre ca u2; u2 duplicat eliminat


def test_threat_summary_counts():
    hits = [ot._hit("x", "lawsuit and layoffs", "a"), ot._hit("x", "another lawsuit", "b")]
    assert ot.threat_summary(hits) == {"litigation": 2, "distress": 1}


def test_build_queries_without_and_with_product():
    q = ot.build_queries("Chegg", "")
    assert q["github"] == [] and len(q["ddg_news"]) == 3
    assert all('"' not in s and " OR " not in s for s in q["ddg_news"] + q["ddg_text"])
    q = ot.build_queries("Chegg", "homework help")
    assert q["github"] == ["homework help"] and "homework help open source" in q["hn"]


# ── Tool-ul cu surse simulate ────────────────────────────────────────────────

def _mock_sources(monkeypatch, reddit_status=200, fail=()):
    calls = {"github": 0, "reddit": 0}

    def ddg(kind, q, max_results=5):
        if "ddg" in fail:
            raise RuntimeError("down")
        src = "reddit(ddg)" if "site:reddit.com" in q else f"ddg_{kind}"
        return [ot._hit(src, f"Chegg {kind} lawsuit {q}", f"https://ddg/{kind}/{q}")]

    def hn(q):
        if "hn" in fail:
            raise RuntimeError("down")
        return [ot._hit("hackernews", f"Chegg is on its last legs {q}", f"https://hn/{q}", signal=108)]

    def github(product):
        calls["github"] += 1
        return [ot._hit("github", "acme/free-homework", "https://github.com/acme", signal=5000,
                        snippet="homework help", extra_tags=["open_source_clone"])]

    def reddit(q):
        calls["reddit"] += 1
        if reddit_status in (403, 429):
            ot._reddit_blocked = True
            return []
        return [ot._hit("reddit", "Leaving Chegg for ChatGPT", "https://reddit/1", signal=300)]

    monkeypatch.setattr(ot, "_ddg", ddg)
    monkeypatch.setattr(ot, "_hn", hn)
    monkeypatch.setattr(ot, "_github", github)
    monkeypatch.setattr(ot, "_reddit_json", reddit)
    return calls


def test_tool_all_sources_ok(monkeypatch):
    calls = _mock_sources(monkeypatch)
    out = json.loads(ot.paranoid_osint_radar("Chegg, Inc.", "chgg", "Education", product="homework help"))
    assert out["ok"] and out["ticker"] == "CHGG" and out["company"] == "Chegg"
    assert out["sources_status"] == {"ddg_news": "ok", "ddg_web": "ok", "hackernews": "ok",
                                     "github": "ok", "reddit": "ok"}
    assert out["threat_summary"]["litigation"] >= 1
    assert len(out["top_threats"]) <= ot.TOP_THREATS
    assert calls["github"] == 1


def test_tool_reddit_blocked_uses_ddg_fallback_once(monkeypatch):
    calls = _mock_sources(monkeypatch, reddit_status=403)
    out = json.loads(ot.paranoid_osint_radar("Chegg", "CHGG", "Education"))
    assert out["sources_status"]["reddit"].startswith("blocked")
    assert out["sources_status"]["reddit(ddg)"] == "ok"
    assert out["sources_status"]["github"].startswith("skipped")
    ot.paranoid_osint_radar("Chegg", "CHGG", "Education")
    assert calls["reddit"] == 1  # după blocare nu mai lovim Reddit


def test_tool_survives_failing_sources(monkeypatch):
    _mock_sources(monkeypatch, fail=("ddg",))
    out = json.loads(ot.paranoid_osint_radar("Chegg", "CHGG", "Education"))
    assert out["ok"]
    assert out["sources_status"]["ddg_news"].startswith("error")
    assert out["sources_status"]["hackernews"] == "ok"


def test_tool_no_coverage_hint(monkeypatch):
    _mock_sources(monkeypatch, reddit_status=403, fail=("ddg", "hn"))
    out = json.loads(ot.paranoid_osint_radar("Chegg", "CHGG", "Education"))
    assert out["coverage"] == "none" and "incomplet" in out["hint"]


def test_tool_extra_queries_capped(monkeypatch):
    _mock_sources(monkeypatch)
    out = json.loads(ot.paranoid_osint_radar("Chegg", "CHGG", "Education",
                                             extra_queries=["Chegg q1", "Chegg q2", "Chegg q3", "Chegg q4", ""]))
    titles = " ".join(h["title"] for h in out["top_threats"])
    assert "q4" not in titles


def test_tool_requires_company_name():
    out = json.loads(ot.paranoid_osint_radar("  ", "X", "Y"))
    assert out["ok"] is False


class _FakeResponse:
    def __init__(self, status, headers=None, items=()):
        self.status_code, self.headers, self._items = status, headers or {}, list(items)

    def raise_for_status(self):
        pass

    def json(self):
        return {"items": self._items}


def _github_quota(monkeypatch, responses):
    sleeps, queue = [], list(responses)
    monkeypatch.setattr(ot.time, "sleep", sleeps.append)
    monkeypatch.setattr(ot.rate_limiter, "wait", lambda source: None)
    monkeypatch.setattr(ot.requests, "get", lambda *a, **kw: queue.pop(0))
    return sleeps, queue


def test_github_quota_waits_until_reset_then_retries(monkeypatch):
    reset = str(int(ot.time.time()) + 20)
    repo = {"full_name": "x/clone", "html_url": "https://github.com/x/clone", "stargazers_count": 900}
    sleeps, queue = _github_quota(monkeypatch, [
        _FakeResponse(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}),
        _FakeResponse(200, items=[repo])])
    hits = ot._github_search("photo editor")
    assert [h["title"] for h in hits] == ["x/clone"] and not queue
    assert len(sleeps) == 1 and 15 <= sleeps[0] <= 22


def test_github_quota_far_reset_gives_up_without_retry_storm(monkeypatch):
    reset = str(int(ot.time.time()) + 3600)
    sleeps, queue = _github_quota(monkeypatch, [
        _FakeResponse(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}),
        _FakeResponse(200)])
    with pytest.raises(ot._GitHubQuotaExhausted):
        ot._github_search("photo editor")
    assert not sleeps and len(queue) == 1  # o singură cerere, fără reîncercări


# ── Live ─────────────────────────────────────────────────────────────────────

@pytest.mark.live
def test_live_chegg_radar_finds_threats():
    out = json.loads(ot.paranoid_osint_radar("Chegg, Inc.", "CHGG", "Education & Training Services",
                                             product="homework help"))
    assert out["ok"], out
    ok_sources = [s for s, v in out["sources_status"].items() if v == "ok"]
    assert len(ok_sources) >= 2, out["sources_status"]
    assert out["threat_summary"].get("ai_disruption", 0) >= 1
    assert all("chegg" in f"{h['title']} {h['snippet']} {h['url']}".lower() or h["source"] == "github"
               or "homework" in f"{h['title']} {h['snippet']}".lower() for h in out["top_threats"])
