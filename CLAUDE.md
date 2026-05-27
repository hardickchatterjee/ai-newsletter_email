# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This project uses `uv` for dependency management (Python 3.14+).

```bash
# Install dependencies
uv sync

# Start the web app (http://localhost:8000)
uvicorn app.web.app:app --reload

# Run the full daily pipeline (scrape → process → digest → email)
python -m app.daily_runner

# Run scrapers only
python -m app.runner

# Run individual service steps
python app/services/process_anthropic.py
python app/services/process_youtube.py
python app/services/process_digest.py
python app/services/process_email.py

# Run tests
uv run pytest tests/ -v

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
| `SECRET_KEY` | Secret for signing JWTs — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
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
3. Railway auto-detects `render.yaml` and provisions the web service + cron service + Postgres
4. Set secret env vars in Railway dashboard: `GROQ_API_KEY`, `OPENAI_API_KEY`, `RESEND_API_KEY`, `MY_EMAIL`, `SECRET_KEY`
5. `DATABASE_URL` is injected automatically from the linked Railway Postgres
6. Trigger a manual run to verify

**Schedule:** `0 7 * * *` (7 AM UTC daily). Edit in `render.yaml` to change.

## Architecture

### Pipeline Flow

`main.py` runs only the scraping step. The full pipeline lives in `app/daily_runner.py`, which invokes a **LangGraph StateGraph** (`app/pipeline/workflow.py`) with five nodes running in sequence:

```
scrape → process → digest → send_email → finalize
```

1. **scrape** (`app/pipeline/nodes/scrape.py` → `app/runner.py`) — Fetches from three sources via RSS feeds and stores to Postgres. YouTube channels are read from the `user_youtube_channels` DB table (falls back to `DEFAULT_YOUTUBE_CHANNELS` in `app/config.py` if no users have added channels).

2. **process** (`app/pipeline/nodes/process.py`) — Two enrichment steps run in sequence:
   - `process_anthropic.py` — Fetches full article HTML using `requests` + `beautifulsoup4`, stores in `anthropic_articles.markdown`
   - `process_youtube.py` — Fetches transcripts via `youtube-transcript-api`; marks unavailable as `__UNAVAILABLE__`

3. **digest** (`app/pipeline/nodes/digest.py` → `app/services/process_digest.py`) — For each article without a digest:
   - `DigestAgent` generates a title + 2-3 sentence summary
   - `JudgeAgent` scores quality (0–1); only summaries scoring ≥ 0.7 are stored in `digests`

4. **send_email** (`app/pipeline/nodes/email.py` → `app/services/process_email.py`) — Loops over all active users. Per user: fetches only **unsent** digests (checked against `user_digest_sends`), applies YouTube channel filter, `CuratorAgent` ranks, `EmailAgent` writes intro, sends via Resend, records sent IDs in `user_digest_sends`.

5. **finalize** (`app/pipeline/nodes/finalize.py`) — Assembles the `success` flag from error counts.

### Key Design Decisions

- **LangGraph orchestration**: The pipeline is a `StateGraph` with a `PipelineState` TypedDict passed between nodes. No checkpointer — state is in-memory per run. Nodes are thin wrappers; all logic lives in the service files.
- **Idempotency**: All write operations check for existing records by primary key before inserting. Digest IDs are composite strings `"{article_type}:{article_id}"`. Email idempotency is enforced by the `user_digest_sends` join table — same digest is never sent to the same user twice.
- **LLM-as-a-judge**: After `DigestAgent` generates a summary, `JudgeAgent` evaluates it (score ≥ 0.7 to pass). Articles that fail remain "without digest" and are retried on the next pipeline run.
- **RSS fetching pattern**: All scrapers fetch RSS content via `requests` first, then pass `response.content` to `feedparser.parse()`. Direct URL parsing via feedparser fails silently on this platform.
- **Anthropic-only full text**: Only Anthropic articles get full-text fetching (via requests + bs4). OpenAI relies on RSS description; YouTube relies on transcripts.
- **Digest eligibility**: YouTube videos only get digests if they have a non-null, non-`__UNAVAILABLE__` transcript. Anthropic articles only get digests after markdown is populated.
- **Auth**: bcrypt via the `bcrypt` package directly (not passlib — passlib is broken on Python 3.14). JWTs signed with `python-jose`, stored in HTTP-only cookies with 7-day TTL.
- **TemplateResponse API**: Starlette 0.29+ changed the signature to `TemplateResponse(request, name, context)` — `request` is the first positional arg and is NOT included in the context dict.
- **User profiles**: Stored in the `users` DB table. `app/profiles/user_profile.py` is a fallback used only when no active users exist in the DB.
- **LLM provider**: All agents use Groq (`llama-3.3-70b-versatile`) via the OpenAI-compatible SDK (`base_url="https://api.groq.com/openai/v1"`). `OPENAI_API_KEY` is kept as a fallback.
- **Structured output**: Agents use `response_format={"type": "json_object"}` with `json.loads()` + Pydantic model construction.
- **Email provider**: Resend API (`resend` Python SDK). Railway blocks outbound SMTP, so Gmail SMTP was replaced. The sender is `onboarding@resend.dev` (free tier); recipient is `MY_EMAIL`.
- **No docling**: `docling` cannot be installed on Intel Mac (macOS 13 x86_64) due to torch platform incompatibility. Use `requests` + `beautifulsoup4` for URL-to-text extraction instead.
- **Database connection**: `app/database/connection.py` checks `DATABASE_URL` first (used by Railway), then falls back to individual `POSTGRES_*` vars (used locally with Docker).
- **Table creation**: `daily_runner.py` calls `create_tables()` at startup — idempotent, safe to run every day. No manual setup needed on fresh deploys.
- **Dependency management**: `requirements.txt` is committed and generated via `uv export --frozen --no-dev`. Railway uses it; local dev uses `uv sync`.
- **Tests**: `tests/test_web.py` runs against the real PostgreSQL instance (Docker must be up). Each test fixture creates and cleans up its own data.

### Database Schema

Seven tables managed by SQLAlchemy ORM (`app/database/models.py`):
- `youtube_videos` — primary key: `video_id`
- `openai_articles` — primary key: `guid`
- `anthropic_articles` — primary key: `guid`; has `markdown` column populated by step 2
- `digests` — primary key: `"{article_type}:{article_id}"`; `created_at` is set to the source article's `published_at`; has `channel_id` for YouTube filtering
- `users` — primary key: UUID; stores email, bcrypt password hash, profile fields
- `user_youtube_channels` — join table (user_id + channel_id); UNIQUE constraint prevents duplicates
- `user_digest_sends` — composite primary key (user_id + digest_id); tracks which digests have been sent to each user to prevent duplicate emails
