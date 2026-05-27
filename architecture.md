# Architecture

This document explains the full structure of the AI News Aggregator: what each file does, how the pipeline flows, and how all the pieces connect.

---

## High-Level Overview

The system scrapes AI news from three sources (YouTube, OpenAI, Anthropic), enriches the raw data, generates AI-written digests (quality-gated by an LLM judge), ranks them per user, and delivers a personalized email newsletter. The pipeline is orchestrated as a **LangGraph StateGraph**.

```
app/daily_runner.py
        │
        └─► pipeline_graph.invoke(initial_state)
                │
        ┌───────▼────────┐
        │   LangGraph    │
        │   StateGraph   │
        └───────┬────────┘
                │
    ┌───────────▼───────────┐
    │  [1] scrape node      │  ← app/runner.py
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │  [2] process node     │  ← process_anthropic.py + process_youtube.py
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │  [3] digest node      │  ← DigestAgent + JudgeAgent
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │  [4] send_email node  │  ← CuratorAgent + EmailAgent (per user)
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │  [5] finalize node    │  ← assembles success flag
    └───────────────────────┘
```

---

## Entry Points

### [main.py](main.py)
Runs **scraping only** (step 1). Calls `run_scrapers(hours=24)` and prints counts.

### [app/daily_runner.py](app/daily_runner.py)
Runs the **full 5-node LangGraph pipeline**. Calls `create_tables()` first, then `pipeline_graph.invoke(initial_state)`. Logs a summary of all node results on completion.

```bash
python -m app.daily_runner
```

---

## LangGraph Pipeline (`app/pipeline/`)

### [state.py](app/pipeline/state.py)
Defines `PipelineState` — a `TypedDict` shared across all nodes. Each node receives the full state and returns a partial dict; LangGraph merges it back in.

```python
class PipelineState(TypedDict):
    hours: int
    top_n: int
    scrape_youtube_count: int
    scrape_openai_count: int
    scrape_anthropic_count: int
    anthropic_processed: int
    anthropic_failed: int
    youtube_processed: int
    youtube_unavailable: int
    digest_total: int
    digest_processed: int
    digest_failed: int
    email_results: List[Dict[str, Any]]
    email_success_count: int
    email_skip_count: int
    email_error_count: int
    errors: List[str]
    success: bool
```

### [workflow.py](app/pipeline/workflow.py)
Builds and compiles the `StateGraph`. Linear DAG — no conditional edges. No checkpointer; state is fully in-memory per run.

```
START → scrape → process → digest → send_email → finalize → END
```

### Node files (`app/pipeline/nodes/`)

| Node | File | Delegates to |
|---|---|---|
| `scrape` | `nodes/scrape.py` | `app/runner.py` |
| `process` | `nodes/process.py` | `process_anthropic.py`, `process_youtube.py` |
| `digest` | `nodes/digest.py` | `process_digest.py` |
| `send_email` | `nodes/email.py` | `process_email.py` |
| `finalize` | `nodes/finalize.py` | — |

Each node wraps its delegated call in a `try/except` so one node failure doesn't crash the entire graph — the error is recorded in `state["errors"]` and the next node still runs.

---

## Pipeline Steps

### Step 1 — Scraping (`app/runner.py`)

`run_scrapers(hours, channel_ids=None)` instantiates all three scrapers and the repository, then:

1. Resolves the YouTube channel list: uses the `channel_ids` argument first → falls back to `repo.get_all_active_channel_ids()` → falls back to `DEFAULT_YOUTUBE_CHANNELS` from `app/config.py`.
2. Calls `YouTubeScraper.get_latest_videos()` per channel, `OpenAIScraper.get_articles()`, `AnthropicScraper.get_articles()`.
3. Bulk-inserts new records into Postgres via `Repository`. Existing primary keys are silently skipped (idempotent).

Returns `{"youtube": [...], "openai": [...], "anthropic": [...]}`.

---

### Step 2 — Content Enrichment (`app/services/process_anthropic.py` + `process_youtube.py`)

**Anthropic full text** — Queries `anthropic_articles` where `markdown IS NULL`. For each, fetches the page via `requests` + `BeautifulSoup`, cleans it, stores in `anthropic_articles.markdown`. Anthropic is the only source that gets full-text enrichment; OpenAI uses the RSS description, YouTube uses transcripts.

**YouTube transcripts** — Queries `youtube_videos` where `transcript IS NULL`. For each, calls `YouTubeScraper.get_transcript(video_id)`. On success: stores joined transcript text. On failure: stores `__UNAVAILABLE__` to prevent re-fetching and block digest generation.

---

### Step 3 — Digest Generation + Quality Gating (`app/services/process_digest.py`)

Queries all articles not yet in the `digests` table (via `Repository.get_articles_without_digest()`). Eligibility rules:
- YouTube: non-null, non-`__UNAVAILABLE__` transcript
- Anthropic: non-null `markdown`
- OpenAI: always eligible

For each eligible article:
1. `DigestAgent.generate_digest(title, content, article_type)` — calls `llama-3.3-70b-versatile` via Groq, returns `DigestOutput(title, summary)`.
2. `JudgeAgent.judge(original_title, original_content, digest_title, digest_summary, article_type)` — independently scores the summary on factual accuracy, completeness, clarity, and length. Returns `JudgeOutput(score, reasoning, passed)`.
3. Only if `score >= 0.7`: the digest is written to the `digests` table. Failed articles remain "without digest" and are retried on the next pipeline run.

Digest IDs are composite: `"{article_type}:{article_id}"` (e.g. `"youtube:abc123"`).

---

### Step 4 — Email Per User (`app/services/process_email.py`)

Loops over all active users from the DB. For each user:

1. `get_user_digests(repo, user, hours)` — fetches digests from the last N hours that **haven't been sent to this user yet** (checked against `user_digest_sends` table). Also filters YouTube digests to channels the user has subscribed to.
2. If no unsent digests: skip (returns `{"skipped": True}`).
3. `CuratorAgent.rank_digests(digests)` — scores each digest on relevance to the user's profile (0–10), returns a ranked list.
4. `EmailAgent.create_email_digest_response(ranked_articles, total_ranked, limit=top_n)` — writes a personalized greeting + intro, assembles the `EmailDigestResponse`.
5. `email_utils.send_email(subject, body_text, body_html, recipients=[user.email])` — sends via Resend API.
6. `repo.mark_digests_sent(user.id, sent_ids)` — inserts rows into `user_digest_sends` for only the digests included in the email. Digests that were curated but fell below `top_n` remain unsent and can appear in future runs.

---

## AI Agents (`app/agent/`)

### [digest_agent.py](app/agent/digest_agent.py)

**`DigestAgent`**
- Model: `llama-3.3-70b-versatile` via Groq
- Input: article title + content (truncated to 8,000 chars)
- Output: `DigestOutput(title: str, summary: str)`
- Called for every undigested article; output is gated by `JudgeAgent`

**`JudgeAgent`**
- Model: `llama-3.3-70b-versatile` via Groq, `temperature=0.1` (deterministic scoring)
- Input: original title, original content (first 2,000 chars), generated digest title + summary, article type
- Output: `JudgeOutput(score: float, reasoning: str, passed: bool)`
- Evaluates: factual accuracy, completeness, clarity, length (2-3 sentences), absence of hallucinations
- Threshold: `score >= 0.7` → `passed = True` → digest stored; otherwise the article is skipped until next run

### [curator_agent.py](app/agent/curator_agent.py) — `CuratorAgent`
- Model: `llama-3.3-70b-versatile` via Groq
- Constructed with `user_profile` dict (from DB `User` row)
- Input: list of unsent digest dicts
- Output: `List[RankedArticle]` — each with `digest_id`, `rank`, `relevance_score`, `reasoning`

### [email_agent.py](app/agent/email_agent.py) — `EmailAgent`
- Model: `llama-3.3-70b-versatile` via Groq
- Constructed with `user_profile` dict
- Input: top-N `RankedArticleDetail` objects
- Output: `EmailDigestResponse` with `introduction` and `articles`
- `EmailDigestResponse.to_markdown()` renders the full email as markdown

---

## Database (`app/database/`)

### [models.py](app/database/models.py)
Seven SQLAlchemy ORM models:

| Table | Primary Key | Notable Columns |
|---|---|---|
| `youtube_videos` | `video_id` | `transcript` (null → `__UNAVAILABLE__` if fetching fails) |
| `openai_articles` | `guid` | `description` (RSS excerpt used as content) |
| `anthropic_articles` | `guid` | `markdown` (null until step 2 enrichment) |
| `digests` | `"{type}:{article_id}"` | `created_at` mirrors source article's `published_at`; `channel_id` for YouTube filtering |
| `users` | UUID | `email`, `password_hash`, `interests` (ARRAY), `is_active` |
| `user_youtube_channels` | autoincrement int | `(user_id, channel_id)` unique constraint |
| `user_digest_sends` | **(user_id, digest_id) composite** | `sent_at`; prevents duplicate email sends per user |

### [repository.py](app/database/repository.py)
All database reads and writes. Key methods:

**Content:**
- `bulk_create_*()` — idempotent insert; checks by primary key before inserting
- `get_anthropic_articles_without_markdown()` — feeds step 2
- `get_youtube_videos_without_transcript()` — feeds step 3
- `get_articles_without_digest()` — joins all three tables, applies eligibility rules
- `create_digest()` — inserts into `digests`; sets `created_at` from source `published_at`
- `get_recent_digests(hours)` — returns digests within the last N hours

**Email idempotency:**
- `get_unsent_digests_for_user(user_id, hours, channel_ids=None)` — returns recent digests not yet in `user_digest_sends` for this user; filters YouTube by `channel_ids` if provided
- `mark_digests_sent(user_id, digest_ids)` — bulk-inserts into `user_digest_sends`; check-before-insert (same pattern as all other repo writes)

**Users / channels:**
- `create_user / get_user_by_email / get_user_by_id / update_user_profile / get_all_active_users`
- `add_user_channel / remove_user_channel / get_user_channels / get_all_active_channel_ids`

### [create_tables.py](app/database/create_tables.py)
Idempotent table creation via `Base.metadata.create_all(engine)`. Called automatically by `daily_runner.py` at startup.

---

## Scrapers (`app/scrapers/`)

| File | Source | Method |
|---|---|---|
| [youtube.py](app/scrapers/youtube.py) | YouTube RSS per channel | `requests` → `feedparser`; transcripts via `youtube-transcript-api`; shorts (`/shorts/`) are skipped |
| [openai.py](app/scrapers/openai.py) | `openai.com/news/rss.xml` | `requests` → `feedparser` |
| [anthropic.py](app/scrapers/anthropic.py) | 3 third-party RSS mirrors (news, research, engineering) | `requests` → `feedparser`; full text via `requests` + `BeautifulSoup` |

All scrapers use `requests.get()` first and pass `response.content` to `feedparser.parse()`. Direct URL parsing fails silently on this platform.

---

## User Profile (`app/profiles/`)

`user_profile.py` contains `USER_PROFILE` — a fallback dict used when no active DB users exist. For real users, `build_user_profile_dict(user: User)` in `process_email.py` converts the `User` ORM object into the same dict format that `CuratorAgent` and `EmailAgent` expect.

---

## Configuration (`app/config.py`)

```python
DEFAULT_YOUTUBE_CHANNELS = ["UCawZsQWqfGSbCI5yjkdVkTA", ...]  # fallback list
```

Used only when no active users have added channels. The live channel list comes from `repo.get_all_active_channel_ids()`.

---

## Services — Individual Step Scripts

Each service module can be run standalone:

| Script | Runs |
|---|---|
| `python app/services/process_anthropic.py` | Step 2 only |
| `python app/services/process_youtube.py` | Step 3 only |
| `python app/services/process_digest.py` | Step 4 only (includes JudgeAgent) |
| `python app/services/process_email.py` | Step 5 only (single-user legacy path) |

---

## Email Utilities (`app/services/email_utils.py`)

- `send_email(subject, body_text, body_html, recipients)` — sends via Resend API using `RESEND_API_KEY`. Sender: `onboarding@resend.dev`; defaults to `MY_EMAIL` if no recipients given.
- `digest_to_html(digest_response)` — renders `EmailDigestResponse` to styled HTML.
- `markdown_to_html(markdown_text)` — wraps rendered markdown in a full HTML document with inline CSS.

---

## Environment Variables

| Variable | Used By |
|---|---|
| `GROQ_API_KEY` | All agents (DigestAgent, JudgeAgent, CuratorAgent, EmailAgent) |
| `OPENAI_API_KEY` | All agents (fallback) |
| `DATABASE_URL` | `app/database/connection.py` (Railway injects this automatically) |
| `POSTGRES_USER/PASSWORD/DB/HOST/PORT` | `app/database/connection.py` (local Docker fallback) |
| `MY_EMAIL` | Default digest recipient (used by legacy `send_digest_email()`) |
| `RESEND_API_KEY` | `app/services/email_utils.py` |
| `SECRET_KEY` | JWT signing |
| `PROXY_USERNAME` / `PROXY_PASSWORD` | Optional Webshare proxy for YouTube transcripts |

---

## Data Flow Diagram

```
RSS Feeds (YouTube/OpenAI/Anthropic)
        │
        ▼
  app/scrapers/          ← parse + filter by hours
        │
        ▼
  Postgres DB
  (youtube_videos,
   openai_articles,
   anthropic_articles)
        │
    ┌───┴────────────────────────┐
    ▼                            ▼
process_anthropic.py      process_youtube.py
(fetch full HTML text)    (fetch transcript text)
    │                            │
    └───────────┬────────────────┘
                ▼
        process_digest.py
        ┌───────┴────────────┐
        ▼                    ▼
  DigestAgent           JudgeAgent
  (generate summary)    (score ≥ 0.7?)
        │                    │
        └──── if pass ───────┘
                   │
             digests table
                   │
         ┌─────────▼──────────┐
         │  per active user   │
         └─────────┬──────────┘
                   │
         get_unsent_digests_for_user()
         (filter by user channels +
          exclude already-sent)
                   │
    ┌──────────────┴────────────────┐
    ▼                               ▼
CuratorAgent                   EmailAgent
(rank by user profile)         (write personalized intro)
    │                               │
    └──────────────┬────────────────┘
                   ▼
           Resend API → user inbox
                   │
           mark_digests_sent()
           → user_digest_sends table
```

---

## Project Structure

```
app/
├── agent/
│   ├── digest_agent.py      # DigestAgent (title + summary) + JudgeAgent (quality gate)
│   ├── curator_agent.py     # CuratorAgent: ranks digests by user profile
│   └── email_agent.py       # EmailAgent: writes personalized email intro
├── database/
│   ├── models.py            # 7 SQLAlchemy ORM models (incl. UserDigestSend)
│   ├── repository.py        # All DB reads + writes (incl. email idempotency methods)
│   ├── connection.py        # Engine + session (DATABASE_URL → POSTGRES_* fallback)
│   └── create_tables.py     # Idempotent table creation
├── pipeline/                # LangGraph orchestration layer
│   ├── state.py             # PipelineState TypedDict
│   ├── workflow.py          # StateGraph definition + build_pipeline_graph()
│   └── nodes/
│       ├── scrape.py        # scrape node
│       ├── process.py       # process node (anthropic + youtube enrichment)
│       ├── digest.py        # digest node
│       ├── email.py         # send_email node (per-user loop)
│       └── finalize.py      # finalize node (success flag)
├── scrapers/
│   ├── youtube.py           # YouTube RSS + transcript fetching
│   ├── openai.py            # OpenAI RSS scraper
│   └── anthropic.py         # Anthropic RSS + full-text fetching
├── services/
│   ├── process_anthropic.py # Anthropic full-text enrichment
│   ├── process_youtube.py   # YouTube transcript fetching
│   ├── process_digest.py    # Digest generation (DigestAgent + JudgeAgent)
│   ├── process_email.py     # Email curation + sending (multi-user + idempotency)
│   └── email_utils.py       # Resend API wrapper + HTML rendering
├── web/
│   ├── app.py               # FastAPI factory
│   ├── auth.py              # bcrypt hashing, JWT create/verify, get_current_user
│   ├── dependencies.py      # DB session dependency
│   ├── routes/
│   │   ├── auth.py          # GET/POST /signup, /login, /logout
│   │   └── dashboard.py     # GET /dashboard, POST /settings, /channels/add, /channels/remove
│   └── templates/
│       ├── base.html        # Tailwind CDN layout
│       ├── signup.html
│       ├── login.html
│       └── dashboard.html
├── profiles/
│   └── user_profile.py      # Fallback profile (used when no DB users exist)
├── config.py                # DEFAULT_YOUTUBE_CHANNELS (fallback only)
├── runner.py                # Scraping entry point
└── daily_runner.py          # Full pipeline: calls pipeline_graph.invoke()
tests/
└── test_web.py              # Web auth + dashboard tests (pytest, requires Docker DB)
docker/
├── docker-compose.yaml
└── example.env
```
