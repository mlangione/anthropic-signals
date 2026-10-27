# Setting up the Anthropic signals collector — step by step

Time: about 35 minutes, most of it in the X developer portal. You need a GitHub account (personal is fine)
and an X account. Nothing here requires git on your machine; everything is done in a browser.

The end state: a public GitHub repository runs `collect.py` four times a day and commits
`data/signals_digest.json`; the dashboard's 8:00 AM task reads that file from its raw URL.

---

## Step 1 — Create the GitHub repository (5 min)

1. Sign in at github.com → click **+** (top right) → **New repository**.
2. Repository name: `anthropic-signals`. Visibility: **Public** (the dashboard task fetches the raw file
   without credentials; the data is public rumor data, nothing sensitive). Leave "Add a README" unticked.
3. Click **Create repository**.
4. On the empty-repo page click **uploading an existing file**. In Windows Explorer open
   `Documents\FrontierLabDashboard\collector` and drag these into the browser's upload area:
   `collect.py`, `test_collect.py`, `sources.json`, `README.md`, `SETUP.md`, `.gitignore`, and the two
   folders `.github` and `data` (drag the folders themselves so the paths `.github/workflows/collect.yml`
   and `data/.gitkeep` are preserved). Do not upload `__pycache__`.
5. Commit message "collector" → **Commit changes**.
6. Check the result: the repo should show `.github/workflows/collect.yml`. If the folder did not survive
   the drag, use **Add file → Create new file**, type `.github/workflows/collect.yml` as the name (typing
   the slashes creates the folders) and paste the file's contents.

## Step 2 — Let the workflow write to the repository (1 min)

Settings → **Actions** → **General** → scroll to **Workflow permissions** → select
**Read and write permissions** → Save. (Without this the run collects fine but the "Commit data" step fails
with "permission denied".)

## Step 3 — X API access (15–20 min, the only paid part)

1. Go to developer.x.com and sign in with the X account that will own the app.
2. Create a **Project** and an **App** inside it (the portal walks you through this; names do not matter).
   Choose the pay-per-use plan when asked and add a payment method. If the portal only offers monthly tiers,
   Basic is the fallback; it is roughly $200/month and more than this collector needs.
3. In the app's **Keys and tokens** tab, generate the **Bearer Token** (app-only authentication). Copy it
   once; it is shown a single time. You do not need the API key/secret pair or any user tokens.
4. Budget: the default seed list reads roughly 500–800 posts a day (timelines fetch only new posts; the four
   keyword searches run twice daily). At the reported $0.005 per post read that is about $75–120 a month.
   Every run prints the exact count in the Actions log ("x ok … posts read") and the digest carries it as
   `x_reads_last_run`. To spend less, delete accounts from `sources.json → x.accounts`, lower
   `search_max_results`, or set `search_hours_utc` to a single hour.

## Step 4 — Reddit: skip it (0 min)

Reddit is no longer a practical source for a new tool, so the collector skips it unless approved credentials
are present (it never scrapes). What changed, with dates:

- **November 2025** — Reddit closed self-service app creation ("Responsible Builder Policy"): new Data API
  access needs a request form and manual approval; reported waits run weeks and many requests are denied.
- **September 30, 2026** — Reddit announced that RSS feeds end on **November 13, 2026**, that public Data API
  access ends by **March 2027**, and that already-approved apps must re-register on its Developer Platform by
  **January 12, 2027** or lose access. After March 2027 programmatic Reddit data needs a commercial deal.

If you already hold approved credentials from an earlier project, add `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`
and `REDDIT_USER_AGENT` (format `windows:anthropic-signals:1.0 (by /u/YOURNAME)`) and the OAuth path will run until
Reddit turns it off. Otherwise leave the three secrets out: the coverage table shows `reddit skipped` and
nothing else is affected.

### Step 4b — Reddit through a search engine's index instead (10 min, free)

Search engines license or crawl Reddit and expose it through their own APIs, which is the compliant way to see
new Reddit threads without Reddit's API. You get titles, snippets, dates and URLs, not scores or comment counts.
Two options; either or both:

**Google Alerts → RSS (zero code, 5 min).** At google.com/alerts create an alert with the query
`anthropic OR claude site:reddit.com`; under "Show options" set *How often* to "As-it-happens", *Sources* to
"Automatic", *Deliver to* **RSS feed**; click Create Alert, then click the RSS icon next to the alert and copy the
feed URL (it looks like `https://www.google.com/alerts/feeds/<numbers>/<numbers>`). Paste it into
`sources.json` → the first `rss` entry (replace `PASTE_GOOGLE_ALERTS_FEED_URL_HERE`) and set `"enabled": true`.
Add a second alert for `site:reddit.com/r/ClaudeCode` if you want that subreddit in full. The collector
unwraps Google's redirect links, strips the highlighting, and labels Reddit links as `reddit`.

**Brave Search API (programmatic, 5 min).** At api-dashboard.search.brave.com sign up for the free "Data for
Search" plan (around 2,000 queries a month; the collector uses about 16 a day, four queries on each of the four
scheduled runs), create an API key, and add it as the repository secret `BRAVE_API_KEY`.
Queries live in `sources.json → search.queries`; they are site-scoped to reddit.com and date-restricted to the
last 7 days. Google's Programmable Search JSON API is supported the same way (`GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX`,
100 free queries a day) if you prefer Google's index.

Threads found this way carry tier D in the dashboard, like any anonymous account, and the daily task treats the
thread title and snippet as the claim text.

## Step 5 — Add the secrets to the repository (3 min)

Settings → **Secrets and variables** → **Actions** → **New repository secret**, one at a time:

| Name | Value |
|---|---|
| `X_BEARER_TOKEN` | the Bearer Token from step 3 |

That is the only required secret. Optional ones: `BRAVE_API_KEY` (or `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX`) for
Reddit-via-search (step 4b); `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` only if you already
hold approved Reddit API credentials (step 4); `BSKY_HANDLE` and `BSKY_APP_PASSWORD` only if a run's log shows
Bluesky search returning 401 (create an app password in Bluesky under Settings → Privacy and security → App
passwords). `GITHUB_TOKEN` is provided automatically; `GIST_ID`/`GIST_TOKEN` are not needed.

## Step 6 — Run it twice by hand (5 min)

1. **Actions** tab → if GitHub asks you to enable workflows for this repository, click **I understand… enable**.
2. Select **collect-anthropic-signals** → **Run workflow** → **Run workflow**. Open the run, then the
   **Collect** step: you should see a coverage table like

       x                ok          143  143 posts read (timelines + 4 searches)
       reddit           skipped       0  no approved API credentials (Reddit closed self-service registration; public API ends Mar 2027)
       search           ok           31  Brave: 4 queries
       hn               ok           21
       bluesky          ok           34  public AppView
       feeds            ok           19  13 feeds OK
       anthropic_pages  ok           37  0 new article links; baseline recorded
       watch_pages      ok            0  3 pages checked
       github           ok            9
       npm              ok           13
       appstore         ok            1
       markets          ok           14  Polymarket: 3 events searched; Manifold OK

   `skipped` means a key is missing; `error` names the cause. The first run only baselines the Anthropic
   newsroom pages (undated links are not reported as news).
3. Run it a second time. `anthropic_pages` should now say "0 new article links" without "baseline", and the
   **Commit data** step should show a commit. From here the schedule takes over (00:15, 06:15, 12:15, 18:15 UTC).
4. Open the digest in a browser to confirm it is public:
   `https://raw.githubusercontent.com/YOURNAME/anthropic-signals/main/data/signals_digest.json`

## Step 7 — Point the dashboard at it (2 min)

Open `Documents\FrontierLabDashboard\signals_data.json` and set

    "digest_url": "https://raw.githubusercontent.com/YOURNAME/anthropic-signals/main/data/signals_digest.json"

(or ask Claude in Cowork: "set the signals digest URL to …"). The next 8:00 AM run fetches it; to see the
effect immediately, open the task "Frontier Lab Dashboard — daily refresh" in the Claude desktop app and click
**Run now**. The tab's header pill changes from "Collector digest not yet connected" to "Collected … · N sources OK",
and the Notes card lists what each source returned.

---

## Changing what it watches

Edit `sources.json` in the repository (pencil icon → commit). Seed X accounts carry a starting tier and
`always: true` for accounts whose every post should reach the digest; the dashboard keeps the authoritative tier
and the measured hit rate. Add subreddits, feeds, watched pages, repos or market search terms the same way.
The offline tests run at the start of every workflow run, so a malformed `sources.json` fails fast.

## If something goes wrong

- **Commit data step fails ("permission denied" / "refusing to allow a GitHub App")** → step 2 was skipped.
- **x error 402/403** → the plan or payment method is not active, or the secret is a user token rather than the
  app-only Bearer Token. **429** → rate-limited; the script backs off, and the next run continues from `since_id`.
- **reddit skipped** → expected without approved credentials (step 4). **reddit error 401** → client ID/secret
  wrong; **403** → user agent not in Reddit's format, or Reddit has revoked access under the 2026 changes.
- **bluesky error 401** → add the two BSKY secrets (step 5).
- **feeds "failed: …"** → one feed URL changed; the others still load. Fix or remove it in `sources.json`.
- **The schedule stops after a quiet period** → GitHub disables scheduled workflows after 60 days without
  repository activity; the workflow's own commits normally prevent this. Re-enable from the Actions tab.
- **The dashboard task cannot fetch the raw URL** → run `python3 collect.py --create-gist` once locally with
  `GIST_TOKEN` set (a fine-grained GitHub token with gist scope), store the printed `GIST_ID` as a repository
  *variable* and `GIST_TOKEN` as a secret, and use the printed gist URL as `digest_url` instead.

## Running it on your own machine instead

With Python 3.11+ installed: open a terminal in the `collector` folder and run
`python collect.py --no-x --no-reddit` for a keyless run, or set the environment variables above and run
`python collect.py`. Output lands in `collector\data\`. If you ever want this instead of GitHub Actions, point
`digest_url` at nothing and tell Claude to read `collector/data/signals_digest.json` from the folder directly.
