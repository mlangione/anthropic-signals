#!/usr/bin/env python3
"""Anthropic signals collector.

Polls the sources listed in sources.json (X, Reddit, Hacker News, Bluesky, RSS/Atom feeds, the Claude
status page, Anthropic's newsroom pages, GitHub releases, npm, watched docs pages, the App Store and
prediction markets) plus tripwires for things that tend to appear before an announcement (model registries
such as OpenRouter and LiteLLM, status-page components, the careers board, sitemaps) and writes three files:

  <out>/signals_raw.json          every item seen in the last `window_days` days, with engagement metrics
                                  re-polled on each run (metrics_history) and the collector's own state
                                  (X user ids, since_ids, page hashes, registry keys) so the next run only fetches what is new
  <out>/signals_digest.json       what the daily dashboard task reads: candidates from the last `digest_hours` hours
                                  ranked best first by an early-detection priority (leak vocabulary, model ids, identifiers
                                  never seen before, tripwires, rising engagement, source tier) and sized to
                                  `digest_budget_chars` so a fetch tool cannot truncate it; it ends with "complete": true
  <out>/signals_digest_more.json  the candidates that did not fit, same format

Standard library only. Credentials come from environment variables and every source degrades to
"skipped" or "error" in the coverage block instead of failing the run:

  X_BEARER_TOKEN                      X API v2 app-only bearer token (pay-per-use or Basic tier)
  REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT   Reddit "script" app (free tier, non-commercial)
  BSKY_HANDLE, BSKY_APP_PASSWORD      only needed if Bluesky post search starts requiring a session
  BRAVE_API_KEY                       Brave Search API (free tier) for reddit.com-scoped searches; or
  GOOGLE_CSE_KEY, GOOGLE_CSE_CX       Google Programmable Search JSON API for the same
  GITHUB_TOKEN                        optional; raises the GitHub API limit (set automatically in Actions)
  GIST_ID, GIST_TOKEN                 optional; also uploads the digest to a gist

Usage:
  python3 collect.py --previous data/signals_raw.json --out data
  python3 collect.py --no-x --no-reddit            # keyless run (feeds, HN, Bluesky, GitHub, npm, markets)
  python3 collect.py --create-gist                 # one-off: create the public gist, print its id and raw URL
"""
import argparse
import base64
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "frontier-lab-signals-collector/1.0 (personal research dashboard; contact via repo issues)"
NOW = dt.datetime.now(dt.timezone.utc)
TIMEOUT = int(os.environ.get("COLLECT_TIMEOUT", "30"))
DEADLINE = [None]   # wall-clock limit for the whole run (set from --max-seconds); checked before every request


# ----------------------------------------------------------------------------- small utilities
def iso(d):
    return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(s):
    """Best-effort parse of ISO 8601, RFC 2822 and epoch values to an aware UTC datetime (or None)."""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        v = float(s)
        if v > 1e12:
            v /= 1000.0
        return dt.datetime.fromtimestamp(v, dt.timezone.utc)
    s = str(s).strip()
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime
    try:
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(s, limit=None):
    if not s:
        return ""
    p = _Stripper()
    try:
        p.feed(str(s))
    except Exception:  # noqa: BLE001 - malformed markup is not worth failing a run
        return re.sub(r"<[^>]+>", " ", str(s))[:limit] if limit else re.sub(r"<[^>]+>", " ", str(s))
    text = html.unescape(" ".join("".join(p.parts).split()))
    return text[:limit] if limit else text


def http(url, headers=None, data=None, method=None, retries=1, accept_json=False):
    """GET/POST with a UA, timeout and a couple of retries; returns (status, body_text, headers)."""
    if DEADLINE[0] and time.time() > DEADLINE[0]:
        raise RuntimeError("run deadline exceeded before request")
    hdrs = {"User-Agent": UA, "Accept": "application/json" if accept_json else "*/*"}
    hdrs.update(headers or {})
    body = data
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif isinstance(data, str):
        body = data.encode()
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace") if e.fp else ""
            if e.code == 429 and attempt < retries:
                reset = e.headers.get("x-rate-limit-reset") or e.headers.get("Retry-After")
                wait = 5.0
                if reset and reset.isdigit():
                    wait = min(60.0, max(1.0, float(reset) - time.time() if float(reset) > 1e9 else float(reset)))
                time.sleep(wait)
                continue
            return e.code, txt, dict(e.headers or {})
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"network failure for {url}: {last}")


def get_json(url, headers=None):
    status, body, _ = http(url, headers=headers, accept_json=True)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:200]}")
    return json.loads(body)


def item(id_, platform, source, url, text, created, kind, author=None, title=None, metrics=None, tier=None,
         cites=None, tags=None, extra=None):
    created = created or NOW
    return {
        "id": id_, "platform": platform, "source": source, "author": author or source, "tier": tier,
        "url": url, "title": title, "text": (text or "")[:1200], "created_at": iso(created), "kind": kind,
        "metrics": metrics or {}, "cites": cites or [], "tags": tags or [], "extra": extra or {},
    }


class Coverage:
    def __init__(self):
        self.rows = {}

    def ok(self, platform, count, note=""):
        self.rows[platform] = {"status": "ok", "count": count, "note": note}

    def skipped(self, platform, note):
        self.rows[platform] = {"status": "skipped", "count": 0, "note": note}

    def error(self, platform, err):
        self.rows[platform] = {"status": "error", "count": 0, "note": str(err)[:300]}
        print(f"  ! {platform}: {str(err)[:200]}", file=sys.stderr)


# ----------------------------------------------------------------------------- X (api.x.com v2)
X_TWEET_FIELDS = "created_at,public_metrics,referenced_tweets,author_id,conversation_id"


def x_headers(token):
    return {"Authorization": f"Bearer {token}"}


def x_resolve_ids(handles, state, token):
    ids = dict(state.get("x_user_ids", {}))
    missing = [h for h in handles if h.lower() not in {k.lower() for k in ids}]
    for i in range(0, len(missing), 100):
        chunk = missing[i:i + 100]
        data = get_json("https://api.x.com/2/users/by?usernames=" + urllib.parse.quote(",".join(chunk)) + "&user.fields=public_metrics",
                        headers=x_headers(token))
        for u in data.get("data", []):
            ids[u["username"]] = {"id": u["id"], "followers": (u.get("public_metrics") or {}).get("followers_count")}
        for err in data.get("errors", []):
            print(f"  ! X handle not found: {err.get('value')}", file=sys.stderr)
    state["x_user_ids"] = ids
    return ids


def x_items_from_response(data, default_author=None, source_label=None, tier=None, always=False):
    users = {u["id"]: u for u in (data.get("includes", {}).get("users") or [])}
    out = []
    for t in data.get("data", []) or []:
        author = default_author
        if not author and t.get("author_id") in users:
            author = users[t["author_id"]]["username"]
        author = author or t.get("author_id") or "unknown"
        pm = t.get("public_metrics") or {}
        refs = t.get("referenced_tweets") or []
        cites = [f"https://x.com/i/status/{r['id']}" for r in refs]
        kind = "repost" if any(r["type"] == "retweeted" for r in refs) else ("quote" if any(r["type"] == "quoted" for r in refs) else ("reply" if any(r["type"] == "replied_to" for r in refs) else "post"))
        followers = (users.get(t.get("author_id"), {}).get("public_metrics") or {}).get("followers_count")
        out.append(item(
            f"x:{t['id']}", "x", source_label or author, f"https://x.com/{author}/status/{t['id']}", t.get("text", ""),
            parse_dt(t.get("created_at")), kind, author=author, tier=tier,
            metrics={"likes": pm.get("like_count"), "reposts": pm.get("retweet_count"), "replies": pm.get("reply_count"),
                     "quotes": pm.get("quote_count"), "views": pm.get("impression_count"), "followers": followers},
            cites=cites, tags=["always"] if always else [], extra={"conversation_id": t.get("conversation_id")}))
    return out


def collect_x(cfg, state, cov, token, since_dt):
    xc = cfg.get("x") or {}
    if not token:
        cov.skipped("x", "X_BEARER_TOKEN not set")
        return []
    items, reads = [], 0
    try:
        handles = [a["handle"] for a in xc.get("accounts", [])]
        ids = x_resolve_ids(handles, state, token)
        since_ids = dict(state.get("x_since_ids", {}))
        for acc in xc.get("accounts", []):
            h = acc["handle"]
            info = next((v for k, v in ids.items() if k.lower() == h.lower()), None)
            if not info:
                continue
            params = {"max_results": "100", "tweet.fields": X_TWEET_FIELDS, "expansions": "referenced_tweets.id,referenced_tweets.id.author_id",
                      "user.fields": "username,public_metrics"}
            if since_ids.get(h):
                params["since_id"] = since_ids[h]
            else:
                params["start_time"] = iso(since_dt)
            data = get_json(f"https://api.x.com/2/users/{info['id']}/tweets?" + urllib.parse.urlencode(params), headers=x_headers(token))
            got = x_items_from_response(data, default_author=h, tier=acc.get("tier"), always=acc.get("always", False))
            for it in got:
                it["metrics"]["followers"] = it["metrics"].get("followers") or info.get("followers")
            items.extend(got)
            reads += len(got)
            newest = (data.get("meta") or {}).get("newest_id")
            if newest:
                since_ids[h] = newest
        state["x_since_ids"] = since_ids
        # keyword searches only at the configured hours, to keep paid reads predictable
        hours = xc.get("search_hours_utc") or []
        if not hours or NOW.hour in hours or os.environ.get("X_FORCE_SEARCH") == "1":
            search_since = dict(state.get("x_search_since_ids", {}))
            for sq in xc.get("searches", []):
                q, max_r = (sq["query"], sq.get("max_results")) if isinstance(sq, dict) else (sq, None)
                params = {"query": q, "max_results": str(max(10, min(100, int(max_r or xc.get("search_max_results", 50))))), "tweet.fields": X_TWEET_FIELDS,
                          "expansions": "author_id", "user.fields": "username,public_metrics"}
                key = hashlib.sha1(q.encode()).hexdigest()[:10]
                if search_since.get(key):
                    params["since_id"] = search_since[key]
                else:
                    params["start_time"] = iso(max(since_dt, NOW - dt.timedelta(days=6, hours=23)))
                data = get_json("https://api.x.com/2/tweets/search/recent?" + urllib.parse.urlencode(params), headers=x_headers(token))
                got = x_items_from_response(data)
                for it in got:
                    it["tags"].append("search")
                    it["extra"]["query"] = q
                items.extend(got)
                reads += len(got)
                newest = (data.get("meta") or {}).get("newest_id")
                if newest:
                    search_since[key] = newest
            state["x_search_since_ids"] = search_since
            note = f"{reads} posts read (timelines + {len(xc.get('searches', []))} searches)"
        else:
            note = f"{reads} posts read (timelines only; searches run at {hours} UTC)"
        cov.ok("x", len(items), note)
    except Exception as e:  # noqa: BLE001
        cov.error("x", e)
    state["x_reads_last_run"] = reads
    return items


# ----------------------------------------------------------------------------- Reddit
def reddit_token(cid, secret, ua):
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    status, body, _ = http("https://www.reddit.com/api/v1/access_token", headers={"Authorization": f"Basic {auth}", "User-Agent": ua},
                           data={"grant_type": "client_credentials"}, method="POST", accept_json=True)
    if status != 200:
        raise RuntimeError(f"reddit token HTTP {status}: {body[:200]}")
    return json.loads(body)["access_token"]


def reddit_posts(listing, sub_name, tier, kw, filt, min_score):
    out = []
    for child in (listing.get("data") or {}).get("children", []):
        d = child.get("data") or {}
        if child.get("kind") != "t3":
            continue
        title, body = d.get("title", ""), d.get("selftext", "") or ""
        text = f"{title}\n{body}".strip()
        if filt and not kw.search(text):
            continue
        if (d.get("score") or 0) < min_score and (d.get("num_comments") or 0) < 5:
            continue
        out.append(item(
            f"reddit:{d.get('name')}", "reddit", f"r/{d.get('subreddit') or sub_name}", "https://www.reddit.com" + d.get("permalink", ""),
            text, parse_dt(d.get("created_utc")), "post", author=f"u/{d.get('author')}", title=title, tier=tier,
            metrics={"score": d.get("score"), "upvote_ratio": d.get("upvote_ratio"), "comments": d.get("num_comments")},
            cites=[d["url"]] if d.get("url") and not d.get("is_self") else [], extra={"flair": d.get("link_flair_text")}))
    return out


def collect_reddit(cfg, cov, creds, since_dt):
    """Runs only with approved OAuth credentials. Reddit closed self-service API registration in November 2025 and
    announced (Sept 30, 2026) that RSS ends Nov 13, 2026 and public Data API access ends by March 2027, so there is
    deliberately no unauthenticated fallback: without credentials the source is skipped, not scraped."""
    rc = cfg.get("reddit") or {}
    kw = re.compile(cfg["keywords"], re.I)
    cid, secret, ua = creds
    if not (cid and secret):
        cov.skipped("reddit", "no approved API credentials (Reddit closed self-service registration; public API ends Mar 2027)")
        return []
    items = []
    try:
        tok = reddit_token(cid, secret, ua or UA)
        base, hdr = "https://oauth.reddit.com", {"Authorization": f"bearer {tok}", "User-Agent": ua or UA}
        for sub in rc.get("subreddits", []):
            for sort in ("new", "hot"):
                data = get_json(f"{base}/r/{sub['name']}/{sort}?limit=100&raw_json=1", headers=hdr)
                items.extend(reddit_posts(data, sub["name"], "D", kw, sub.get("filter", True), rc.get("min_score", 5)))
        for q in rc.get("searches", []):
            data = get_json(f"{base}/search?" + urllib.parse.urlencode({"q": q, "sort": "new", "t": "week", "limit": 100, "type": "link", "raw_json": 1}), headers=hdr)
            items.extend(reddit_posts(data, "search", "D", kw, True, rc.get("min_score", 5)))
        items = [it for it in items if parse_dt(it["created_at"]) >= since_dt]
        cov.ok("reddit", len(items), "OAuth")
    except Exception as e:  # noqa: BLE001
        cov.error("reddit", e)
    return items


# ----------------------------------------------------------------------------- Hacker News (Algolia)
def collect_hn(cfg, cov, since_dt):
    hc = cfg.get("hn") or {}
    items, seen = [], set()
    try:
        for q in hc.get("queries", []):
            for tags in ("story", "show_hn", "ask_hn"):
                url = "https://hn.algolia.com/api/v1/search_by_date?" + urllib.parse.urlencode(
                    {"query": q, "tags": tags, "hitsPerPage": 100, "numericFilters": f"created_at_i>{int(since_dt.timestamp())}"})
                for h in get_json(url).get("hits", []):
                    if h["objectID"] in seen or (h.get("points") or 0) < hc.get("min_points", 3):
                        continue
                    seen.add(h["objectID"])
                    hn_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
                    items.append(item(
                        f"hn:{h['objectID']}", "hn", "Hacker News", hn_url, (h.get("title") or "") + ("\n" + strip_html(h.get("story_text"), 800) if h.get("story_text") else ""),
                        parse_dt(h.get("created_at")), "story", author=h.get("author"), title=h.get("title"), tier="C",
                        metrics={"points": h.get("points"), "comments": h.get("num_comments")}, cites=[h["url"]] if h.get("url") else []))
        cov.ok("hn", len(items))
    except Exception as e:  # noqa: BLE001
        cov.error("hn", e)
    return items


# ----------------------------------------------------------------------------- Bluesky (AT Protocol)
def bsky_session(handle, app_password):
    status, body, _ = http("https://bsky.social/xrpc/com.atproto.server.createSession", headers={"Content-Type": "application/json"},
                           data=json.dumps({"identifier": handle, "password": app_password}), method="POST", accept_json=True)
    if status != 200:
        raise RuntimeError(f"bluesky session HTTP {status}: {body[:200]}")
    return json.loads(body)["accessJwt"]


def bsky_post_item(p, tier="D"):
    rec = p.get("record") or {}
    author = (p.get("author") or {}).get("handle", "unknown")
    rkey = p["uri"].rsplit("/", 1)[-1]
    embed = p.get("embed") or {}
    cites = []
    ext = embed.get("external") or (embed.get("media") or {}).get("external")
    if ext and ext.get("uri"):
        cites.append(ext["uri"])
    if (embed.get("record") or {}).get("uri"):
        cites.append(embed["record"]["uri"])
    return item(f"bsky:{p['uri']}", "bluesky", author, f"https://bsky.app/profile/{author}/post/{rkey}", rec.get("text", ""),
                parse_dt(rec.get("createdAt")), "post", author=author, tier=tier,
                metrics={"likes": p.get("likeCount"), "reposts": p.get("repostCount"), "replies": p.get("replyCount"), "quotes": p.get("quoteCount")},
                cites=cites)


def collect_bluesky(cfg, cov, creds, since_dt):
    bc = cfg.get("bluesky") or {}
    items, seen = [], set()
    hdr = {}
    try:
        for q in bc.get("searches", []):
            url = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?" + urllib.parse.urlencode(
                {"q": q, "sort": "latest", "limit": 100, "since": iso(since_dt)})
            status, body, _ = http(url, headers=hdr, accept_json=True)
            if status in (401, 403) and creds[0] and creds[1] and not hdr:
                hdr = {"Authorization": f"Bearer {bsky_session(*creds)}"}
                status, body, _ = http(url.replace("public.api.bsky.app", "bsky.social"), headers=hdr, accept_json=True)
            if status != 200:
                raise RuntimeError(f"searchPosts HTTP {status}: {body[:160]}")
            for p in json.loads(body).get("posts", []):
                if p["uri"] not in seen:
                    seen.add(p["uri"])
                    it = bsky_post_item(p)
                    it["tags"].append("search")
                    items.append(it)
        for actor in bc.get("accounts", []):
            data = get_json("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?" + urllib.parse.urlencode({"actor": actor, "limit": 50, "filter": "posts_no_replies"}))
            for f in data.get("feed", []):
                p = f.get("post") or {}
                if p.get("uri") and p["uri"] not in seen and parse_dt((p.get("record") or {}).get("createdAt")) >= since_dt:
                    seen.add(p["uri"])
                    it = bsky_post_item(p, tier="B")
                    it["tags"].append("always")
                    items.append(it)
        cov.ok("bluesky", len(items), "session" if hdr else "public AppView")
    except Exception as e:  # noqa: BLE001
        cov.error("bluesky", e)
    return items


# ----------------------------------------------------------------------------- RSS / Atom
NS = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/", "dc": "http://purl.org/dc/elements/1.1/"}


def parse_feed(xml_text):
    """Return a list of dicts {title, link, summary, published, id} from RSS 2.0 or Atom."""
    root = ET.fromstring(xml_text.encode("utf-8", "replace") if isinstance(xml_text, str) else xml_text)
    entries = []
    if root.tag.endswith("feed"):  # Atom
        for e in root.findall("atom:entry", NS):
            link = ""
            for l in e.findall("atom:link", NS):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", ""); break
            content = e.find("atom:content", NS)
            summary = e.find("atom:summary", NS)
            entries.append({"title": (e.findtext("atom:title", "", NS) or "").strip(), "link": link,
                            "summary": (content.text if content is not None and content.text else (summary.text if summary is not None else "")) or "",
                            "published": e.findtext("atom:published", "", NS) or e.findtext("atom:updated", "", NS),
                            "id": e.findtext("atom:id", "", NS) or link})
    else:  # RSS 2.0 / RDF
        for i in root.iter():
            if i.tag.endswith("item"):
                enc = i.find("content:encoded", NS)
                entries.append({"title": (i.findtext("title") or "").strip(), "link": (i.findtext("link") or "").strip(),
                                "summary": (enc.text if enc is not None and enc.text else i.findtext("description") or "") or "",
                                "published": i.findtext("pubDate") or i.findtext("dc:date", "", NS),
                                "id": (i.findtext("guid") or i.findtext("link") or "").strip()})
    return entries


def unwrap_redirect(url):
    """Google Alerts (and some aggregators) wrap links as https://www.google.com/url?...&url=<target>&...; return the target."""
    try:
        p = urllib.parse.urlparse(url)
        if p.netloc.endswith("google.com") and p.path == "/url":
            target = urllib.parse.parse_qs(p.query).get("url") or urllib.parse.parse_qs(p.query).get("q")
            if target:
                return target[0]
    except ValueError:
        pass
    return url


def platform_for_url(url, default):
    """A feed can carry links from many hosts (Google Alerts); label Reddit links as reddit so they are capped and tiered correctly."""
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.endswith("reddit.com"):
        return "reddit"
    return default


def collect_rss(cfg, cov, since_dt):
    kw = re.compile(cfg["keywords"], re.I)
    items, errors, n_feeds = [], [], 0
    for f in cfg.get("rss", []):
        if f.get("enabled") is False or not str(f.get("url", "")).startswith("http"):
            continue                                   # placeholder rows (e.g. a Google Alerts feed not yet created)
        try:
            status, body = None, ""
            for feed_url in [f["url"]] + list(f.get("fallbacks") or []):      # e.g. a Substack feed that answers 403 to datacenter IPs may have a mirror
                status, body, _ = http(feed_url)
                if status == 200:
                    break
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            n_feeds += 1
            for e in parse_feed(body):
                pub = parse_dt(e["published"])
                if pub and pub < since_dt:
                    continue
                title = strip_html(e["title"], 300)          # Google Alerts titles carry <b> tags
                text = strip_html(e["summary"], 1200)
                if f.get("filter", True) and not kw.search(title + " " + text):
                    continue
                link = unwrap_redirect(e["link"])
                eid = hashlib.sha1((link or e["id"] or title).encode()).hexdigest()[:16]
                plat = platform_for_url(link, f.get("platform", "rss")) if f.get("mixed_hosts") else f.get("platform", "rss")
                items.append(item(f"rss:{eid}", plat, f["name"], link, f"{title}\n{text}".strip(), pub,
                                  f.get("kind", "article"), title=title, tier=f.get("tier")))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{f['name']}: {str(e)[:80]}")
    if n_feeds:
        cov.ok("feeds", len(items), f"{n_feeds} feeds OK" + (f"; failed: {'; '.join(errors)}" if errors else ""))
    else:
        cov.error("feeds", "; ".join(errors) or "no feeds configured")
    return items


# ----------------------------------------------------------------------------- web search scoped to reddit.com (Brave Search API or Google Programmable Search)
def collect_search(cfg, cov, keys, since_dt):
    """Reddit threads via a search engine's index instead of Reddit's closed API: titles, snippets, dates and URLs
    (no scores or comment counts). Brave Search API when BRAVE_API_KEY is set; Google Programmable Search when
    GOOGLE_CSE_KEY and GOOGLE_CSE_CX are set; both if both are present. Free tiers cover this volume."""
    sc = cfg.get("search") or {}
    queries = sc.get("queries", [])
    brave_key, g_key, g_cx = keys
    if not queries:
        cov.skipped("search", "no search.queries configured")
        return []
    if not brave_key and not (g_key and g_cx):
        cov.skipped("search", "no BRAVE_API_KEY or GOOGLE_CSE_KEY/GOOGLE_CSE_CX")
        return []
    items, seen, notes = [], set(), []
    days = int(sc.get("days", 7))

    def add(url, title, snippet, when, engine):
        url = unwrap_redirect(url)
        if not url or url in seen:
            return
        seen.add(url)
        created = parse_dt(when) if when else None
        if created and created < since_dt:
            return
        items.append(item("search:" + hashlib.sha1(url.encode()).hexdigest()[:16], platform_for_url(url, "web"), f"{engine} search", url,
                          f"{title}\n{snippet}".strip(), created, "search_hit", title=title, tier="D",
                          tags=["search", "undated"] if not created else ["search"], extra={"engine": engine}))

    if brave_key:
        try:
            freshness = "pd" if days <= 1 else ("pw" if days <= 7 else "pm")
            for q in queries:
                data = get_json("https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": q, "freshness": freshness, "count": sc.get("count", 20), "text_decorations": "false"}),
                                headers={"X-Subscription-Token": brave_key})
                for r in (data.get("web") or {}).get("results", []) or []:
                    add(r.get("url", ""), strip_html(r.get("title", ""), 300), strip_html(r.get("description", ""), 600), r.get("page_age"), "Brave")
            notes.append(f"Brave: {len(queries)} queries")
        except Exception as e:  # noqa: BLE001
            notes.append(f"Brave error: {str(e)[:80]}")
    if g_key and g_cx:
        try:
            for q in queries:
                data = get_json("https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode({"key": g_key, "cx": g_cx, "q": q, "dateRestrict": f"d{days}", "sort": "date", "num": 10}))
                for r in data.get("items", []) or []:
                    meta = ((r.get("pagemap") or {}).get("metatags") or [{}])[0]
                    when = meta.get("article:published_time") or meta.get("og:updated_time")
                    add(r.get("link", ""), strip_html(r.get("title", ""), 300), strip_html(r.get("snippet", ""), 600), when, "Google")
            notes.append(f"Google: {len(queries)} queries")
        except Exception as e:  # noqa: BLE001
            notes.append(f"Google error: {str(e)[:80]}")
    cov.ok("search", len(items), "; ".join(notes))
    return items


# ----------------------------------------------------------------------------- Anthropic newsroom pages (no RSS exists)
A_RE = re.compile(r'<a\b[^>]*?href="([^"#?]+)[^"]*"[^>]*>(.*?)</a>', re.I | re.S)


PUBLISHED_RE = re.compile(r'(?:property|name)="article:published_time"\s+content="([^"]+)"|content="([^"]+)"\s+(?:property|name)="article:published_time"', re.I)
SKIP_SLUGS = {"announcements", "features", "safeguards", "security", "partnerships", "policy", "product", "research", "education", "societal-impacts",
              "interpretability", "alignment", "economics", "team", "customers", "events", "news", "engineering"}


def collect_pages(cfg, cov, prev_by_id, date_budget=8):
    """Anthropic's newsroom pages have no RSS: parse the article links, treat links not seen before as new, and read each new
    article's article:published_time so the item carries the real publication date. The first run only records a baseline."""
    items, errors = [], []
    for pg in cfg.get("pages", []):
        try:
            status, body, _ = http(pg["url"])
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            first_run = not any(it.get("platform") == pg.get("platform", "web") and it.get("source") == pg["name"] for it in prev_by_id.values())
            seen = set()
            base = urllib.parse.urlparse(pg["url"])
            for href, inner in A_RE.findall(body):
                if not href.startswith(pg["link_prefix"]) or href.rstrip("/") == pg["link_prefix"].rstrip("/"):
                    continue
                slug_parts = [p for p in href[len(pg["link_prefix"]):].split("/") if p]
                if not slug_parts or len(slug_parts) > 1 or slug_parts[0].lower() in SKIP_SLUGS:
                    continue
                full = f"{base.scheme}://{base.netloc}{href}"
                if full in seen:
                    continue
                seen.add(full)
                title = strip_html(inner, 200)
                if len(title.split()) < 2:
                    continue
                iid = "page:" + hashlib.sha1(full.encode()).hexdigest()[:16]
                prev = prev_by_id.get(iid)
                created, tags = (parse_dt(prev["created_at"]) if prev else NOW), []
                if prev is None:
                    tags.append("baseline" if first_run else "new")
                    if not first_run and date_budget > 0:
                        date_budget -= 1
                        try:
                            st, art, _ = http(full)
                            m = PUBLISHED_RE.search(art) if st == 200 else None
                            if m:
                                created = parse_dt(m.group(1) or m.group(2)) or NOW
                        except Exception:  # noqa: BLE001
                            pass
                items.append(item(iid, pg.get("platform", "web"), pg["name"], full, title, created, "article", title=title, tier=pg.get("tier", "A"), tags=tags))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pg['name']}: {str(e)[:80]}")
    n_new = sum(1 for it in items if "new" in it["tags"])
    note = f"{n_new} new article links" + ("; baseline recorded" if any("baseline" in it["tags"] for it in items) else "") + (f"; {'; '.join(errors)}" if errors else "")
    cov.ok("anthropic_pages", len(items), note) if items else cov.error("anthropic_pages", "; ".join(errors) or "nothing parsed")
    return items


# ----------------------------------------------------------------------------- watched pages (docs changelog, CHANGELOG.md, pricing)
def collect_watch_pages(cfg, state, cov):
    pages = dict(state.get("pages", {}))
    items, errors = [], []
    for wp in cfg.get("watch_pages", []):
        try:
            status, body, _ = http(wp["url"])
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            text = body if wp["url"].endswith((".md", ".txt")) else strip_html(re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I))
            lines = [l.strip() for l in re.split(r"(?<=[.!?])\s+|\n+", text) if len(l.strip()) > wp.get("min_len", 20)][:600]
            digest = hashlib.sha256(text.encode()).hexdigest()
            prev = pages.get(wp["url"])
            if prev and prev.get("hash") != digest:
                new_lines = [l for l in lines if l not in set(prev.get("lines", []))][:12]
                items.append(item("watch:" + hashlib.sha1((wp["url"] + digest).encode()).hexdigest()[:16], wp.get("platform", "web"), wp["name"], wp["url"],
                                  f"{wp['name']} changed. New text: " + " | ".join(new_lines)[:1000], NOW, "changelog", title=f"{wp['name']} changed", tier=wp.get("tier", "A"),
                                  tags=["tripwire"], extra={"new_lines": new_lines}))
            pages[wp["url"]] = {"hash": digest, "lines": lines, "checked_at": iso(NOW)}
        except Exception as e:  # noqa: BLE001
            errors.append(f"{wp['name']}: {str(e)[:80]}")
    state["pages"] = pages
    cov.ok("watch_pages", len(items), f"{len(cfg.get('watch_pages', []))} pages checked" + (f"; failed: {'; '.join(errors)}" if errors else ""))
    return items


# ----------------------------------------------------------------------------- GitHub releases + npm
MODEL_ID_RE = re.compile(r"\bclaude-[a-z0-9][a-z0-9.-]*\b", re.I)


def collect_github(cfg, cov, token, since_dt):
    items, errors = [], []
    hdr = {"Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    for repo in cfg.get("github_repos", []):
        try:
            for r in get_json(f"https://api.github.com/repos/{repo}/releases?per_page=15", headers=hdr):
                pub = parse_dt(r.get("published_at") or r.get("created_at"))
                if pub and pub < since_dt:
                    continue
                body = r.get("body") or ""
                ids = sorted(set(m.lower() for m in MODEL_ID_RE.findall(body)))
                items.append(item(f"gh:{repo}:{r['id']}", "github", repo, r["html_url"], f"{r.get('name') or r.get('tag_name')}\n{body}", pub, "release",
                                  title=r.get("name") or r.get("tag_name"), tier="A", tags=["model_ids"] if ids else [], extra={"tag": r.get("tag_name"), "model_ids": ids}))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{repo}: {str(e)[:80]}")
    cov.ok("github", len(items), "; ".join(errors)) if not errors or items else cov.error("github", "; ".join(errors))
    return items


def collect_npm(cfg, cov, since_dt):
    items, errors = [], []
    for pkg in cfg.get("npm_packages", []):
        try:
            data = get_json("https://registry.npmjs.org/" + urllib.parse.quote(pkg, safe="@"))
            tags = data.get("dist-tags") or {}
            for ver, ts in (data.get("time") or {}).items():
                if ver in ("created", "modified"):
                    continue
                pub = parse_dt(ts)
                if not pub or pub < since_dt:
                    continue
                label = ", ".join(k for k, v in tags.items() if v == ver)
                items.append(item(f"npm:{pkg}@{ver}", "npm", pkg, f"https://www.npmjs.com/package/{pkg}/v/{ver}", f"{pkg} {ver} published" + (f" ({label})" if label else ""),
                                  pub, "release", title=f"{pkg} {ver}", tier="A", extra={"version": ver, "dist_tags": label}))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pkg}: {str(e)[:80]}")
    cov.ok("npm", len(items), "; ".join(errors)) if not errors or items else cov.error("npm", "; ".join(errors))
    return items


# ----------------------------------------------------------------------------- App Store
def collect_appstore(cfg, cov, since_dt):
    ac = cfg.get("appstore") or {}
    if not ac.get("app_id"):
        cov.skipped("appstore", "no app id")
        return []
    try:
        data = get_json(f"https://itunes.apple.com/lookup?id={ac['app_id']}&country={ac.get('country', 'us')}")
        res = (data.get("results") or [{}])[0]
        pub = parse_dt(res.get("currentVersionReleaseDate"))
        if not res or not pub:
            raise RuntimeError("no result for app id")
        if pub < since_dt:
            cov.ok("appstore", 0, f"latest {res.get('version')} on {pub.date()} (outside window)")
            return []
        it = item(f"appstore:{ac['app_id']}:{res.get('version')}", "appstore", res.get("trackName", "App Store"), res.get("trackViewUrl", ""),
                  f"{res.get('trackName')} {res.get('version')}: {res.get('releaseNotes') or ''}", pub, "release", title=f"{res.get('trackName')} {res.get('version')}", tier="A")
        cov.ok("appstore", 1)
        return [it]
    except Exception as e:  # noqa: BLE001
        cov.error("appstore", e)
        return []


# ----------------------------------------------------------------------------- prediction markets
def collect_markets(cfg, cov):
    mc = cfg.get("markets") or {}
    rx = re.compile(mc.get("polymarket_regex", "anthropic|claude"), re.I)
    items, notes = [], []
    try:
        seen, n_ev = set(), 0
        for term in mc.get("polymarket_terms", ["anthropic", "claude"]):
            data = get_json("https://gamma-api.polymarket.com/public-search?" + urllib.parse.urlencode({"q": term, "limit_per_type": mc.get("polymarket_limit", 25)}))
            for ev in data.get("events", []) or []:
                n_ev += 1
                for m in ev.get("markets", []) or []:
                    hay = f"{ev.get('title', '')} {m.get('question', '')} {m.get('slug', '')}"
                    if m.get("closed") or m.get("id") in seen or not rx.search(hay):
                        continue
                    seen.add(m["id"])
                    try:
                        prices = json.loads(m.get("outcomePrices") or "[]")
                        outcomes = json.loads(m.get("outcomes") or "[]")
                    except ValueError:
                        prices, outcomes = [], []
                    prob = float(prices[0]) if prices else None
                    q = m.get("question") or m.get("groupItemTitle") or ev.get("title")
                    chg = m.get("oneDayPriceChange")
                    items.append(item(f"pm:{m['id']}", "polymarket", "Polymarket", f"https://polymarket.com/event/{ev.get('slug')}/{m.get('slug')}",
                                      f"{q} — {outcomes[0] if outcomes else 'Yes'} {round(prob * 100) if prob is not None else '?'}%"
                                      + (f" (1d {chg:+.0%})" if isinstance(chg, (int, float)) and chg else ""),
                                      parse_dt(m.get("createdAt")), "market", title=q, tier="market",
                                      metrics={"volume24h": m.get("volume24hr"), "liquidity": m.get("liquidityNum"), "volume": m.get("volumeNum")},
                                      extra={"probability": prob, "end_date": m.get("endDate"), "outcomes": outcomes, "prices": prices, "event": ev.get("title"),
                                             "change_1d": chg, "change_1w": m.get("oneWeekPriceChange")}))
        notes.append(f"Polymarket: {n_ev} events searched")
    except Exception as e:  # noqa: BLE001
        notes.append(f"Polymarket error: {str(e)[:80]}")
    try:
        seen, detail_budget = set(), 20
        for term in mc.get("manifold_terms", []):
            for m in get_json("https://api.manifold.markets/v0/search-markets?" + urllib.parse.urlencode({"term": term, "filter": "open", "limit": 50})):
                if m["id"] in seen or m.get("outcomeType") not in (None, "BINARY", "MULTIPLE_CHOICE"):
                    continue
                seen.add(m["id"])
                prob, answers = m.get("probability"), []
                if m.get("outcomeType") == "MULTIPLE_CHOICE" and detail_budget > 0:
                    detail_budget -= 1
                    try:
                        full = get_json(f"https://api.manifold.markets/v0/market/{m['id']}")
                        answers = sorted([{"text": a.get("text"), "probability": a.get("probability")} for a in full.get("answers", []) if a.get("probability") is not None],
                                         key=lambda a: -a["probability"])[:4]
                    except Exception:  # noqa: BLE001
                        answers = []
                summary = f"{round(prob * 100)}%" if prob is not None else ("; ".join(f"{a['text']} {round(a['probability'] * 100)}%" for a in answers) or "?")
                items.append(item(f"mf:{m['id']}", "manifold", "Manifold", m.get("url", ""), f"{m.get('question')} — {summary}",
                                  parse_dt(m.get("createdTime")), "market", title=m.get("question"), tier="market",
                                  metrics={"volume24h": m.get("volume24Hours"), "volume": m.get("volume"), "traders": m.get("uniqueBettorCount")},
                                  extra={"probability": prob, "answers": answers, "close_time": m.get("closeTime")}))
        notes.append("Manifold OK")
    except Exception as e:  # noqa: BLE001
        notes.append(f"Manifold error: {str(e)[:80]}")
    cov.ok("markets", len(items), "; ".join(notes))
    return items


# ----------------------------------------------------------------------------- tripwires: registries, status components, careers, sitemaps
# Machine-readable places where an unannounced model, product, office or page tends to appear before the announcement. Each keeps a little
# state so only changes become items; the first run records a baseline and emits nothing (except sources that carry their own timestamps).
def _per_million(v):
    try:
        return f"${float(v) * 1e6:g}/M"
    except (TypeError, ValueError):
        return "?"


def collect_registry(cfg, state, cov, since_dt):
    rc = cfg.get("registry") or {}
    if not rc:
        cov.skipped("registry", "not configured")
        return []
    items, notes, fails = [], [], 0
    orc = rc.get("openrouter")
    if orc is not None:
        try:
            rx = re.compile(orc.get("regex", r"anthropic|claude|cloaked|stealth"), re.I)
            data = get_json(orc.get("url", "https://openrouter.ai/api/v1/models")).get("data") or []
            hits = 0
            for m in data:
                if not rx.search(f"{m.get('id', '')} {m.get('name', '')} {(m.get('description') or '')[:400]}"):
                    continue
                hits += 1
                created = parse_dt(m.get("created"))
                if not created or created < since_dt:
                    continue
                pr = m.get("pricing") or {}
                known = bool(re.search(r"anthropic|claude", f"{m.get('id', '')} {m.get('name', '')}", re.I))
                bits = [f"OpenRouter lists {m.get('id')} ({m.get('name')})" + ("" if known else "; provider not named (cloaked or stealth model)")]
                if m.get("context_length"):
                    bits.append(f"context {m['context_length']}")
                if pr.get("prompt") is not None:
                    bits.append(f"prompt {_per_million(pr.get('prompt'))}, completion {_per_million(pr.get('completion'))}")
                text = "; ".join(bits) + f". {strip_html(m.get('description'), 240)}"
                items.append(item(f"or:{m.get('id')}", "registry", "OpenRouter models", f"https://openrouter.ai/{m.get('id')}", text, created, "model_listed",
                                  title=m.get("name") or m.get("id"), tier="B", tags=["tripwire"], extra={"model_ids": [m.get("id")]}))
            notes.append(f"OpenRouter {len(data)} models ({hits} matched)")
        except Exception as e:  # noqa: BLE001
            fails += 1
            notes.append(f"OpenRouter error: {str(e)[:80]}")
    llc = rc.get("litellm")
    if llc is not None:
        try:
            data = get_json(llc.get("url", "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"))
            rx = re.compile(llc.get("regex", r"claude|anthropic"), re.I)
            keys = sorted(k for k in data if isinstance(k, str) and k != "sample_spec" and rx.search(k))
            prev = state.get("litellm_keys")
            n_new = 0
            if prev is not None:
                known = set(prev)
                for k in keys:
                    if k in known:
                        continue
                    n_new += 1
                    spec = data.get(k) if isinstance(data.get(k), dict) else {}
                    bits = [f"provider {spec['litellm_provider']}"] if spec.get("litellm_provider") else []
                    if spec.get("max_input_tokens"):
                        bits.append(f"max input {spec['max_input_tokens']}")
                    if spec.get("input_cost_per_token") is not None:
                        bits.append(f"{_per_million(spec.get('input_cost_per_token'))} in, {_per_million(spec.get('output_cost_per_token'))} out")
                    text = f"LiteLLM's model list gained {k}" + (f" ({'; '.join(bits)})" if bits else "")
                    items.append(item(f"litellm:{k}", "registry", "LiteLLM model list", "https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json",
                                      text, NOW, "model_listed", title=k, tier="B", tags=["tripwire"], extra={"model_ids": [k]}))
            state["litellm_keys"] = sorted(set(prev or []) | set(keys))[-3000:]
            notes.append(f"LiteLLM {len(keys)} claude/anthropic keys" + (f", {n_new} new" if prev is not None else " (baseline)"))
        except Exception as e:  # noqa: BLE001
            fails += 1
            notes.append(f"LiteLLM error: {str(e)[:80]}")
    if fails and fails == sum(x is not None for x in (orc, llc)):
        cov.error("registry", "; ".join(notes))
    else:
        cov.ok("registry", len(items), "; ".join(notes))
    return items


def collect_statuspage(cfg, cov, since_dt):
    """Atlassian Statuspage components: a new component (a product, model or surface) is often added before it is announced."""
    sp = cfg.get("statuspage") or {}
    if not sp.get("components_url"):
        cov.skipped("statuspage", "not configured")
        return []
    try:
        comps = get_json(sp["components_url"]).get("components") or []
        names = {c.get("id"): c.get("name") for c in comps}
        items = []
        for c in comps:
            created = parse_dt(c.get("created_at"))
            if not created or created < since_dt:
                continue
            group = names.get(c.get("group_id"))
            text = (f"Status page added component '{c.get('name')}'" + (f" under '{group}'" if group else "")
                    + (f": {strip_html(c.get('description'), 160)}" if c.get("description") else ""))
            items.append(item(f"sp:{c.get('id')}", "status", sp.get("name", "Status components"), sp.get("page_url", ""), text, created, "component_added",
                              title=f"New status component: {c.get('name')}", tier="A", tags=["tripwire"], extra={"component": c.get("name")}))
        cov.ok("statuspage", len(items), f"{len(comps)} components")
        return items
    except Exception as e:  # noqa: BLE001
        cov.error("statuspage", e)
        return []


def _job_locations(j):
    return {p.strip() for p in re.split(r"[|;]", (j.get("location") or {}).get("name") or "") if p.strip()}


def collect_careers(cfg, state, cov):
    """Greenhouse job board: new postings that match a watch pattern (IR, securities, silicon, hardware, country leads...) or open a new location."""
    cc = cfg.get("careers") or {}
    board = cc.get("greenhouse_board")
    if not board:
        cov.skipped("careers", "not configured")
        return []
    try:
        jobs = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs").get("jobs") or []
        rx = re.compile(cc.get("watch_regex") or r"$^", re.I)
        prev_ids, prev_locs = state.get("job_ids"), state.get("job_locations")
        known, known_locs = set(prev_ids or []), set(prev_locs or [])
        items, n_new = [], 0
        if prev_ids is not None:
            for j in jobs:
                if j.get("id") in known:
                    continue
                n_new += 1
                title = j.get("title") or ""
                new_locs = sorted(_job_locations(j) - known_locs)
                if rx.search(title) or new_locs:
                    loc = (j.get("location") or {}).get("name") or "location n/a"
                    items.append(item(f"job:{j.get('id')}", "careers", f"{cc.get('name', board)} careers", j.get("absolute_url") or f"https://job-boards.greenhouse.io/{board}",
                                      f"New opening: {title} ({loc})" + (f"; first posting in {', '.join(new_locs)}" if new_locs else ""),
                                      parse_dt(j.get("first_published")) or parse_dt(j.get("updated_at")) or NOW, "job_posted", title=title, tier="A",
                                      tags=["tripwire"], extra={"location": loc}))
        state["job_ids"] = sorted(known | {j["id"] for j in jobs if j.get("id") is not None})[-3000:]
        state["job_locations"] = sorted(known_locs | set().union(*[_job_locations(j) for j in jobs]))
        cov.ok("careers", len(items), f"{len(jobs)} openings; " + (f"{n_new} new since last run" if prev_ids is not None else "baseline recorded"))
        return items
    except Exception as e:  # noqa: BLE001
        cov.error("careers", e)
        return []


LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>(?:\s*<lastmod>\s*([^<\s]+)\s*</lastmod>)?", re.I)


def sitemap_urls(url, child_regex=None, max_children=6):
    status, body, _ = http(url)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    pairs = LOC_RE.findall(body)
    if "<sitemapindex" not in body[:3000].lower():
        return {html.unescape(u): lm for u, lm in pairs}
    crx = re.compile(child_regex) if child_regex else None
    out = {}
    for loc, _lm in [p for p in pairs if not crx or crx.search(p[0])][:max_children]:
        st, sub, _ = http(html.unescape(loc))
        if st == 200:
            out.update({html.unescape(u): lm for u, lm in LOC_RE.findall(sub)})
    return out


def collect_sitemaps(cfg, state, cov):
    """New URLs in a site's sitemap show up when a page is published, which can be before anything links to it. The slug alone is often a headline."""
    sms = cfg.get("sitemaps") or []
    if not sms:
        cov.skipped("sitemaps", "not configured")
        return []
    store, items, notes, fails = dict(state.get("sitemaps") or {}), [], [], 0
    for sm in sms:
        try:
            inc = re.compile(sm.get("include") or ".")
            exc = re.compile(sm["exclude"]) if sm.get("exclude") else None
            urls = {u: lm for u, lm in sitemap_urls(sm["url"], sm.get("child_regex"), sm.get("max_children", 6)).items() if inc.search(u) and not (exc and exc.search(u))}
            by_hash = {hashlib.sha1(u.encode()).hexdigest()[:10]: u for u in urls}
            prev = store.get(sm["name"])
            if prev is not None:
                known = set(prev)
                for h, u in by_hash.items():
                    if h in known:
                        continue
                    slug = u.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
                    items.append(item(f"sitemap:{h}", sm.get("platform", "sitemap"), sm["name"], u, f"New URL in the {sm['name']}: {u} (slug: {slug})", NOW, "new_url",
                                      title=slug, tier=sm.get("tier", "A"), tags=["tripwire", "new"], extra={"lastmod": urls[u]}))
            store[sm["name"]] = sorted(set(prev or []) | set(by_hash))[-8000:]
            notes.append(f"{sm['name']}: {len(by_hash)} URLs" + ("" if prev is not None else " (baseline)"))
        except Exception as e:  # noqa: BLE001
            fails += 1
            notes.append(f"{sm.get('name')}: {str(e)[:80]}")
    state["sitemaps"] = store
    if fails == len(sms):
        cov.error("sitemaps", "; ".join(notes))
    else:
        cov.ok("sitemaps", len(items), "; ".join(notes))
    return items


# ----------------------------------------------------------------------------- merge, digest, outputs
METRIC_KEYS = ("likes", "reposts", "replies", "quotes", "views", "score", "comments", "points", "volume24h")


def merge(prev_items, new_items, window_days):
    cutoff = NOW - dt.timedelta(days=window_days)
    merged = {}
    for it in prev_items:
        fs = parse_dt(it.get("first_seen")) or parse_dt(it.get("created_at")) or NOW
        if fs >= cutoff:
            merged[it["id"]] = it
    for it in new_items:
        old = merged.get(it["id"])
        if old:
            hist = old.get("metrics_history") or []
            snap = {k: v for k, v in (old.get("metrics") or {}).items() if k in METRIC_KEYS and v is not None}
            if snap:
                snap["at"] = old.get("last_seen") or old.get("first_seen")
                hist.append(snap)
            it["metrics_history"] = hist[-10:]
            it["first_seen"] = old.get("first_seen") or old.get("created_at")
            if not it["metrics"] or all(v is None for v in it["metrics"].values()):
                it["metrics"] = old.get("metrics") or {}
            for tag in ("baseline", "new"):               # provenance tags persist from the run that first saw the item
                if tag in (old.get("tags") or []) and tag not in it["tags"]:
                    it["tags"].append(tag)
            if old.get("created_at") and it["platform"] in ("anthropic.com",) and "new" in it["tags"]:
                it["created_at"] = min(it["created_at"], old["created_at"])   # keep the dated created_at found when the link was new
        else:
            it["first_seen"] = iso(NOW)
            it["metrics_history"] = []
        it["last_seen"] = iso(NOW)
        merged[it["id"]] = it
    out = list(merged.values())
    out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return out


# ----------------------------------------------------------------------------- early-detection flags, ranking and the size-budgeted digest
# The dashboard task reads the digest through a fetch tool that truncates at roughly 25K tokens, so the digest is built to a character budget
# (digest_budget_chars) instead of a fixed item count: items are ranked by an early-detection priority, lower-ranked items get shorter text,
# and whatever still does not fit goes to signals_digest_more.json. The last key of the file is "complete": true, so a truncated read is detectable.
PRIMARY_PLATFORMS = {"status", "anthropic.com", "docs.claude.com", "claude.com", "github", "npm", "appstore", "partner", "registry", "careers", "sitemap"}
ROUTINE_PRIMARY = {"github", "npm"}                       # version bumps: primary evidence but low information unless a model id or flag is attached
MARKET_PLATFORMS = {"polymarket", "manifold"}
SOCIAL_PLATFORMS = {"x", "reddit", "hn", "bluesky"}
# per-platform ceilings; they apply to unflagged items only, so a flagged post is never crowded out by volume
DIGEST_CAPS = {"x": 60, "reddit": 30, "hn": 20, "bluesky": 20, "substack": 20, "blog": 15, "newsletter": 12, "press": 25, "web": 12, "github": 12, "npm": 6}
TIER_BONUS = {"A": 30, "B": 20, "C": 12, "D": 6}
STRONG_FLAGS = {"leak", "newid", "trip", "rising"}
DEFAULT_TEXT_BANDS = [[25, 480], [80, 300], [150, 180], [100000, 110]]    # [rank below, text characters]

LEAK_RE = re.compile(
    r"\b(?:leak(?:s|ed|ing|er|ers)?|rumou?r(?:s|ed)?|spotted|sighting|unreleased|unannounced|imminent|code[- ]?names?|feature[- ]?flags?|"
    r"a/b[- ]?test(?:s|ing)?|early[- ]access|eap|staging|stealth|cloaked|found (?:in|on|inside)|hidden (?:in|string|flag|model)|"
    r"people familiar|according to (?:a |two |three |several )?(?:sources?|people)|sources? (?:say|said|tell|told|familiar)|reportedly|"
    r"scoop|exclusive|in talks|preparing to|set to (?:launch|release|announce|unveil|file|debut)|expected to|as soon as|"
    r"could (?:launch|release|ship|announce|debut)|about to|teas(?:er|ed|ing)|incoming|dropping (?:soon|today|tomorrow|next)|"
    r"rolling out|model[- ]ids?|system card|checkpoint)\b", re.I)
SOON_RE = re.compile(
    r"\b(?:soon|coming|upcoming|next (?:week|month)|this week|tomorrow|tonight|new model|next[- ]gen|testing|alpha|beta|preview|waitlist|"
    r"invite[- ]only|ipo|s-1|valuation|tender offer)\b", re.I)
ID_RE = re.compile(r"\b(?:anthropic[/.])?claude[-_][a-z0-9]+(?:[-_.][a-z0-9]+)*|\bclaude ?\d+(?:[.-]\d+)*\b|\b(?:opus|sonnet|haiku|fable|mythos)[ -]?v?\d+(?:[.-]\d+)*\b", re.I)
HISTORIC_IDS = {"claude-1", "claude-2", "claude-2-1", "claude-3", "claude-3-5", "claude-3-7", "claude-4", "claude-4-1", "claude-4-5", "claude-instant", "claude-code", "claude-ai",
                "claude-agent-sdk", "claude-code-action", "opus-3", "opus-4", "opus-4-1", "opus-4-5", "sonnet-3", "sonnet-3-5", "sonnet-3-7", "sonnet-4", "sonnet-4-5",
                "haiku-3", "haiku-3-5", "haiku-4-5"}      # names that predate the collector, so a mention of them is never a "new identifier"
FAMILY_RE = re.compile(r"(?:(\d+(?:-\d+)*)-)?(opus|sonnet|haiku|fable|mythos)(?:-(\d+(?:-\d+)*))?(?:-.*)?")

LEGEND = {
    "plat": "platform", "by": "author handle (x, reddit, hn, bluesky)", "src": "outlet or source name (everything else)",
    "tier": "source credibility prior: A official, B trusted tracker or analyst, C reporter or aggregator, D anonymous; absent for markets",
    "at": "created, UTC", "sid": "x only: the post URL is https://x.com/<by>/status/<sid>", "url": "link (all platforms except x)",
    "text": "headline or post text, trimmed harder for lower-ranked items", "metrics": "nonzero engagement counts", "fol": "author followers (x, tiers C/D only)",
    "flags": "leak = rumor or leak vocabulary; soon = forward-looking vocabulary; id = a Claude model id or name in the text; newid = an identifier not seen in the earlier days; "
             "rising = engagement climbing fast; trip = tripwire (registry, status component, careers, sitemap, watched page changed)",
    "corr": "distinct authors who mentioned the same new identifier", "cites": "outbound link (non-x)", "prob": "market probability", "end": "market end date",
    "ids": "model ids found", "ver": "package version", "new_lines": "new text on a watched page",
}


def minute(s):
    d = parse_dt(s)
    return d.strftime("%Y-%m-%dT%H:%MZ") if d else s


def trim(s, n):
    s = " ".join((s or "").split())
    if len(s) <= n:
        return s
    cut = s[:max(n - 1, 1)]
    head = cut.rsplit(" ", 1)[0]
    return (head if len(head) >= 0.6 * len(cut) else cut).rstrip(" ,;:-") + "…"


def norm_id(tok):
    """One canonical spelling per model: 'claude-opus-4-5-20251101', 'anthropic/claude-opus-4.5' and 'Opus 4.5' all become 'opus-4-5'."""
    t = re.sub(r"^(?:(?:us|eu|apac|global|jp|au|ca)\.)?(?:anthropic|bedrock|vertex[-_]ai|azure(?:_ai)?|openrouter)[/.]", "", str(tok).strip().lower())
    t = re.sub(r"[\s_@]+", "-", t).strip(".-")
    body = re.sub(r"-(?:20\d{6}|latest|v\d+(?::\d+)?)(?=-|$)", "", t[len("claude-"):] if t.startswith("claude-") else t).replace(".", "-")
    m = FAMILY_RE.fullmatch(body)
    if m:
        return f"{m.group(2)}-{m.group(3) or m.group(1) or ''}".strip("-")
    return t.replace(".", "-") if re.fullmatch(r"claude-\d+(?:[.-]\d+)*", t) else t


def ids_in(text):
    return {norm_id(m.group(0)) for m in ID_RE.finditer(text or "")}


def text_of(it):
    return f"{it.get('title') or ''} {it.get('text') or ''}"


def velocity(it):
    """Engagement points per hour: from the last two metric snapshots, else the lifetime average of a young item."""
    now_e = engagement(it)
    hist = it.get("metrics_history") or []
    if hist:
        t0, t1 = parse_dt(hist[-1].get("at")), parse_dt(it.get("last_seen")) or NOW
        if t0 and t1 > t0:
            return max(0.0, now_e - engagement({"metrics": hist[-1]})) / max((t1 - t0).total_seconds() / 3600.0, 0.5)
    created = parse_dt(it.get("created_at")) or NOW
    hrs = max((NOW - created).total_seconds() / 3600.0, 1.0)
    return now_e / hrs if hrs <= 24 else 0.0


def annotate_items(items, state, recent_hours=48):
    """Early-detection flags, set in place on every item: leak / soon vocabulary, model-id mentions, identifiers first seen inside the digest window,
    cross-author corroboration of those, rising engagement and tripwires. Identifiers older than the window are remembered in state['seen_ids']."""
    cutoff = NOW - dt.timedelta(hours=recent_hours)
    seen_old = dict.fromkeys(sorted(HISTORIC_IDS) + list(state.get("seen_ids") or []))
    toks, recent_authors = {}, {}
    for it in items:
        t = ids_in(text_of(it)) | {norm_id(i) for i in (it.get("extra") or {}).get("model_ids") or []}
        toks[id(it)] = t
        created = parse_dt(it.get("created_at")) or NOW
        if created < cutoff:
            for tok in t:
                seen_old.setdefault(tok)
        elif it.get("kind") != "repost":                     # a repost repeats its origin, so it is not independent corroboration
            for tok in t:
                recent_authors.setdefault(tok, set()).add((it.get("author") or it.get("source") or "").lower())
    state["seen_ids"] = list(seen_old)[-1500:]
    for it in items:
        text, flags, t = text_of(it), [], toks[id(it)]
        created = parse_dt(it.get("created_at")) or NOW
        if LEAK_RE.search(text):
            flags.append("leak")
        elif SOON_RE.search(text):
            flags.append("soon")
        if t:
            flags.append("id")
        new = [tok for tok in t if tok not in seen_old] if created >= cutoff else []
        if new:
            flags.append("newid")
        corr = max((len(recent_authors.get(tok, ())) for tok in new), default=0)
        if corr >= 2:
            it["corr"] = corr
        else:
            it.pop("corr", None)
        if "tripwire" in (it.get("tags") or []):
            flags.append("trip")
        if velocity(it) >= 6 and engagement(it) >= 20:
            flags.append("rising")
        it["flags"] = flags


def priority(it):
    plat, f, tags = it["platform"], set(it.get("flags") or []), it.get("tags") or []
    s = 0.0
    if plat in ROUTINE_PRIMARY:
        s += 60
    elif plat in PRIMARY_PLATFORMS:
        s += 100
    elif plat in MARKET_PLATFORMS:
        s += 70
    s += TIER_BONUS.get(it.get("tier"), 0) + (15 if "always" in tags else 0)
    s += 25 * ("leak" in f) + 8 * ("soon" in f) + 12 * ("id" in f) + 45 * ("newid" in f) + 60 * ("trip" in f) + 15 * ("rising" in f)
    s += min(30, 15 * max(0, int(it.get("corr") or 0) - 1))
    s += min(35, math.log2(1 + engagement(it)) * 4)
    fol = (it.get("metrics") or {}).get("followers")
    if fol:
        s += min(10, math.log10(1 + fol) * 2)
    age_h = max(0.0, (NOW - (parse_dt(it.get("created_at")) or NOW)).total_seconds() / 3600.0)
    s += 10 if age_h < 12 else 5 if age_h < 24 else 0
    if it.get("kind") == "repost":
        s -= 40
    elif it.get("kind") == "reply":
        s -= 5
    if plat == "x" and "search" in tags and not f and "always" not in tags:
        s -= 5
    return s


def engagement(it):
    m = it.get("metrics") or {}
    return sum(float(m.get(k) or 0) * w for k, w in (("likes", 1), ("reposts", 3), ("quotes", 3), ("replies", 0.5), ("score", 1), ("comments", 1), ("points", 2)))


def compact(it, text_cap):
    """One digest line: short keys, empty fields dropped, X post URLs rebuilt from by + sid."""
    plat = it["platform"]
    e = {"plat": plat}
    if plat in SOCIAL_PLATFORMS:
        e["by"] = it.get("author") or it.get("source")
    else:
        e["src"] = it.get("source")
    if it.get("tier"):
        e["tier"] = it["tier"]
    e["at"] = minute(it.get("created_at"))
    if it.get("kind") not in (None, "post", "story", "article"):
        e["kind"] = it["kind"]
    if plat == "x" and str(it.get("id", "")).startswith("x:"):
        e["sid"] = it["id"][2:]
    else:
        e["url"] = it.get("url")
    text = " ".join((it.get("text") or "").split())
    title = " ".join((it.get("title") or "").split())
    if title and text.startswith(title):                      # headline-first feeds repeat the title at the start of the text
        text = text[len(title):].lstrip(" \n:-—")
        text = f"{title}. {text}" if text else title
    e["text"] = trim(text, text_cap)
    m = {k: v for k, v in (it.get("metrics") or {}).items() if v and k not in ("views", "followers", "liquidity", "volume")}
    if m:
        e["metrics"] = m
    fol = (it.get("metrics") or {}).get("followers")
    if plat == "x" and fol and it.get("tier") in (None, "C", "D"):
        e["fol"] = fol
    if it.get("flags"):
        e["flags"] = it["flags"]
    if it.get("corr"):
        e["corr"] = it["corr"]
    if plat not in ("x",) and it.get("cites"):
        e["cites"] = it["cites"][0]
    x = it.get("extra") or {}
    if x.get("probability") is not None:
        e["prob"] = round(x["probability"], 3)
    end = x.get("end_date") or x.get("close_time")
    if end:
        end_dt = parse_dt(end)
        e["end"] = end_dt.strftime("%Y-%m-%d") if end_dt else str(end)[:10]
    if x.get("model_ids"):
        e["ids"] = x["model_ids"][:6]
    if x.get("version"):
        e["ver"] = x["version"]
    if x.get("new_lines"):
        e["new_lines"] = [trim(l, 140) for l in x["new_lines"][:6]]
    return e


def _cj(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def dumps_lines(head, items, tail=None):
    """Plain valid JSON with one line per header key and per item: compact, diff-friendly, readable by a person and by a fetch tool."""
    parts = [f" {json.dumps(k)}:{_cj(v)}" for k, v in head.items()]
    parts.append(' "items":[\n' + ",\n".join(_cj(it) for it in items) + "\n ]")
    parts += [f" {json.dumps(k)}:{_cj(v)}" for k, v in (tail or {}).items()]
    return "{\n" + ",\n".join(parts) + "\n}\n"


def dumps_digest(d):
    head = {k: v for k, v in d.items() if k not in ("items", "complete") and not k.startswith("_")}
    return dumps_lines(head, d["items"], {"complete": True})


def digest_candidates(items, cfg):
    kw = re.compile(cfg["keywords"], re.I)
    cn = re.compile(r"\b(?:" + "|".join(re.escape(c) for c in cfg["codenames"]) + r")\b", re.I) if cfg.get("codenames") else None
    cutoff = NOW - dt.timedelta(hours=cfg.get("digest_hours", 48))
    week = NOW - dt.timedelta(days=cfg.get("window_days", 7))
    out = []
    for it in items:
        plat, tags = it["platform"], it.get("tags") or []
        created = parse_dt(it.get("created_at")) or NOW
        seen_at = max(created, parse_dt(it.get("first_seen")) or created)
        if "baseline" in tags:
            continue                                       # undated links recorded on the collector's first run
        if plat in PRIMARY_PLATFORMS or plat in MARKET_PLATFORMS:
            keep = seen_at >= week
        else:
            text = text_of(it)
            curated = "always" in tags and it.get("tier") in ("A", "B", None)    # official accounts and trusted trackers: every post
            keep = seen_at >= cutoff and bool(curated or kw.search(text) or (cn and cn.search(text)) or "id" in (it.get("flags") or []))
        if keep:
            out.append(it)
    return out


def _fit(ranked, avail, bands):
    """Compact entries for as many of `ranked` (best first) as fit in `avail` characters; lower ranks get shorter text before anything is dropped."""
    top = bands[0][0]
    entries, total = [], 0
    for shrink in (1.0, 0.75, 0.55):
        entries = []
        for i, it in enumerate(ranked):
            cap = next(c for upto, c in bands if i < upto)
            if i >= top:
                cap = max(80, int(cap * shrink))
            entries.append(compact(it, cap))
        total = sum(len(_cj(e)) + 2 for e in entries)
        if total <= avail:
            return entries
    while entries and total > avail:
        total -= len(_cj(entries.pop())) + 2
    return entries


def make_digest(items, cfg, cov, state):
    annotate_items(items, state, cfg.get("digest_hours", 48))
    cands = digest_candidates(items, cfg)
    cands.sort(key=lambda it: (priority(it), it.get("created_at", "")), reverse=True)
    caps = cfg.get("digest_caps") or DIGEST_CAPS
    ranked, capped, seen_n = [], [], {}
    for it in cands:
        plat = it["platform"]
        weak = not (set(it.get("flags") or []) & STRONG_FLAGS) and "always" not in (it.get("tags") or []) and plat not in MARKET_PLATFORMS
        if weak and plat in caps and seen_n.get(plat, 0) >= caps[plat]:
            capped.append(it)
            continue
        seen_n[plat] = seen_n.get(plat, 0) + 1
        ranked.append(it)
    budget = int(cfg.get("digest_budget_chars", 56000))
    bands = cfg.get("digest_text_caps") or DEFAULT_TEXT_BANDS

    def build(n_keep, entries):
        kept, dropped = ranked[:n_keep], ranked[n_keep:] + capped
        counts, over = {}, {}
        for it in kept:
            counts[it["platform"]] = counts.get(it["platform"], 0) + 1
        for it in dropped:
            over[it["platform"]] = over.get(it["platform"], 0) + 1
        d = {"schema": "signals_digest/2", "lab": cfg["lab"], "collected_at": iso(NOW), "digest_hours": cfg.get("digest_hours", 48),
             "read_me": "Items are ranked best first. This file is complete only if the last key is \"complete\":true; otherwise your fetch was truncated, so say so. "
                        "Items that did not fit are in signals_digest_more.json (same format).",
             "legend": LEGEND, "n_items": len(entries), "overflow": {"n": len(dropped), "by_plat": over, "file": "signals_digest_more.json"},
             "budget": {"chars_limit": budget}, "coverage": cov.rows, "counts": counts, "x_reads_last_run": state.get("x_reads_last_run"),
             "items": entries, "_more": dropped}
        return d, kept, dropped

    probe, _, _ = build(0, [])
    plats = sorted({it["platform"] for it in ranked + capped})
    probe["overflow"] = {"n": 99999, "by_plat": {p: 9999 for p in plats}, "file": "signals_digest_more.json"}
    probe["counts"] = {p: 9999 for p in plats}
    probe["budget"] = {"chars_limit": budget, "chars_used": budget}
    reserve = len(dumps_digest(probe)) + 200
    entries = _fit(ranked, budget - reserve, bands)
    digest, kept, dropped = build(len(entries), entries)
    while len(dumps_digest(digest)) > budget and digest["items"]:                  # safety net: the reserve was an estimate
        entries.pop()
        digest, kept, dropped = build(len(entries), entries)
    digest["budget"]["chars_used"] = budget                                          # placeholder with the same digit count as the final figure
    digest["budget"]["chars_used"] = len(dumps_digest(digest))
    more = sorted(dropped, key=lambda it: (priority(it), it.get("created_at", "")), reverse=True)[:int(cfg.get("digest_more_max_items", 400))]
    digest["_more"] = [compact(it, 240) for it in more]
    return digest


def plain_digest(items, cfg, cov, state):
    """Last-resort digest used only if ranking fails, so a bug in the ranking can never stop the data from updating."""
    cutoff = NOW - dt.timedelta(hours=cfg.get("digest_hours", 48))
    pool = [it for it in items if "baseline" not in (it.get("tags") or []) and (parse_dt(it.get("created_at")) or NOW) >= cutoff]
    pool.sort(key=lambda it: it.get("created_at", ""), reverse=True)
    rows = [{"plat": it.get("platform"), "src": it.get("author") or it.get("source"), "at": minute(it.get("created_at")), "url": it.get("url"),
             "text": trim(it.get("text"), 200)} for it in pool[:150]]
    return {"schema": "signals_digest/2", "lab": cfg["lab"], "collected_at": iso(NOW), "digest_hours": cfg.get("digest_hours", 48),
            "read_me": "Plain fallback digest: ranking failed this run (see coverage.digest). Newest first, no flags. This file is complete only if the last key is \"complete\":true.",
            "n_items": len(rows), "overflow": {"n": max(0, len(pool) - len(rows)), "by_plat": {}, "file": "signals_digest_more.json"},
            "budget": {"chars_limit": int(cfg.get("digest_budget_chars", 56000))}, "coverage": cov.rows, "counts": {},
            "x_reads_last_run": state.get("x_reads_last_run"), "items": rows, "_more": []}


def make_digest_safely(items, cfg, cov, state):
    try:
        return make_digest(items, cfg, cov, state)
    except Exception as e:  # noqa: BLE001
        print(f"  ! digest ranking failed ({e}); writing a plain digest", file=sys.stderr)
        cov.error("digest", e)
        return plain_digest(items, cfg, cov, state)


def dumps_more(digest, cfg):
    d = {"schema": "signals_digest_more/2", "lab": cfg["lab"], "collected_at": digest["collected_at"], "read_me": "Items that did not fit in signals_digest.json, ranked best first; same keys (see the legend there).",
         "n_items": len(digest["_more"])}
    return dumps_lines(d, digest["_more"], {"complete": True})




def upload_gist(gist_id, token, files):
    body = json.dumps({"files": {name: {"content": content} for name, content in files.items()}})
    status, resp, _ = http(f"https://api.github.com/gists/{gist_id}", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
                           data=body, method="PATCH", accept_json=True)
    if status != 200:
        raise RuntimeError(f"gist HTTP {status}: {resp[:200]}")
    return json.loads(resp)


def create_gist(token):
    body = json.dumps({"description": "Anthropic signals collector output", "public": True,
                       "files": {"signals_digest.json": {"content": json.dumps({"schema": "signals_digest/2", "items": []})}}})
    status, resp, _ = http("https://api.github.com/gists", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
                           data=body, method="POST", accept_json=True)
    if status != 201:
        raise RuntimeError(f"gist HTTP {status}: {resp[:200]}")
    g = json.loads(resp)
    raw = g["files"]["signals_digest.json"]["raw_url"]
    stable = re.sub(r"/raw/[0-9a-f]{40}/", "/raw/", raw)
    print(f"GIST_ID={g['id']}\nraw digest URL (always latest): {stable}")
    return g


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "sources.json"))
    ap.add_argument("--previous", default=os.path.join(HERE, "data", "signals_raw.json"), help="previous signals_raw.json (path or URL)")
    ap.add_argument("--out", default=os.path.join(HERE, "data"))
    ap.add_argument("--no-x", action="store_true")
    ap.add_argument("--no-reddit", action="store_true")
    ap.add_argument("--no-tripwires", action="store_true", help="skip the registry, status-component, careers and sitemap tripwires")
    ap.add_argument("--create-gist", action="store_true")
    ap.add_argument("--max-seconds", type=int, default=900, help="stop starting new requests after this many seconds")
    args = ap.parse_args()
    DEADLINE[0] = time.time() + args.max_seconds

    if args.create_gist:
        create_gist(os.environ["GIST_TOKEN"])
        return 0

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    prev = {"items": [], "state": {}}
    try:
        if args.previous.startswith("http"):
            prev = get_json(args.previous)
        elif os.path.exists(args.previous):
            with open(args.previous, encoding="utf-8") as fh:
                prev = json.load(fh)
    except Exception as e:  # noqa: BLE001
        print(f"  ! could not load previous output ({e}); starting fresh", file=sys.stderr)
    state = dict(prev.get("state") or {})
    prev_items = prev.get("items") or []
    prev_by_id = {it["id"]: it for it in prev_items}
    since_dt = NOW - dt.timedelta(days=cfg.get("window_days", 7))
    cov = Coverage()
    print(f"Collecting {cfg['lab']} signals at {iso(NOW)} (window {cfg.get('window_days', 7)} days, {len(prev_items)} items carried)")

    new = []
    if args.no_x:
        cov.skipped("x", "--no-x")
    else:
        new += collect_x(cfg, state, cov, os.environ.get("X_BEARER_TOKEN"), since_dt)
    if args.no_reddit:
        cov.skipped("reddit", "--no-reddit")
    else:
        new += collect_reddit(cfg, cov, (os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET"), os.environ.get("REDDIT_USER_AGENT")), since_dt)
    new += collect_hn(cfg, cov, since_dt)
    new += collect_bluesky(cfg, cov, (os.environ.get("BSKY_HANDLE"), os.environ.get("BSKY_APP_PASSWORD")), since_dt)
    new += collect_rss(cfg, cov, since_dt)
    new += collect_search(cfg, cov, (os.environ.get("BRAVE_API_KEY"), os.environ.get("GOOGLE_CSE_KEY"), os.environ.get("GOOGLE_CSE_CX")), since_dt)
    new += collect_pages(cfg, cov, prev_by_id)
    new += collect_watch_pages(cfg, state, cov)
    new += collect_github(cfg, cov, os.environ.get("GITHUB_TOKEN"), since_dt)
    new += collect_npm(cfg, cov, since_dt)
    new += collect_appstore(cfg, cov, since_dt)
    new += collect_markets(cfg, cov)
    if args.no_tripwires:
        for plat in ("registry", "statuspage", "careers", "sitemaps"):
            cov.skipped(plat, "--no-tripwires")
    else:
        new += collect_registry(cfg, state, cov, since_dt)
        new += collect_statuspage(cfg, cov, since_dt)
        new += collect_careers(cfg, state, cov)
        new += collect_sitemaps(cfg, state, cov)

    items = merge(prev_items, new, cfg.get("window_days", 7))
    digest = make_digest_safely(items, cfg, cov, state)   # annotates items with flags and updates state, so it runs before raw is written
    raw_head = {"schema": "signals_raw/1", "lab": cfg["lab"], "collected_at": iso(NOW), "window_days": cfg.get("window_days", 7), "coverage": cov.rows, "state": state}

    os.makedirs(args.out, exist_ok=True)
    out_path = lambda name: os.path.join(args.out, name)  # noqa: E731
    with open(out_path("signals_raw.json"), "w", encoding="utf-8") as fh:
        fh.write(dumps_lines(raw_head, items))
    digest_text, more_text = dumps_digest(digest), dumps_more(digest, cfg)
    with open(out_path("signals_digest.json"), "w", encoding="utf-8") as fh:
        fh.write(digest_text)
    with open(out_path("signals_digest_more.json"), "w", encoding="utf-8") as fh:
        fh.write(more_text)
    for plat, row in cov.rows.items():
        print(f"  {plat:16s} {row['status']:8s} {row['count']:5d}  {row['note'][:90]}")
    print(f"{len(new)} new items this run; {len(items)} in window; digest {len(digest['items'])} items, {digest['overflow']['n']} to overflow "
          f"({len(digest_text)} of {digest['budget']['chars_limit']} chars; more file {len(more_text) // 1024} KB)")

    gist_id, gist_token = os.environ.get("GIST_ID"), os.environ.get("GIST_TOKEN")
    if gist_id and gist_token:
        try:
            upload_gist(gist_id, gist_token, {"signals_digest.json": digest_text, "signals_digest_more.json": more_text})
            print(f"digest uploaded to gist {gist_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! gist upload failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
