# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This project uses `uv` for dependency management (Python 3.14+).

```bash
# Install dependencies
uv sync

# Run scrapers and save to DB (entry point)
python main.py

# Run the full daily pipeline (scrape → process → digest → email)
python -m app.daily_runner

# Run scrapers only
python -m app.runner

# Run individual service steps
python app/services/process_anthropic.py
python app/services/process_youtube.py
python app/services/process_digest.py
python app/services/process_email.py

# Create database tables manually (daily_runner does this automatically now)
python app/database/create_tables.py

# Start the PostgreSQL database
cd docker && docker compose up -d

# Verify DB is healthy and tables exist
docker ps  # STATUS should show (healthy)
docker exec -it ai-news-aggregator-db psql -U postgres -d ai_news_aggregator -c "\dt"

# Check row counts
docker exec -it ai-news-aggregator-db psql -U postgres -d ai_news_aggregator -c "
SELECT 'youtube_videos' AS table, COUNT(*) FROM youtube_videos
UNION ALL SELECT 'openai_articles', COUNT(*) FROM openai_articles
UNION ALL SELECT 'anthropic_articles', COUNT(*) FROM anthropic_articles
UNION ALL SELECT 'digests', COUNT(*) FROM digests;"
```

There are no tests in this repository yet.

## Environment Setup

Copy `docker/example.env` to `.env` in the project root. Required variables:

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Primary LLM API key — all three agents (DigestAgent, CuratorAgent, EmailAgent) use Groq (`llama-3.3-70b-versatile`) |
| `OPENAI_API_KEY` | Fallback LLM API key if Groq is unavailable |
| `DATABASE_URL` | Full Postgres connection string — takes priority over individual vars (set by Railway automatically) |
| `POSTGRES_USER/PASSWORD/DB/HOST/PORT` | PostgreSQL connection (defaults: postgres/postgres/ai_news_aggregator/localhost/5432) — used when `DATABASE_URL` is not set |
| `MY_EMAIL` | Recipient email address for the digest |
| `RESEND_API_KEY` | Resend API key for email delivery (replaces Gmail SMTP) |
| `PROXY_USERNAME` / `PROXY_PASSWORD` | Optional Webshare proxy for YouTube transcript fetching |

**Note:** If a local Postgres is already running on port 5432, it will intercept connections before Docker. `brew services stop postgresql` alone may not fully kill the process — use `pg_ctl -D /usr/local/var/postgresql@17 stop` to ensure it's stopped, then restart the Docker container.

## Deployment (Railway)

The project deploys to Railway as a daily Cron Job. Config is in `render.yaml` (kept for reference; Railway auto-detects it on first connect).

```bash
# Regenerate requirements.txt after adding/updating dependencies
uv export --frozen --no-dev -o requirements.txt
```

**Deploy steps:**
1. Push to GitHub
2. Railway → New Project → Deploy from GitHub repo → select repo
3. Railway auto-detects `render.yaml` and provisions the cron service + Postgres
4. Set secret env vars in Railway dashboard: `GROQ_API_KEY`, `OPENAI_API_KEY`, `RESEND_API_KEY`, `MY_EMAIL`
5. `DATABASE_URL` is injected automatically from the linked Railway Postgres
6. Trigger a manual run to verify

**Schedule:** `0 7 * * *` (7 AM UTC daily). Edit in `render.yaml` to change.

## Architecture

### Pipeline Flow

`main.py` runs only the scraping step. The full pipeline lives in `app/daily_runner.py` and runs five sequential steps:

1. **Scraping** (`app/runner.py`) — Fetches from three sources via RSS feeds and stores to Postgres:
   - YouTube: RSS feed per channel → transcript via `youtube-transcript-api`
   - OpenAI: RSS feed from `openai.com/news`
   - Anthropic: Three RSS feeds (news, research, engineering) via a third-party mirror

2. **Anthropic Markdown** (`app/services/process_anthropic.py`) — Fetches full article HTML using `requests` + `beautifulsoup4` and stores cleaned text in `anthropic_articles.markdown`.

3. **YouTube Transcripts** (`app/services/process_youtube.py`) — Fetches transcripts for videos stored without one; marks unavailable transcripts as `__UNAVAILABLE__`.

4. **Digest Generation** (`app/services/process_digest.py`) — For each article without a digest, calls `DigestAgent` (llama-3.3-70b-versatile via Groq) to generate a title + 2-3 sentence summary stored in the `digests` table.

5. **Email** (`app/services/process_email.py`) — Loads the user profile from `app/profiles/`, calls `CuratorAgent` (llama-3.3-70b-versatile via Groq) to rank all recent digests, then `EmailAgent` (llama-3.3-70b-versatile via Groq) to write the intro, and sends via Resend API.

### Key Design Decisions

- **Idempotency**: All write operations check for existing records by primary key before inserting. Digest IDs are composite strings `"{article_type}:{article_id}"`.
- **RSS fetching pattern**: All scrapers fetch RSS content via `requests` first, then pass `response.content` to `feedparser.parse()`. Direct URL parsing via feedparser fails silently on this platform.
- **Anthropic-only full text**: Only Anthropic articles get full-text fetching (via requests + bs4). OpenAI relies on RSS description; YouTube relies on transcripts.
- **Digest eligibility**: YouTube videos only get digests if they have a non-null, non-`__UNAVAILABLE__` transcript. Anthropic articles only get digests after markdown is populated.
- **User profiles** (`app/profiles/default_profile.py`): Dicts with keys `name`, `background`, `expertise_level`, `interests` (list), `preferences` (dict). Imported via `app/profiles/__init__.py` as `DEFAULT_PROFILE`. Both `CuratorAgent` and `EmailAgent` receive the profile at construction time.
- **LLM provider**: All agents use Groq (`llama-3.3-70b-versatile`) via the OpenAI-compatible SDK (`base_url="https://api.groq.com/openai/v1"`). `OPENAI_API_KEY` is kept as a fallback.
- **Structured output**: All agents use `client.responses.parse(...)` with `text_format=<PydanticModel>` — this is the OpenAI Responses API pattern, not Chat Completions.
- **Email provider**: Resend API (`resend` Python SDK). Railway blocks outbound SMTP, so Gmail SMTP was replaced. The sender is `onboarding@resend.dev` (free tier); recipient is `MY_EMAIL`. `APP_PASSWORD` is no longer used.
- **No docling**: `docling` cannot be installed on Intel Mac (macOS 13 x86_64) due to torch platform incompatibility. Use `requests` + `beautifulsoup4` for URL-to-text extraction instead.
- **Database connection**: `app/database/connection.py` checks `DATABASE_URL` first (used by Railway), then falls back to individual `POSTGRES_*` vars (used locally with Docker).
- **Table creation**: `daily_runner.py` calls `create_tables()` at startup — idempotent, safe to run every day. No manual setup needed on fresh deploys.
- **Dependency management**: `requirements.txt` is committed and generated via `uv export --frozen --no-dev`. Render uses it; local dev uses `uv sync`.

### Database Schema

Four tables managed by SQLAlchemy ORM (`app/database/models.py`):
- `youtube_videos` — primary key: `video_id`
- `openai_articles` — primary key: `guid`
- `anthropic_articles` — primary key: `guid`; has `markdown` column populated by step 2
- `digests` — primary key: `"{article_type}:{article_id}"`; `created_at` is set to the source article's `published_at`
