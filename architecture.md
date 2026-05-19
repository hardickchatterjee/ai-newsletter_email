# Architecture

This document explains the full structure of the AI News Aggregator: what each file does, how the pipeline flows, and how all the pieces connect.

---

## High-Level Overview

The system scrapes AI news from three sources (YouTube, OpenAI, Anthropic), enriches the raw data, generates AI-written digests, ranks them against a user profile, and delivers a personalized email newsletter.

```
main.py / daily_runner.py
        │
        ▼
[1] app/runner.py          ← scrapes all three sources → Postgres
        │
        ▼
[2] process_anthropic.py   ← fetches full article text for Anthropic posts
        │
        ▼
[3] process_youtube.py     ← fetches transcripts for YouTube videos
        │
        ▼
[4] process_digest.py      ← DigestAgent generates title + summary per article
        │
        ▼
[5] process_email.py       ← CuratorAgent ranks → EmailAgent writes intro → Gmail
```

---

## Entry Points

### [main.py](main.py)
Runs **scraping only** (step 1). Calls `run_scrapers(hours=24)` and prints counts.

```
python main.py
```

### [app/daily_runner.py](app/daily_runner.py)
Runs the **full 5-step pipeline** end-to-end. Accepts `hours` (lookback window) and `top_n` (articles in email). Each step is logged; the return dict contains per-step results and a `success` flag.

```
python -m app.daily_runner
```

---

## Pipeline Steps

### Step 1 — Scraping (`app/runner.py`)

`run_scrapers(hours)` instantiates all three scrapers and the repository, then:

1. Iterates over `YOUTUBE_CHANNELS` from `app/config.py` and calls `YouTubeScraper.get_latest_videos()` per channel.
2. Calls `OpenAIScraper.get_articles()` and `AnthropicScraper.get_articles()`.
3. Bulk-inserts new records into Postgres via `Repository`. Existing primary keys are silently skipped (idempotent).

Returns a dict of lists: `{"youtube": [...], "openai": [...], "anthropic": [...]}`.

---

### Step 2 — Anthropic Full Text (`app/services/process_anthropic.py`)

Queries all `anthropic_articles` rows where `markdown IS NULL`, then for each:
- Calls `AnthropicScraper.url_to_markdown(url)` — fetches the page with `requests`, strips scripts/nav/footer via `BeautifulSoup`, collapses whitespace.
- Writes the result back to `anthropic_articles.markdown`.

Anthropic is the only source that gets full-text enrichment. OpenAI uses the RSS description; YouTube uses transcripts.

---

### Step 3 — YouTube Transcripts (`app/services/process_youtube.py`)

Queries all `youtube_videos` rows where `transcript IS NULL`, then for each:
- Calls `YouTubeScraper.get_transcript(video_id)` via `youtube-transcript-api`.
- On success: stores the joined transcript text.
- On failure or unavailability: stores the sentinel string `__UNAVAILABLE__`.

The `__UNAVAILABLE__` sentinel prevents re-fetching and blocks digest generation for that video.

---

### Step 4 — Digest Generation (`app/services/process_digest.py`)

Queries all articles that don't yet have a digest (via `Repository.get_articles_without_digest()`). Eligibility rules enforced in the repository:
- YouTube: must have a non-null, non-`__UNAVAILABLE__` transcript.
- Anthropic: must have a non-null `markdown`.
- OpenAI: always eligible (uses RSS description as content).

For each eligible article, calls `DigestAgent.generate_digest(title, content, article_type)` which calls `gpt-4o-mini` via the OpenAI Responses API and returns a `DigestOutput(title, summary)`. The result is inserted into the `digests` table with `created_at` set to the source article's `published_at`.

Digest IDs are composite strings: `"{article_type}:{article_id}"` (e.g. `"youtube:abc123"`).

---

### Step 5 — Email (`app/services/process_email.py`)

Two sub-steps:

**Curation** — `CuratorAgent.rank_digests(digests)` receives all recent digests and calls `gpt-4.1` to score each on relevance (0–10) against the user profile. Returns a ranked list of `RankedArticle` objects.

**Email generation** — `EmailAgent.generate_introduction(ranked_articles)` calls `gpt-4o-mini` to write a personalized greeting and 2–3 sentence intro previewing the top articles. Then `create_email_digest_response()` assembles the full `EmailDigestResponse`.

**Sending** — `email_utils.send_email()` sends via Gmail SMTP (port 465 SSL) using `MY_EMAIL` and `APP_PASSWORD` from the environment. The email is sent as both plain-text (markdown) and HTML (rendered via the `markdown` library with custom CSS).

---

## Scrapers (`app/scrapers/`)

| File | Source | Method |
|---|---|---|
| [youtube.py](app/scrapers/youtube.py) | YouTube RSS per channel | `requests` → `feedparser`; transcripts via `youtube-transcript-api` |
| [openai.py](app/scrapers/openai.py) | `openai.com/news/rss.xml` | `requests` → `feedparser` |
| [anthropic.py](app/scrapers/anthropic.py) | 3 third-party RSS mirrors (news, research, engineering) | `requests` → `feedparser`; full text via `requests` + `BeautifulSoup` |

All scrapers accept an `hours` parameter and filter entries by `published_at >= now - hours`. They use `requests.get()` first and pass `response.content` to `feedparser.parse()` — direct URL parsing via feedparser fails silently on this platform.

YouTube shorts (URLs containing `/shorts/`) are skipped during scraping.

---

## AI Agents (`app/agent/`)

### [digest_agent.py](app/agent/digest_agent.py) — `DigestAgent`
- Model: `gpt-4o-mini`
- Input: article title + content (truncated to 8,000 chars)
- Output: `DigestOutput(title: str, summary: str)`
- Uses OpenAI Responses API: `client.responses.parse(..., text_format=DigestOutput)`

### [curator_agent.py](app/agent/curator_agent.py) — `CuratorAgent`
- Model: `gpt-4.1`
- Constructed with `user_profile` dict; builds a system prompt embedding name, background, expertise level, interests, and preferences.
- Input: list of digest dicts (id, title, summary, type)
- Output: `List[RankedArticle]` — each with `digest_id`, `rank`, `relevance_score`, `reasoning`

### [email_agent.py](app/agent/email_agent.py) — `EmailAgent`
- Model: `gpt-4o-mini`
- Constructed with `user_profile` dict.
- Input: top-N `RankedArticle` objects
- Output: `EmailIntroduction(greeting, introduction)` — assembled into `EmailDigestResponse`
- `EmailDigestResponse.to_markdown()` renders the full email as markdown.

---

## Database (`app/database/`)

### [connection.py](app/database/connection.py)
Reads `POSTGRES_*` env vars, builds the SQLAlchemy engine and `SessionLocal` factory. `get_session()` returns a new session.

### [models.py](app/database/models.py)
Four SQLAlchemy ORM models:

| Table | Primary Key | Notable Columns |
|---|---|---|
| `youtube_videos` | `video_id` | `transcript` (null until step 3; `__UNAVAILABLE__` if unavailable) |
| `openai_articles` | `guid` | `description` (RSS excerpt used as content) |
| `anthropic_articles` | `guid` | `markdown` (null until step 2) |
| `digests` | `"{type}:{article_id}"` | `created_at` mirrors source article's `published_at` |

### [repository.py](app/database/repository.py)
All database reads and writes. Key methods:

- `bulk_create_*()` — idempotent insert; checks by primary key before inserting.
- `get_anthropic_articles_without_markdown()` — feeds step 2.
- `get_youtube_videos_without_transcript()` — feeds step 3.
- `get_articles_without_digest()` — joins all three tables, filters by eligibility rules, excludes already-digested articles.
- `create_digest()` — inserts into `digests`; sets `created_at` from source `published_at`.
- `get_recent_digests(hours)` — returns digests created within the last N hours for email generation.

### [create_tables.py](app/database/create_tables.py)
One-time setup script. Run once on first install to create all tables via `Base.metadata.create_all(engine)`.

---

## User Profile (`app/profiles/`)

### [user_profile.py](app/profiles/user_profile.py)
A single dict `DEFAULT_PROFILE` (aliased to `USER_PROFILE` via `__init__.py`) with keys:

```python
{
    "name": "Hardick",
    "background": "...",
    "expertise_level": "Intermediate to Advanced",
    "interests": [...],
    "preferences": {"content_depth": "...", "content_type": "...", "format": "..."}
}
```

Both `CuratorAgent` and `EmailAgent` receive this at construction time. Edit this file to change what content gets surfaced and how the email reads.

---

## Configuration (`app/config.py`)

```python
YOUTUBE_CHANNELS = ["UCawZsQWqfGSbCI5yjkdVkTA"]  # Matthew Berman
```

Add channel IDs here to scrape additional YouTube channels.

---

## Services — Individual Step Scripts

Each service module has an `if __name__ == "__main__"` block so it can be run standalone:

| Script | Runs |
|---|---|
| `python app/services/process_anthropic.py` | Step 2 only |
| `python app/services/process_youtube.py` | Step 3 only |
| `python app/services/process_digest.py` | Step 4 only |
| `python app/services/process_email.py` | Step 5 only (generate + send) |

---

## Email Utilities (`app/services/email_utils.py`)

- `send_email(subject, body_text, body_html, recipients)` — SMTP send via Gmail SSL.
- `digest_to_html(digest_response)` — renders `EmailDigestResponse` to a styled HTML email; falls back to `markdown_to_html()` for other types.
- `markdown_to_html(markdown_text)` — wraps rendered markdown in a full HTML document with inline CSS.

---

## Environment Variables

All secrets are loaded from `.env` in the project root (copy from `docker/example.env`):

| Variable | Used By |
|---|---|
| `OPENAI_API_KEY` | All three agents |
| `POSTGRES_USER/PASSWORD/DB/HOST/PORT` | `app/database/connection.py` |
| `MY_EMAIL` | Sender address and default recipient |
| `APP_PASSWORD` | Gmail App Password for SMTP auth |
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
  app/database/          ← idempotent upsert to Postgres
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
        (DigestAgent → gpt-4o-mini)
                │
                ▼
          digests table
                │
                ▼
        process_email.py
        ┌───────┴────────────┐
        ▼                    ▼
  CuratorAgent          EmailAgent
  (gpt-4.1)             (gpt-4o-mini)
  rank digests          write intro
        │                    │
        └──────────┬─────────┘
                   ▼
           Gmail SMTP → inbox
```
