# Anthropic signals collector

The "middle path" collector for the Frontier Lab Dashboard's **Anthropic signals** tab. It gathers raw
items (posts, stories, feed entries, releases, incidents, market prices) on a schedule and publishes two
JSON files; the dashboard's daily Cowork task reads the digest, extracts claims, scores them and renders
the tab. Nothing here interprets anything — it only collects.

Standard library only (Python 3.11+). Every source degrades to `skipped` or `error` in the output's
`coverage` block instead of failing the run, so a missing key never blocks the others.

## What it polls

| Source | Needs | Notes |
|---|---|---|
| X: 25 seeded accounts (Anthropic staff, trackers, analysts, reporters) + 4 keyword searches | `X_BEARER_TOKEN` | v2 API, app-only bearer. Timelines use `since_id`, so each run reads only new posts. Searches run only at the hours in `sources.json → x.search_hours_utc` to keep paid reads predictable. Each run prints the posts read. |
| Reddit: r/ClaudeAI, r/ClaudeCode, r/Anthropic (all posts); r/singularity, r/artificial, r/LocalLLaMA (keyword-filtered); two searches | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | Free tier (100 QPM, non-commercial). Falls back to the public `.json` endpoints without credentials, which Reddit rate-limits hard. |
| Hacker News (Algolia) | nothing | stories, Show HN, Ask HN mentioning Anthropic or Claude, ≥3 points |
| Bluesky | nothing (optional `BSKY_HANDLE`/`BSKY_APP_PASSWORD`) | post search via the public AppView plus three author feeds; the session fallback is used only if search starts returning 401 |
| RSS/Atom: Claude status page, AWS What's New, Vertex release notes, Import AI, Zvi, Simon Willison, Algorithmic Bridge, Interconnects, Latent Space, Platformer, Mollick, SemiAnalysis, Stratechery | nothing | keyword-filtered except the status page and the two Anthropic-centric newsletters |
| anthropic.com/news, /engineering, /research | nothing | no RSS exists, so the article links are parsed; the first run only records a baseline, later runs tag new links and read their `article:published_time` |
| Watched pages: docs release notes, Claude Code CHANGELOG, pricing page | nothing | emits an item with the new sentences when a page's text changes |
| GitHub releases (SDKs, Claude Code, Agent SDK), npm (`@anthropic-ai/claude-code`, `@anthropic-ai/sdk`) | `GITHUB_TOKEN` (automatic in Actions) | model-id strings in release notes are extracted into `extra.model_ids` |
| App Store (Claude iOS release notes) | nothing | iTunes lookup API |
| Polymarket, Manifold | nothing | every open market whose question mentions Anthropic/Claude, with price and 1-day change |

## Outputs (committed to `data/` on every run)

- `signals_raw.json` — all items seen in the last 7 days (`window_days`), engagement re-polled each run
  (`metrics_history`), plus collector state (`x_user_ids`, `since_id`s, page hashes). Git history is the archive.
- `signals_digest.json` — what the dashboard task reads: keyword matches from the last 48 hours with text
  trimmed to 320 characters (capped per platform), every primary-source item from the window, coverage,
  counts and the X reads used.

## Setup (about 20 minutes)

1. Create a **public** GitHub repository (e.g. `anthropic-signals`) and push this folder's contents
   (`collect.py`, `test_collect.py`, `sources.json`, `.github/workflows/collect.yml`, this README, and an
   empty `data/` folder with a `.gitkeep`).
2. **X**: at developer.x.com create a project/app on the pay-per-use plan and copy the app-only
   **Bearer Token** → repository secret `X_BEARER_TOKEN`. Expect roughly 500–800 reads a day with the
   default seed list (≈$75–120/month at the reported $0.005 per read; check the current price). Trim
   `x.accounts` or `x.searches` in `sources.json` to spend less.
3. **Reddit**: at reddit.com/prefs/apps create a *script* app → secrets `REDDIT_CLIENT_ID`,
   `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT` in the form
   `windows:anthropic-signals:1.0 (by /u/yourname)`. Free tier, personal/non-commercial use only.
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
python3 collect.py --no-x --no-reddit        # keyless run into ./data
X_BEARER_TOKEN=... python3 collect.py        # full run
```

`--max-seconds` (default 900) stops new requests after that long so a slow host cannot stall the run;
anything not reached is marked `error: run deadline exceeded` in `coverage`.

## Editing the seed list

Everything is in `sources.json`: X accounts (with a starting tier and `always: true` for accounts whose
posts should reach the digest even without a keyword match), subreddits, HN queries, feeds, watched pages,
repos, packages and market search terms. Tiers here are only the collector's hint; the dashboard keeps the
authoritative tier and the measured hit rate per source.

## Terms

Stay within each platform's API terms: X data is read through the paid API, Reddit through the free
non-commercial tier, everything else through public endpoints. Do not add scrapers for X or Reddit.
