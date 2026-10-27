#!/usr/bin/env python3
"""Offline tests for the collector's parsers, merge and digest logic (no network). Run: python3 test_collect.py"""
import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as C  # noqa: E402

CFG = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.json"), encoding="utf-8"))
SINCE = C.NOW - dt.timedelta(days=7)
RECENT = C.iso(C.NOW - dt.timedelta(hours=5))

RSS = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Claude Status - Incident History</title>
<item><title>Elevated errors for Claude Fable 5.1</title><description>&lt;p&gt;Resolved - fixed.&lt;/p&gt;</description>
<pubDate>{C.NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate><link>https://status.claude.com/incidents/abc</link><guid>https://status.claude.com/incidents/abc</guid></item>
<item><title>Old incident</title><description>x</description><pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate><link>https://status.claude.com/incidents/old</link></item>
</channel></rss>"""

ATOM = f"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Simon Willison</title>
<entry><title>Notes on the new Claude release</title><link rel="alternate" href="https://simonwillison.net/2026/Oct/26/claude/"/>
<id>tag:simonwillison.net,2026:claude</id><published>{RECENT}</published><content type="html">&lt;p&gt;Anthropic shipped something.&lt;/p&gt;</content></entry>
<entry><title>Unrelated post about SQLite</title><link rel="alternate" href="https://simonwillison.net/2026/Oct/25/sqlite/"/><id>x2</id><published>{RECENT}</published><summary>db stuff</summary></entry>
</feed>"""

X_TIMELINE = {"data": [
    {"id": "1001", "text": "Shipping something new for Claude Code next week.", "created_at": RECENT, "author_id": "u1",
     "public_metrics": {"like_count": 120, "retweet_count": 30, "reply_count": 8, "quote_count": 4, "impression_count": 50000}},
    {"id": "1002", "text": "RT @someone: Anthropic is about to…", "created_at": RECENT, "author_id": "u1",
     "referenced_tweets": [{"type": "retweeted", "id": "999"}], "public_metrics": {"like_count": 0, "retweet_count": 0, "reply_count": 0, "quote_count": 0}}],
    "includes": {"users": [{"id": "u9", "username": "someone", "public_metrics": {"followers_count": 10}}]},
    "meta": {"newest_id": "1002", "result_count": 2}}

REDDIT = {"data": {"children": [
    {"kind": "t3", "data": {"name": "t3_abc", "title": "Claude got a new model picker?", "selftext": "Saw a Fable 5.2 option for a second", "score": 44,
                            "upvote_ratio": 0.9, "num_comments": 12, "created_utc": (C.NOW - dt.timedelta(hours=3)).timestamp(), "permalink": "/r/ClaudeAI/comments/abc/x/",
                            "author": "someone", "subreddit": "ClaudeAI", "is_self": True, "url": "https://www.reddit.com/r/ClaudeAI/comments/abc/x/"}},
    {"kind": "t3", "data": {"name": "t3_low", "title": "meh", "selftext": "", "score": 1, "num_comments": 0, "created_utc": C.NOW.timestamp(), "permalink": "/r/ClaudeAI/comments/low/",
                            "author": "a", "subreddit": "ClaudeAI", "is_self": True}}]}}

POLY = {"events": [{"title": "Anthropic IPO by __?", "slug": "anthropic-ipo-by", "markets": [
    {"id": "6066698", "question": "Will Anthropic IPO by November 20, 2026?", "slug": "will-anthropic-ipo-by-november-20-2026", "closed": False,
     "outcomePrices": "[\"0.675\", \"0.325\"]", "outcomes": "[\"Yes\", \"No\"]", "volume24hr": 25160.39, "liquidityNum": 6496.8, "volumeNum": 45419.3,
     "createdAt": "2026-10-22T22:06:45.570207Z", "endDate": "2026-11-21T04:59:00Z", "oneDayPriceChange": 0.45, "oneWeekPriceChange": None},
    {"id": "1", "question": "Will Anthropic IPO by June 30, 2026?", "slug": "closed-one", "closed": True, "outcomePrices": "[\"0\", \"1\"]"}]},
    {"title": "Something about Xi", "slug": "xi", "markets": [{"id": "2", "question": "Xi out?", "slug": "xi-out", "closed": False, "outcomePrices": "[\"0.03\", \"0.97\"]"}]}]}

MANIFOLD_SEARCH = [{"id": "nts6Ry9sgZ", "question": "Will Anthropic IPO by….?", "url": "https://manifold.markets/Jack1/will-anthropic-ipo-by", "outcomeType": "MULTIPLE_CHOICE",
                    "createdTime": 1780432735808, "closeTime": 1798675140000, "volume24Hours": 7816.4, "volume": 37299.9, "uniqueBettorCount": 61}]
MANIFOLD_FULL = {"answers": [{"text": "Nov 2026", "probability": 0.55}, {"text": "Dec 2026", "probability": 0.25}, {"text": "2027", "probability": 0.2}]}

HN = {"hits": [{"objectID": "50244547", "title": "Why Anthropic can't afford to delay its IPO", "url": "https://www.axios.com/2026/10/21/anthropic-ipo-ai-costs",
                "author": "toomuchtodo", "points": 4, "num_comments": 0, "created_at": RECENT}]}

BSKY = {"posts": [{"uri": "at://did:plc:abc/app.bsky.feed.post/3k", "author": {"handle": "jackclark.bsky.social"},
                   "record": {"text": "Import AI this week covers Anthropic", "createdAt": RECENT}, "likeCount": 10, "repostCount": 2, "replyCount": 1, "quoteCount": 0,
                   "embed": {"external": {"uri": "https://importai.substack.com/p/import-ai-999"}}}]}

GOOGLE_ALERTS = f"""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:idx="urn:atom-extension:indexing"><id>tag:google.com,2013:googlealerts/feed:1</id>
<title>Google Alert - anthropic site:reddit.com</title><updated>{RECENT}</updated>
<entry><id>tag:google.com,2013:googlealerts/feed:2</id><title type="html">Did &lt;b&gt;Claude&lt;/b&gt; just get a new model picker? : r/ClaudeAI</title>
<link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://www.reddit.com/r/ClaudeAI/comments/abc1/model_picker/&amp;ct=ga&amp;cd=CAIyGjA&amp;usg=AOvVaw0"/>
<published>{RECENT}</published><updated>{RECENT}</updated><content type="html">Saw a &lt;b&gt;Fable&lt;/b&gt; 5.2 option for a second this morning ...</content></entry>
<entry><id>tag:google.com,2013:googlealerts/feed:3</id><title type="html">Anthropic IPO thread</title>
<link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://www.example.com/not-reddit&amp;ct=ga"/><published>{RECENT}</published><content type="html">x</content></entry>
</feed>"""

BRAVE = {"web": {"results": [
    {"title": "Claude Code 2.1.310 broke my hooks : r/ClaudeCode", "url": "https://www.reddit.com/r/ClaudeCode/comments/zzz1/broke_my_hooks/", "description": "Anyone else seeing this after the update?", "page_age": RECENT},
    {"title": "Old thread", "url": "https://www.reddit.com/r/ClaudeAI/comments/old/", "description": "ancient", "page_age": "2024-01-01T00:00:00"},
    {"title": "Undated result", "url": "https://www.reddit.com/r/Anthropic/comments/nodate/", "description": "no page_age field"}]}}

NEWS_HTML = """<html><body><a href="/news">News</a><a href="/news/public-benefit-fund">Anthropic commits 5% of its shares to a new Public Benefit Fund</a>
<a href="/news/public-benefit-fund">dup</a><a href="/research/formal-verification">Formal verification</a><a href="/news/x?utm=1">Short</a></body></html>"""


def fake_get_json(table):
    def _get(url, headers=None):
        for key, val in table.items():
            if key in url:
                return val
        raise RuntimeError(f"unexpected URL {url}")
    return _get


class ParserTests(unittest.TestCase):
    def test_parse_rss_and_atom(self):
        rss = C.parse_feed(RSS)
        self.assertEqual(len(rss), 2)
        self.assertEqual(rss[0]["link"], "https://status.claude.com/incidents/abc")
        atom = C.parse_feed(ATOM)
        self.assertEqual(atom[0]["link"], "https://simonwillison.net/2026/Oct/26/claude/")
        self.assertIn("Anthropic shipped", atom[0]["summary"])

    def test_collect_rss_filters_and_windows(self):
        cfg = dict(CFG, rss=[{"name": "Status", "url": "https://status.example/history.rss", "platform": "status", "tier": "A", "filter": False, "kind": "incident"},
                             {"name": "Simon", "url": "https://blog.example/atom", "platform": "blog", "tier": "B", "filter": True, "kind": "article"}])
        cov = C.Coverage()
        with mock.patch.object(C, "http", side_effect=lambda url, **k: (200, RSS if "status" in url else ATOM, {})):
            items = C.collect_rss(cfg, cov, SINCE)
        titles = sorted(i["title"] for i in items)
        self.assertEqual(titles, ["Elevated errors for Claude Fable 5.1", "Notes on the new Claude release"])  # old incident and SQLite post dropped
        self.assertEqual(cov.rows["feeds"]["status"], "ok")

    def test_google_alerts_feed_unwraps_links_and_labels_reddit(self):
        cfg = dict(CFG, rss=[{"name": "GA", "url": "https://www.google.com/alerts/feeds/1/2", "platform": "reddit", "tier": "D", "filter": False, "kind": "post", "mixed_hosts": True},
                             {"name": "placeholder", "url": "PASTE_ME", "platform": "reddit", "enabled": False}])
        cov = C.Coverage()
        with mock.patch.object(C, "http", return_value=(200, GOOGLE_ALERTS, {})):
            items = C.collect_rss(cfg, cov, SINCE)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "https://www.reddit.com/r/ClaudeAI/comments/abc1/model_picker/")
        self.assertEqual(items[0]["title"], "Did Claude just get a new model picker? : r/ClaudeAI")   # <b> tags stripped
        self.assertEqual(items[0]["platform"], "reddit")
        self.assertEqual(items[1]["platform"], "reddit")        # non-reddit link keeps the feed's configured platform
        self.assertEqual(cov.rows["feeds"]["note"], "1 feeds OK")

    def test_search_collector(self):
        cov = C.Coverage()
        self.assertEqual(C.collect_search(CFG, cov, (None, None, None), SINCE), [])
        self.assertEqual(cov.rows["search"]["status"], "skipped")
        with mock.patch.object(C, "get_json", return_value=BRAVE):
            items = C.collect_search(CFG, cov, ("key", None, None), SINCE)
        urls = sorted(i["url"] for i in items)
        self.assertEqual(urls, ["https://www.reddit.com/r/Anthropic/comments/nodate/", "https://www.reddit.com/r/ClaudeCode/comments/zzz1/broke_my_hooks/"])  # old one dropped
        self.assertTrue(all(i["platform"] == "reddit" for i in items))
        self.assertIn("undated", next(i for i in items if "nodate" in i["url"])["tags"])
        self.assertEqual(cov.rows["search"]["status"], "ok")

    def test_x_items(self):
        got = C.x_items_from_response(X_TIMELINE, default_author="bcherny", tier="A", always=True)
        self.assertEqual(got[0]["url"], "https://x.com/bcherny/status/1001")
        self.assertEqual(got[0]["metrics"]["likes"], 120)
        self.assertEqual(got[1]["kind"], "repost")
        self.assertEqual(got[1]["cites"], ["https://x.com/i/status/999"])

    def test_collect_x_uses_since_ids_and_counts_reads(self):
        cfg = dict(CFG, x=dict(CFG["x"], accounts=[{"handle": "bcherny", "tier": "A", "always": True}], searches=["Anthropic"], search_hours_utc=[]))
        state, cov = {"x_user_ids": {"bcherny": {"id": "u1", "followers": 50000}}, "x_since_ids": {"bcherny": "900"}}, C.Coverage()
        calls = []

        def _get(url, headers=None):
            calls.append(url)
            return X_TIMELINE
        with mock.patch.object(C, "get_json", side_effect=_get):
            items = C.collect_x(cfg, state, cov, "tok", SINCE)
        self.assertIn("since_id=900", calls[0])
        self.assertEqual(state["x_since_ids"]["bcherny"], "1002")
        self.assertEqual(state["x_reads_last_run"], 4)       # 2 timeline + 2 search
        self.assertEqual(len(items), 4)
        self.assertEqual(cov.rows["x"]["status"], "ok")

    def test_reddit_posts(self):
        got = C.reddit_posts(REDDIT, "ClaudeAI", "D", C.re.compile(CFG["keywords"], C.re.I), False, 5)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["metrics"]["score"], 44)
        self.assertTrue(got[0]["url"].startswith("https://www.reddit.com/r/ClaudeAI/"))
        cov = C.Coverage()
        self.assertEqual(C.collect_reddit(CFG, cov, (None, None, None), SINCE), [])     # no scraping fallback
        self.assertEqual(cov.rows["reddit"]["status"], "skipped")

    def test_markets(self):
        cov = C.Coverage()
        with mock.patch.object(C, "get_json", side_effect=fake_get_json({"public-search": POLY, "search-markets": MANIFOLD_SEARCH, "/v0/market/": MANIFOLD_FULL})):
            items = C.collect_markets(CFG, cov)
        pm = [i for i in items if i["platform"] == "polymarket"]
        self.assertEqual(len(pm), 1)                                   # closed market and the Xi market are excluded
        self.assertAlmostEqual(pm[0]["extra"]["probability"], 0.675)
        self.assertIn("(1d +45%)", pm[0]["text"])
        mf = [i for i in items if i["platform"] == "manifold"]
        self.assertEqual(mf[0]["extra"]["answers"][0]["text"], "Nov 2026")

    def test_hn_bsky_pages(self):
        cov = C.Coverage()
        with mock.patch.object(C, "get_json", side_effect=fake_get_json({"hn.algolia.com": HN})):
            hn = C.collect_hn(dict(CFG, hn={"queries": ["Anthropic"], "min_points": 3}), cov, SINCE)
        self.assertEqual(hn[0]["cites"], ["https://www.axios.com/2026/10/21/anthropic-ipo-ai-costs"])
        with mock.patch.object(C, "http", return_value=(200, json.dumps(BSKY), {})), mock.patch.object(C, "get_json", return_value={"feed": []}):
            bs = C.collect_bluesky(dict(CFG, bluesky={"searches": ["Anthropic"], "accounts": ["x"]}), cov, (None, None), SINCE)
        self.assertEqual(bs[0]["url"], "https://bsky.app/profile/jackclark.bsky.social/post/3k")
        with mock.patch.object(C, "http", return_value=(200, NEWS_HTML, {})):
            pg = C.collect_pages(dict(CFG, pages=[CFG["pages"][0]]), cov, {})
        self.assertEqual([p["title"] for p in pg], ["Anthropic commits 5% of its shares to a new Public Benefit Fund"])

    def test_watch_pages_emit_only_on_change(self):
        cov, state = C.Coverage(), {}
        cfg = dict(CFG, watch_pages=[{"name": "Docs", "url": "https://docs.example/x", "platform": "docs.claude.com", "tier": "A"}])
        with mock.patch.object(C, "http", return_value=(200, "<p>Release notes. Version one is here today.</p>", {})):
            self.assertEqual(C.collect_watch_pages(cfg, state, cov), [])          # first sight: baseline only
        with mock.patch.object(C, "http", return_value=(200, "<p>Release notes. Version one is here today. Version two adds a new model id.</p>", {})):
            items = C.collect_watch_pages(cfg, state, cov)
        self.assertEqual(len(items), 1)
        self.assertIn("Version two adds a new model id.", items[0]["extra"]["new_lines"])

    def test_merge_and_digest(self):
        old = C.item("x:1", "x", "btibor91", "https://x.com/btibor91/status/1", "Anthropic leak", C.NOW - dt.timedelta(hours=30), "post", metrics={"likes": 10})
        old["first_seen"] = C.iso(C.NOW - dt.timedelta(hours=30)); old["last_seen"] = old["first_seen"]; old["metrics_history"] = []
        stale = C.item("x:0", "x", "a", "u", "ancient", C.NOW - dt.timedelta(days=30), "post"); stale["first_seen"] = C.iso(C.NOW - dt.timedelta(days=30))
        new = C.item("x:1", "x", "btibor91", "https://x.com/btibor91/status/1", "Anthropic leak", C.NOW - dt.timedelta(hours=30), "post", metrics={"likes": 25})
        other = C.item("hn:2", "hn", "Hacker News", "https://news.ycombinator.com/item?id=2", "Nothing relevant here", C.NOW, "story", metrics={"points": 50})
        merged = C.merge([old, stale], [new, other], 7)
        ids = {m["id"] for m in merged}
        self.assertEqual(ids, {"x:1", "hn:2"})
        x1 = next(m for m in merged if m["id"] == "x:1")
        self.assertEqual(x1["metrics"]["likes"], 25)
        self.assertEqual(x1["metrics_history"][0]["likes"], 10)
        cov = C.Coverage(); cov.ok("x", 1)
        dig = C.make_digest(merged, CFG, cov, {"x_reads_last_run": 3})
        self.assertEqual([(d["by"], d["sid"]) for d in dig["items"]], [("btibor91", "1")])        # HN item has no keyword match
        self.assertEqual(dig["x_reads_last_run"], 3)
        self.assertEqual(dig["schema"], "signals_digest/2")


# ----------------------------------------------------------------------------- early detection, the size budget and the tripwires
def mk(id_, plat, source, text, hours_ago=3, kind="post", **kw):
    url = kw.pop("url", f"https://example.com/{id_}")
    it = C.item(id_, plat, source, url, text, C.NOW - dt.timedelta(hours=hours_ago), kind, **kw)
    it["first_seen"] = it["created_at"]
    it["last_seen"] = it["created_at"]
    it["metrics_history"] = []
    return it


OPENROUTER = {"data": [
    {"id": "anthropic/claude-fable-9", "name": "Anthropic: Claude Fable 9", "created": int((C.NOW - dt.timedelta(days=1)).timestamp()), "context_length": 1000000,
     "description": "A new frontier model.", "pricing": {"prompt": "0.000005", "completion": "0.000025"}},
    {"id": "anthropic/claude-3-haiku", "name": "Anthropic: Claude 3 Haiku", "created": 1710000000, "pricing": {"prompt": "0.00000025", "completion": "0.00000125"}},
    {"id": "openrouter/horizon-x", "name": "Horizon X", "created": int((C.NOW - dt.timedelta(hours=5)).timestamp()), "description": "Cloaked model provided to gather feedback."},
    {"id": "openai/gpt-9", "name": "OpenAI: GPT-9", "created": int(C.NOW.timestamp()), "description": "Something else."}]}

LITELLM1 = {"sample_spec": {"litellm_provider": "x"}, "gpt-4": {"litellm_provider": "openai"}, "claude-opus-4-5": {"litellm_provider": "anthropic", "max_input_tokens": 200000}}
LITELLM2 = dict(LITELLM1, **{"claude-fable-9": {"litellm_provider": "anthropic", "max_input_tokens": 1000000, "input_cost_per_token": 5e-06, "output_cost_per_token": 2.5e-05},
                             "anthropic.claude-fable-9-v1:0": {"litellm_provider": "bedrock"}})

STATUSPAGE = {"components": [
    {"id": "g1", "name": "Claude Code", "created_at": "2024-01-01T00:00:00Z", "group": True},
    {"id": "c1", "name": "Claude Cowork", "created_at": RECENT, "group_id": "g1", "description": "Agentic desktop app"},
    {"id": "c2", "name": "claude.ai", "created_at": "2024-01-01T00:00:00Z"}]}

JOBS1 = {"jobs": [{"id": 1, "title": "Research Engineer", "location": {"name": "San Francisco, CA"}, "absolute_url": "https://jobs.example/1", "updated_at": RECENT}]}
JOBS2 = {"jobs": JOBS1["jobs"] + [
    {"id": 2, "title": "Head of Investor Relations", "location": {"name": "San Francisco, CA"}, "absolute_url": "https://jobs.example/2", "updated_at": RECENT},
    {"id": 3, "title": "Account Executive", "location": {"name": "Tokyo, Japan | San Francisco, CA"}, "absolute_url": "https://jobs.example/3", "updated_at": RECENT},
    {"id": 4, "title": "Software Engineer", "location": {"name": "San Francisco, CA"}, "absolute_url": "https://jobs.example/4", "updated_at": RECENT}]}

SITEMAP_INDEX = '<?xml version="1.0"?><sitemapindex><sitemap><loc>https://site.example/sitemap-1.xml</loc></sitemap></sitemapindex>'
SITEMAP_A = ('<urlset><url><loc>https://site.example/news/old-post</loc><lastmod>2026-01-01</lastmod></url>'
             '<url><loc>https://site.example/careers/job-1</loc></url></urlset>')
SITEMAP_B = SITEMAP_A.replace("</urlset>", '<url><loc>https://site.example/news/claude-fable-9-launch</loc><lastmod>2026-10-27</lastmod></url>'
                                          '<url><loc>https://site.example/careers/job-2</loc></url></urlset>')


class EarlyDetectionTests(unittest.TestCase):
    def test_norm_id_makes_one_spelling_per_model(self):
        for tok in ("claude-opus-4-5-20251101", "anthropic/claude-opus-4.5", "Opus 4.5", "us.anthropic.claude-opus-4-5-20251101-v1:0", "vertex_ai/claude-opus-4-5@20251101"):
            self.assertEqual(C.norm_id(tok), "opus-4-5", tok)
        self.assertEqual(C.norm_id("claude-3-5-sonnet-20241022"), "sonnet-3-5")
        self.assertEqual(C.norm_id("claude-code"), "claude-code")
        self.assertEqual(C.ids_in("Using claude-opus-4-5 and Claude Opus 4.5; Sonnet 5 is rumored; try claude_fable_9"), {"opus-4-5", "sonnet-5", "fable-9"})

    def test_flags_vocabulary_novelty_and_corroboration(self):
        old = mk("x:10", "x", "a", "Using claude-opus-4-5 for everything", hours_ago=96)
        a = mk("x:11", "x", "leaker1", "Spotted claude-fable-9 in the model picker, rolling out soon", tier="D")
        b = mk("x:12", "x", "leaker2", "claude-fable-9 shows up in the API too")
        rt = mk("x:15", "x", "leaker3", "RT @leaker1: Spotted claude-fable-9 in the picker", kind="repost")
        c = mk("x:13", "x", "someone", "Claude Opus 4.5 is great", hours_ago=2)
        d = mk("x:14", "x", "someone", "Anthropic is launching something soon?")
        state = {}
        C.annotate_items([old, a, b, rt, c, d], state)
        self.assertTrue({"leak", "id", "newid"} <= set(a["flags"]))
        self.assertEqual(a["corr"], 2)                                  # two independent authors; the repost does not count
        self.assertIn("newid", b["flags"])
        self.assertIn("id", c["flags"])
        self.assertNotIn("newid", c["flags"])                           # opus-4-5 was already mentioned four days ago
        self.assertEqual(d["flags"], ["soon"])
        self.assertIn("opus-4-5", state["seen_ids"])
        self.assertNotIn("fable-9", state["seen_ids"])                  # first mentioned inside the window, so not yet "seen before"

    def test_rising_engagement_flag(self):
        hot = mk("x:20", "x", "someone", "Anthropic news", hours_ago=10, metrics={"likes": 100, "reposts": 10})
        hot["metrics_history"] = [{"likes": 5, "reposts": 0, "at": C.iso(C.NOW - dt.timedelta(hours=6))}]
        hot["last_seen"] = C.iso(C.NOW)
        quiet = mk("x:21", "x", "someone", "Anthropic news two", hours_ago=10, metrics={"likes": 30})
        quiet["metrics_history"] = [{"likes": 29, "at": C.iso(C.NOW - dt.timedelta(hours=6))}]
        quiet["last_seen"] = C.iso(C.NOW)
        C.annotate_items([hot, quiet], {})
        self.assertIn("rising", hot["flags"])
        self.assertNotIn("rising", quiet["flags"])

    def test_priority_puts_early_signals_above_chatter(self):
        leak = mk("x:30", "x", "lyraxana", "Spotted claude-fable-9 in the picker", tier="D", tags=["always"])
        chatter = mk("x:31", "x", "user", "Claude is nice", tier="C", metrics={"likes": 12})
        official = mk("x:32", "x", "claudeai", "Introducing something", tier="A", tags=["always"])
        repost = mk("x:33", "x", "user", "RT @claudeai: Introducing something", kind="repost")
        C.annotate_items([leak, chatter, official, repost], {})
        order = sorted([leak, chatter, official, repost], key=C.priority, reverse=True)
        self.assertEqual(order[0]["id"], "x:30")
        self.assertEqual(order[-1]["id"], "x:33")
        self.assertGreater(C.priority(official), C.priority(chatter))

    def test_digest_fits_budget_keeps_early_signals_and_loses_nothing_silently(self):
        items = [mk(f"x:{1000 + i}", "x", f"user{i % 40}", f"Claude thing number {i} " + "blah " * 60, hours_ago=1 + (i % 40), tier="C" if i % 3 else None, metrics={"likes": i % 17})
                 for i in range(300)]
        leak = mk("x:9999", "x", "lyraxana", "Spotted a new model id claude-fable-9 in the picker, rolling out soon", hours_ago=30, tier="D", tags=["always"])
        official = mk("x:9998", "x", "claudeai", "Introducing something", hours_ago=2, tier="A", tags=["always"])
        market = mk("pm:1", "polymarket", "Polymarket", "Will Anthropic IPO by Nov 20? - Yes 65%", hours_ago=100, kind="market", tier="market",
                    extra={"probability": 0.65, "end_date": "2026-11-21T04:59:00Z"})
        gh = mk("gh:1", "github", "anthropics/claude-code", "v2.1.0 release notes " + "x " * 200, hours_ago=20, tier="A", url="https://github.com/anthropics/claude-code/releases/tag/v2.1.0")
        everything = items + [leak, official, market, gh]
        for budget in (24000, 6000):
            dig = C.make_digest([dict(i) for i in everything], dict(CFG, digest_budget_chars=budget), C.Coverage(), {})
            text = C.dumps_digest(dig)
            self.assertLessEqual(len(text), budget)
            parsed = json.loads(text)                                   # plain valid JSON
            self.assertIs(parsed["complete"], True)
            self.assertTrue(text.rstrip().endswith('"complete":true\n}'.rstrip()))
            self.assertEqual(parsed["n_items"], len(parsed["items"]))
            self.assertEqual(parsed["items"][0]["sid"], "9999")           # the early signal ranks first
            self.assertEqual(parsed["items"][0]["flags"][:1], ["leak"])
            self.assertIn("9998", [i.get("sid") for i in parsed["items"]])
            self.assertIn("polymarket", {i["plat"] for i in parsed["items"]})
            self.assertEqual(parsed["overflow"]["n"] + parsed["n_items"], len(everything))        # every candidate is either in the digest or counted as overflow
            self.assertEqual(len(dig["_more"]), parsed["overflow"]["n"])
            self.assertEqual(parsed["budget"]["chars_limit"], budget)
            more = json.loads(C.dumps_more(dig, CFG))
            self.assertEqual(more["n_items"], len(more["items"]))
            self.assertIs(more["complete"], True)

    def test_compact_entries(self):
        x = mk("x:40", "x", "lyraxana", "Spotted claude-fable-9", tier="D", metrics={"likes": 5, "reposts": 0, "views": 9000, "followers": 1200})
        mkt = mk("pm:2", "polymarket", "Polymarket", "Will Anthropic IPO? - Yes 65%", kind="market", tier="market", extra={"probability": 0.6501, "end_date": "2026-11-21T04:59:00Z"})
        rss = mk("rss:1", "substack", "Zvi", "Big headline\nBody text that follows the headline.", kind="article", title="Big headline", tier="B")
        watch = mk("watch:1", "docs.claude.com", "Docs", "Docs changed. New text: a | b", kind="changelog", tier="A", tags=["tripwire"], extra={"new_lines": ["claude-fable-9 is available", "b"]})
        C.annotate_items([x, mkt, rss, watch], {})
        cx = C.compact(x, 100)
        self.assertEqual((cx["plat"], cx["by"], cx["sid"], cx["fol"], cx["tier"]), ("x", "lyraxana", "40", 1200, "D"))
        self.assertNotIn("url", cx)
        self.assertEqual(cx["metrics"], {"likes": 5})                    # zero and views dropped
        self.assertRegex(cx["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\dZ$")
        cm = C.compact(mkt, 100)
        self.assertEqual((cm["prob"], cm["end"], cm["kind"]), (0.65, "2026-11-21", "market"))
        mf = mk("mf:1", "manifold", "Manifold", "Will Anthropic IPO? - Nov 2026 55%", kind="market", tier="market", extra={"answers": [], "close_time": 1798675140000})
        self.assertEqual(C.compact(mf, 100)["end"], "2026-12-30")                       # epoch milliseconds become a date
        self.assertEqual(C.compact(rss, 100)["text"], "Big headline. Body text that follows the headline.")
        self.assertEqual(C.compact(rss, 100)["src"], "Zvi")
        cw = C.compact(watch, 100)
        self.assertEqual(cw["new_lines"][0], "claude-fable-9 is available")
        self.assertIn("trip", cw["flags"])

    def test_ranking_failure_falls_back_to_a_plain_digest(self):
        items = [mk(f"x:{500 + i}", "x", "someone", f"Claude news {i}", hours_ago=i + 1) for i in range(5)]
        cov = C.Coverage()
        with mock.patch.object(C, "annotate_items", side_effect=RuntimeError("boom")):
            dig = C.make_digest_safely(items, CFG, cov, {})
        parsed = json.loads(C.dumps_digest(dig))
        self.assertEqual(parsed["n_items"], 5)
        self.assertEqual(parsed["items"][0]["text"], "Claude news 0")                  # newest first
        self.assertIs(parsed["complete"], True)
        self.assertEqual(cov.rows["digest"]["status"], "error")
        self.assertEqual(json.loads(C.dumps_more(dig, CFG))["n_items"], 0)

    def test_trim_keeps_word_boundaries(self):
        self.assertEqual(C.trim("short", 50), "short")
        t = C.trim("alpha beta gamma delta epsilon zeta eta theta", 25)
        self.assertLessEqual(len(t), 25)
        self.assertTrue(t.endswith("…"))
        self.assertNotIn("gamm…", t)

    def test_x_searches_accept_per_query_limits(self):
        cfg = dict(CFG, x=dict(CFG["x"], accounts=[], searches=[{"query": "claude-opus", "max_results": 30}, "Anthropic leak"], search_hours_utc=[]))
        calls = []

        def _get(url, headers=None):
            calls.append(url)
            return {"data": [], "meta": {}}
        with mock.patch.object(C, "get_json", side_effect=_get):
            C.collect_x(cfg, {"x_user_ids": {}}, C.Coverage(), "tok", SINCE)
        search_calls = [u for u in calls if "search/recent" in u]
        self.assertIn("max_results=30", search_calls[0])
        self.assertIn("max_results=50", search_calls[1])

    def test_rss_fallback_url_is_used_when_primary_is_blocked(self):
        cfg = dict(CFG, rss=[{"name": "Import AI", "url": "https://blocked.example/feed", "fallbacks": ["https://mirror.example/feed"], "platform": "substack", "tier": "B",
                              "filter": False, "kind": "article"}])
        seen = []

        def _http(url, **k):
            seen.append(url)
            return (403, "", {}) if "blocked" in url else (200, ATOM, {})
        cov = C.Coverage()
        with mock.patch.object(C, "http", side_effect=_http):
            items = C.collect_rss(cfg, cov, SINCE)
        self.assertEqual(seen, ["https://blocked.example/feed", "https://mirror.example/feed"])
        self.assertEqual(cov.rows["feeds"]["status"], "ok")
        self.assertGreaterEqual(len(items), 1)


class TripwireTests(unittest.TestCase):
    def test_registry_openrouter_and_litellm(self):
        cfg = dict(CFG, registry={"openrouter": {"regex": "anthropic|claude|cloaked|stealth"}, "litellm": {"regex": "claude|anthropic"}})
        state, cov = {}, C.Coverage()
        with mock.patch.object(C, "get_json", side_effect=fake_get_json({"openrouter.ai": OPENROUTER, "litellm": LITELLM1})):
            first = C.collect_registry(cfg, state, cov, SINCE)
        ids = sorted(i["id"] for i in first)
        self.assertEqual(ids, ["or:anthropic/claude-fable-9", "or:openrouter/horizon-x"])        # old haiku and the unrelated OpenAI model are skipped; LiteLLM is a baseline
        fable = next(i for i in first if i["id"].endswith("fable-9"))
        self.assertIn("$5/M", fable["text"])
        self.assertNotIn("provider not named", fable["text"])
        self.assertIn("provider not named", next(i for i in first if i["id"].endswith("horizon-x"))["text"])
        self.assertEqual(fable["platform"], "registry")
        self.assertIn("tripwire", fable["tags"])
        self.assertEqual(state["litellm_keys"], ["claude-opus-4-5"])
        with mock.patch.object(C, "get_json", side_effect=fake_get_json({"openrouter.ai": {"data": []}, "litellm": LITELLM2})):
            second = C.collect_registry(cfg, state, cov, SINCE)
        self.assertEqual(sorted(i["title"] for i in second), ["anthropic.claude-fable-9-v1:0", "claude-fable-9"])
        self.assertEqual(cov.rows["registry"]["status"], "ok")
        self.assertIn("2 new", cov.rows["registry"]["note"])

    def test_registry_reports_error_when_everything_fails(self):
        cfg = dict(CFG, registry={"openrouter": {}, "litellm": {}})
        cov = C.Coverage()
        with mock.patch.object(C, "get_json", side_effect=RuntimeError("boom")):
            self.assertEqual(C.collect_registry(cfg, {}, cov, SINCE), [])
        self.assertEqual(cov.rows["registry"]["status"], "error")

    def test_statuspage_components(self):
        cfg = dict(CFG, statuspage={"name": "Claude status components", "components_url": "https://status.example/api/v2/components.json", "page_url": "https://status.example"})
        cov = C.Coverage()
        with mock.patch.object(C, "get_json", return_value=STATUSPAGE):
            items = C.collect_statuspage(cfg, cov, SINCE)
        self.assertEqual(len(items), 1)
        self.assertIn("'Claude Cowork' under 'Claude Code'", items[0]["text"])
        self.assertEqual(items[0]["platform"], "status")
        self.assertEqual(cov.rows["statuspage"]["status"], "ok")

    def test_careers_watch_regex_and_new_locations(self):
        cfg = dict(CFG, careers={"name": "Anthropic", "greenhouse_board": "anthropic", "watch_regex": r"\b(investor relations|ipo)\b"})
        state, cov = {}, C.Coverage()
        with mock.patch.object(C, "get_json", return_value=JOBS1):
            self.assertEqual(C.collect_careers(cfg, state, cov), [])                        # baseline
        self.assertIn("baseline", cov.rows["careers"]["note"])
        with mock.patch.object(C, "get_json", return_value=JOBS2):
            items = C.collect_careers(cfg, state, cov)
        self.assertEqual(sorted(i["id"] for i in items), ["job:2", "job:3"])               # IR title; first posting in Tokyo; the plain engineer role is ignored
        self.assertIn("first posting in Tokyo, Japan", next(i for i in items if i["id"] == "job:3")["text"])
        self.assertEqual(sorted(state["job_ids"]), [1, 2, 3, 4])
        with mock.patch.object(C, "get_json", return_value=JOBS2):
            self.assertEqual(C.collect_careers(cfg, state, cov), [])                        # nothing new on the next run

    def test_sitemap_diff_expands_index_and_filters(self):
        cfg = dict(CFG, sitemaps=[{"name": "site sitemap", "url": "https://site.example/sitemap.xml", "platform": "sitemap", "tier": "A", "exclude": "/careers"}])
        state, cov = {}, C.Coverage()

        def serve(child):
            return lambda url, **k: (200, SITEMAP_INDEX if url.endswith("/sitemap.xml") else child, {})
        with mock.patch.object(C, "http", side_effect=serve(SITEMAP_A)):
            self.assertEqual(C.collect_sitemaps(cfg, state, cov), [])                       # baseline
        self.assertEqual(len(state["sitemaps"]["site sitemap"]), 1)                         # the careers URL is excluded
        with mock.patch.object(C, "http", side_effect=serve(SITEMAP_B)):
            items = C.collect_sitemaps(cfg, state, cov)
        self.assertEqual([i["url"] for i in items], ["https://site.example/news/claude-fable-9-launch"])
        self.assertEqual(items[0]["title"], "claude fable 9 launch")
        self.assertEqual(items[0]["platform"], "sitemap")
        C.annotate_items(items, {})
        self.assertTrue({"trip", "id", "newid"} <= set(items[0]["flags"]))                  # the slug alone names a model nobody has mentioned before

    def test_watch_page_changes_are_tripwires(self):
        cov, state = C.Coverage(), {}
        cfg = dict(CFG, watch_pages=[{"name": "robots", "url": "https://site.example/robots.txt", "platform": "anthropic.com", "tier": "A", "min_len": 8}])
        with mock.patch.object(C, "http", return_value=(200, "User-agent: *\nDisallow: /a\n", {})):
            C.collect_watch_pages(cfg, state, cov)
        with mock.patch.object(C, "http", return_value=(200, "User-agent: *\nDisallow: /a\nDisallow: /fable-9-launch\n", {})):
            items = C.collect_watch_pages(cfg, state, cov)
        self.assertEqual(len(items), 1)
        self.assertIn("Disallow: /fable-9-launch", items[0]["extra"]["new_lines"])
        self.assertIn("tripwire", items[0]["tags"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
