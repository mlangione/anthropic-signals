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
        self.assertEqual([d["id"] for d in dig["items"]], ["x:1"])        # HN item has no keyword match
        self.assertEqual(dig["x_reads_last_run"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=1)
