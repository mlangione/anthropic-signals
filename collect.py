#!/usr/bin/env python3
"""Anthropic signals collector.

Polls the sources listed in sources.json (X, Reddit, Hacker News, Bluesky, RSS/Atom feeds, the Claude
status page, Anthropic's newsroom pages, GitHub releases, npm, watched docs pages, the App Store and
prediction markets) and writes two files:

  <out>/signals_raw.json     every item seen in the last `window_days` days, with engagement metrics
                             re-polled on each run (metrics_history) and the collector's own state
                             (X user ids, since_ids, page hashes) so the next run only fetches what is new
  <out>/signals_digest.json  the compact view the daily dashboard task reads: keyword matches from the
                             last `digest_hours` hours, text trimmed, plus every primary-source item

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
            for q in xc.get("searches", []):
                params = {"query": q, "max_results": str(xc.get("search_max_results", 50)), "tweet.fields": X_TWEET_FIELDS,
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
            status, body, _ = http(f["url"])
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
            text = body if wp["url"].endswith(".md") else strip_html(re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I))
            lines = [l.strip() for l in re.split(r"(?<=[.!?])\s+|\n+", text) if len(l.strip()) > 20][:600]
            digest = hashlib.sha256(text.encode()).hexdigest()
            prev = pages.get(wp["url"])
            if prev and prev.get("hash") != digest:
                new_lines = [l for l in lines if l not in set(prev.get("lines", []))][:12]
                items.append(item("watch:" + hashlib.sha1((wp["url"] + digest).encode()).hexdigest()[:16], wp.get("platform", "web"), wp["name"], wp["url"],
                                  f"{wp['name']} changed. New text: " + " | ".join(new_lines)[:1000], NOW, "changelog", title=f"{wp['name']} changed", tier=wp.get("tier", "A"),
                                  extra={"new_lines": new_lines}))
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


PRIMARY_PLATFORMS = {"status", "anthropic.com", "docs.claude.com", "claude.com", "github", "npm", "appstore", "partner"}
DIGEST_CAPS = {"x": 70, "reddit": 40, "hn": 30, "bluesky": 30, "substack": 20, "blog": 10, "newsletter": 10, "polymarket": 15, "manifold": 10, "web": 15}


def engagement(it):
    m = it.get("metrics") or {}
    return sum(float(m.get(k) or 0) * w for k, w in (("likes", 1), ("reposts", 3), ("quotes", 3), ("replies", 0.5), ("score", 1), ("comments", 1), ("points", 2)))


def make_digest(items, cfg, cov, state):
    kw = re.compile(cfg["keywords"], re.I)
    cutoff = NOW - dt.timedelta(hours=cfg.get("digest_hours", 48))
    picked, counts = [], {}
    for it in items:
        plat = it["platform"]
        created = parse_dt(it.get("created_at")) or NOW
        first_seen = parse_dt(it.get("first_seen")) or created
        if "baseline" in it.get("tags", []):
            continue                                       # undated links recorded on the collector's first run
        if plat in PRIMARY_PLATFORMS or plat in ("polymarket", "manifold"):
            keep = max(created, first_seen) >= NOW - dt.timedelta(days=cfg.get("window_days", 7))
        else:
            keep = max(created, first_seen) >= cutoff and ("always" in it.get("tags", []) or kw.search((it.get("title") or "") + " " + it.get("text", "")))
        if keep:
            picked.append(it)
    picked.sort(key=lambda x: (x["platform"] in PRIMARY_PLATFORMS, engagement(x), x.get("created_at", "")), reverse=True)
    out = []
    for it in picked:
        plat = it["platform"]
        cap = DIGEST_CAPS.get(plat, 50)
        if plat not in PRIMARY_PLATFORMS and counts.get(plat, 0) >= cap:
            continue
        counts[plat] = counts.get(plat, 0) + 1
        m = {k: v for k, v in (it.get("metrics") or {}).items() if v is not None}
        out.append({"id": it["id"], "platform": plat, "source": it["source"], "author": it.get("author"), "tier": it.get("tier"), "url": it["url"],
                    "created_at": it["created_at"], "kind": it["kind"], "text": " ".join(it.get("text", "").split())[:320], "metrics": m,
                    "cites": it.get("cites", [])[:3], "tags": it.get("tags", []), **({"extra": {k: v for k, v in it["extra"].items() if k in ("probability", "end_date", "model_ids", "version", "new_lines", "query")}} if it.get("extra") else {})})
    return {"schema": "signals_digest/1", "lab": cfg["lab"], "collected_at": iso(NOW), "digest_hours": cfg.get("digest_hours", 48),
            "coverage": cov.rows, "counts": counts, "x_reads_last_run": state.get("x_reads_last_run"), "items": out}


def upload_gist(gist_id, token, files):
    body = json.dumps({"files": {name: {"content": content} for name, content in files.items()}})
    status, resp, _ = http(f"https://api.github.com/gists/{gist_id}", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
                           data=body, method="PATCH", accept_json=True)
    if status != 200:
        raise RuntimeError(f"gist HTTP {status}: {resp[:200]}")
    return json.loads(resp)


def create_gist(token):
    body = json.dumps({"description": "Anthropic signals collector output", "public": True,
                       "files": {"signals_digest.json": {"content": json.dumps({"schema": "signals_digest/1", "items": []})}}})
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

    items = merge(prev_items, new, cfg.get("window_days", 7))
    raw = {"schema": "signals_raw/1", "lab": cfg["lab"], "collected_at": iso(NOW), "window_days": cfg.get("window_days", 7),
           "coverage": cov.rows, "state": state, "items": items}
    digest = make_digest(items, cfg, cov, state)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "signals_raw.json"), "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "signals_digest.json"), "w", encoding="utf-8") as fh:
        json.dump(digest, fh, ensure_ascii=False, indent=1)
    for plat, row in cov.rows.items():
        print(f"  {plat:16s} {row['status']:8s} {row['count']:5d}  {row['note'][:90]}")
    print(f"{len(new)} new items this run; {len(items)} in window; digest {len(digest['items'])} items "
          f"({os.path.getsize(os.path.join(args.out, 'signals_digest.json')) // 1024} KB)")

    gist_id, gist_token = os.environ.get("GIST_ID"), os.environ.get("GIST_TOKEN")
    if gist_id and gist_token:
        try:
            upload_gist(gist_id, gist_token, {"signals_digest.json": json.dumps(digest, ensure_ascii=False, indent=1)})
            print(f"digest uploaded to gist {gist_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! gist upload failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
