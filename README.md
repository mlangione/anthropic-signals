# Anthropic signals collector

The "middle path" collector for the Frontier Lab Dashboard's **Anthropic signals** tab. It gathers raw
items (posts, stories, feed entries, releases, incidents, market prices, registry and sitemap changes) on a
schedule and publishes three JSON files; the dashboard's daily Cowork task reads the digest, extracts claims,
scores them and renders the tab. The collector does not judge claims; it only ranks what it collected so the
earliest and most unusual items are read first.

Standard library only (Python 3.11+). Every source degrades to `skipped` or `error` in the output's
`coverage` block instead of failing the run, so a missing key never blocks the others.

## What it polls

| Source | Needs | Notes |
|---|---|---|
| X: 34 seeded accounts (Anthropic staff, trackers, analysts, reporters, small leakers) + 7 keyword searches (three tuned for early detection: spotted/feature-flag/codename vocabulary, `claude-*` model ids, "people familiar" scoops) | `X_BEARER_TOKEN` | v2 API, app-only bearer. Timelines use `since_id`, so each run reads only new posts. Searches run only at the hours in `sources.json → x.search_hours_utc` to keep paid reads predictable. Each run prints the posts read. |
| Reddit: r/ClaudeAI, r/ClaudeCode, r/Anthropic (all posts); r/singularity, r/artificial, r/LocalLLaMA (keyword-filtered); two searches | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` — **only if you already hold approved credentials** | Skipped otherwise, never scraped. Reddit closed self-service API registration in Nov 2025 and announced (Sep 30, 2026) that RSS ends Nov 13, 2026 and public API access ends by Mar 2027. |
| Reddit via search index: Google Alerts RSS feeds (`site:reddit.com` queries) and/or Brave Search API / Google Programmable Search | Google Alerts: nothing (paste the feed URL); `BRAVE_API_KEY` or `GOOGLE_CSE_KEY`+`GOOGLE_CSE_CX` | Compliant substitute for Reddit's closed API: titles, snippets, dates and URLs, no scores or comments. See SETUP.md step 4b. |
| Hacker News (Algolia) | nothing | stories, Show HN, Ask HN mentioning Anthropic or Claude, ≥3 points |
| Bluesky | nothing (optional `BSKY_HANDLE`/`BSKY_APP_PASSWORD`) | post search via the public AppView plus three author feeds; the session fallback is used only if search starts returning 401 |
| RSS/Atom: Claude status page, AWS What's New, Vertex release notes, Import AI, Zvi, Simon Willison, Algorithmic Bridge, Interconnects, Latent Space, Platformer, Mollick, SemiAnalysis, Stratechery, TestingCatalog, The Information, Bloomberg Technology, TechCrunch AI, The Verge AI | nothing | keyword-filtered except the status page and the two Anthropic-centric newsletters. A feed may list `fallbacks` (Import AI and Zvi fall back to their non-Substack mirrors when Substack answers 403 to datacenter IPs). |
| anthropic.com/news, /engineering, /research | nothing | no RSS exists, so the article links are parsed; the first run only records a baseline, later runs tag new links and read their `article:published_time` |
| Watched pages: docs release notes, Claude Code CHANGELOG, pricing page, models overview, model deprecations, supported countries, robots.txt of anthropic.com and claude.com | nothing | emits an item with the new text when a page changes; every change is flagged `trip` (a tripwire) |
| GitHub releases (SDKs, Claude Code, Agent SDK), npm (`@anthropic-ai/claude-code`, `@anthropic-ai/sdk`) | `GITHUB_TOKEN` (automatic in Actions) | model-id strings in release notes are extracted into `extra.model_ids` |
| App Store (Claude iOS release notes) | nothing | iTunes lookup API |
| **Tripwires** (new): sitemaps of anthropic.com and claude.com; OpenRouter's public model list (new `anthropic/*` ids and cloaked/stealth models); LiteLLM's model price list (new `claude`/`anthropic` keys, which carry the Bedrock and Vertex ids); the Claude status page's component list; Anthropic's Greenhouse job board | nothing | Things that tend to appear before the announcement: a published URL whose slug is the headline, a model id in a registry, a new status component, an investor-relations or silicon job posting, an office in a new city. Each keeps a small state in `signals_raw.json`; the first run records a baseline and reports nothing, later runs report only what changed. Skip them with `--no-tripwires`. |
| Polymarket, Manifold | nothing | every open market whose question mentions Anthropic/Claude, with price and 1-day change |

## Outputs (committed to `data/` on every run)

- `signals_raw.json` — all items seen in the last 7 days (`window_days`), engagement re-polled each run
  (`metrics_history`), early-detection `flags`, plus collector state (`x_user_ids`, `since_id`s, page hashes,
  registry keys, sitemap hashes, job ids, identifiers seen). One line per item. Git history is the archive.
- `signals_digest.json` — what the dashboard task reads. Candidates are every post from an official account or
  trusted tracker, anything from the last 48 hours that matches the keywords or carries a Claude model id, and every
  primary-source item and market from the window. They are **ranked best first by an early-detection priority**
  (leak and rumor vocabulary, model ids, identifiers not seen in the earlier days, tripwires, rising engagement,
  corroboration by independent authors, source tier) and **sized to `digest_budget_chars` (56,000)** so the fetch
  tool the dashboard task uses (about 25K tokens) cannot truncate it: lower-ranked items get shorter text first, then
  fall out. Keys are short and a `legend` at the top explains them; the last key is `"complete": true`, so a
  truncated read is detectable. The header also carries coverage, per-platform counts, the X reads used and an
  `overflow` count. Since v1.2 the mix is balanced so volume on one source cannot crowd out the rest: the best few
  press, blog, newsletter, Substack, Hacker News, Reddit and Bluesky items get a diversity bonus (`FLOORS`);
  Polymarket and Manifold are capped, with at most three contracts per Polymarket event (an IPO-date ladder is one
  event); GitHub, npm and status-page incidents are routine and rank low unless they name a new model or come from a
  tripwire; posts about one new identifier are limited to eight apart from official accounts, and flagged X posts
  to 90 (`NEWID_CAP`, `HARD_CAPS`). Replies need a keyword even from official accounts, and `testingcatalog`
  (which covers every lab) is no longer always-include. Rumor flags ignore market wording and the status page's
  "as soon as possible", and product names such as `claude-code` are not model ids.
  (A file this size is too large for a fetch tool to show inline: it saves the result to disk and the reader opens
  that file in chunks. The dashboard task's prompt says so.)
- `signals_digest_more.json` — the candidates that did not fit (up to 400), same format. The dashboard task
  reads it only when the overflow count looks relevant.

## Setup (about 20 minutes)

1. Create a **public** GitHub repository (e.g. `anthropic-signals`) and push this folder's contents
   (`collect.py`, `test_collect.py`, `sources.json`, `.github/workflows/collect.yml`, this README, and an
   empty `data/` folder with a `.gitkeep`).
2. **X**: at developer.x.com create a project/app on the pay-per-use plan and copy the app-only
   **Bearer Token** → repository secret `X_BEARER_TOKEN`. Expect roughly 800–1,100 reads a day with the
   default seed list (≈$120–165/month at the reported $0.005 per read; check the current price). Trim
   `x.accounts` or `x.searches` in `sources.json` to spend less; the three early-detection searches are about
   180 of those reads.
3. **Reddit**: skip unless you already hold approved Data API credentials (see SETUP.md step 4); if you do,
   add `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` and `REDDIT_USER_AGENT` in the form
   `windows:anthropic-signals:1.0 (by /u/yourname)`.
4. (Optional) **Bluesky** app password → `BSKY_HANDLE`, `BSKY_APP_PASSWORD`. Only needed if the public
   search endpoint starts returning 401 in the logs.
5. Run the workflow once by hand (Actions → collect-anthropic-signals → Run workflow). The first run only
   baselines the newsroom pages; the second run onward reports new articles.
6. Put the digest's raw URL into the dashboard's `signals_data.json → meta.raw_source.digest_url`:
   `https://raw.githubusercontent.com/<you>/anthropic-signals/main/data/signals_digest.json`.
   (A gist is also supported: `python collect.py --create-gist` with `GIST_TOKEN` set prints a `GIST_ID`;
   store it as a repository *variable* and the workflow will also upload the digest there.)

GitHub disables scheduled workflows after 60 days without repository activity; the workflow's own commits
keep it active, but if the schedule ever stops, re-enable it from the Actions tab.

## Local run

```
python3 test_collect.py                      # offline parser tests
python3 collect.py --no-x --no-reddit        # keyless run into ./data (add --no-tripwires to skip the registry/sitemap/careers checks)
X_BEARER_TOKEN=... python3 collect.py        # full run
```

`--max-seconds` (default 900) stops new requests after that long so a slow host cannot stall the run;
anything not reached is marked `error: run deadline exceeded` in `coverage`.

## Editing the seed list

Everything is in `sources.json`: X accounts (with a starting tier and `always: true` for accounts whose
posts should reach the digest even without a keyword match; `always` is honoured for tiers A and B, while tier C
and D accounts still need a keyword, a codename or a model id), subreddits, HN queries, feeds, watched pages,
sitemaps, registries, repos, packages and market search terms. `codenames` is a list of strings (for example the
internal names of an early-access programme) that are treated like keywords. `digest_budget_chars`,
`digest_text_caps` and `digest_more_max_items` control the digest size; `digest_floors`, `market_per_event`,
`newid_cap` and `digest_hard_caps` (optional) override the balance constants in `collect.py`. Tiers here are only the collector's hint;
the dashboard keeps the authoritative tier and the measured hit rate per source.

## Terms

Stay within each platform's API terms: X data is read through the paid API, Reddit only through approved
OAuth credentials, everything else through public endpoints. Do not add scrapers for X or Reddit.
