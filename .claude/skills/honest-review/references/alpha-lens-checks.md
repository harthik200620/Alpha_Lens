# Alpha_Lens high-leverage review checks

Concrete, project-specific things that are easy to get wrong and worth verifying
*before* you give a verdict on a change in this area. Each is phrased as
"the claim to check" so you can turn it into evidence. Pull exact rules from
`CLAUDE.md` — this file is a pointer to where the landmines are, not a
substitute for it.

## Backend imports / decomposition

- **Run the harness, don't eyeball imports.** `ALPHA_LENS_SKIP_AUTO_BOOTSTRAP=1
  python -c "import app"` from `backend/` must print **37 routes**. A wrong
  subpackage path or circular import surfaces here, not in `py_compile`.
- **`persistence/db.py` resolves DB paths from `__file__`.** `_APP_DIR` must be
  the **parent** of the module's dir so `news_cache.db`/`users.db` resolve to
  `backend/`, not `backend/persistence/`. Getting this wrong makes
  `sqlite3.connect()` silently create an empty DB and run on it — a data-loss
  trap. Verify `_NEWS_DB`/`_USERS_DB` point at `backend/`.
- **Imports are absolute dotted paths** (`from marketdata.ticker_utils import …`),
  never relative. Subpackages: `persistence/`, `marketdata/`, `newsproc/`,
  `signals/`. The Render entrypoint is `gunicorn --chdir backend … app:app` — if
  a change would require editing that, it's a bigger deal than it looks.
- **State that is mutated in place** (dicts/lists/sets) can be imported back from
  a module; state that is **rebound** (`global X; X = …`) cannot — moving it
  breaks sharing. This is the usual root cause of a "worked before extraction"
  regression.

## Frontend chunk discipline

- `app.js` was split into 9 ordered `app-*.js` classic scripts sharing one global
  scope. The invariant: **concatenating the chunks in `index.html` load order
  reproduces the original byte-for-byte.** If a review touches the split, that
  byte-identity is the test — not "it looks fine."
- A chunk change means updating **three** places in lockstep: `index.html`
  `<script>` tags, `sw.js` `isStaticAsset`, and the `/app-` rule in `app.py`
  `_CACHE_RULES` — plus bumping the `?v=` query and `sw.js CACHE_VERSION`. A diff
  that updates one but not the others is 🟠/🔴.

## Retention / lifecycle (easy to get subtly wrong)

- **News feed and signals are two different windows.** News feed: bounded to
  `NEWS_MAX_ROWS` (800) / `NEWS_MAX_AGE_DAYS` (5). Signals: retained 90 days.
  News referenced by a signal is **exempt** from the news prune. A change that
  conflates these, or that re-introduces a per-cycle hard-delete of signals, is
  a 🔴 — the archival worker is meant to be the *sole* retention authority
  (reversible move to `*_archive`, nothing hard-deleted on the hot path).
- Permanent data deletion (the reset-all-news wipe) is irreversible and gated
  behind a confirm token; flag any code path that can reach it without the guard.

## Process / repo rules

- **Push target is `harthik`, not `origin`.** A change to push config or a
  suggested `git push origin` is wrong for this repo.
- Commits are expected to end with the `Co-Authored-By: Claude Opus 4.8` trailer.
- **CLAUDE.md must stay in sync** with structural changes (new modules, moved
  files, new env vars, new commands). A structural change whose diff doesn't
  touch CLAUDE.md is a 🟠 worth raising.

## Library APIs

For any claim about a third-party API (Flask, google-genai, yfinance, sendgrid,
feedparser, …), check **Context7 MCP** rather than asserting from memory. A
review that confidently misstates a library's behavior is the exact
confident-but-wrong failure this skill is built to avoid.
