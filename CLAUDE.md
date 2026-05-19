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

# Create database tables (run once on first setup)
python app/database/create_tables.py

# Start the PostgreSQL database
cd docker && docker compose up -d
```

There are no tests in this repository yet.

## Environment Setup

Copy `docker/example.env` to `.env` in the project root. Required variables:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Used by all three agents (DigestAgent, CuratorAgent, EmailAgent) |
| `POSTGRES_USER/PASSWORD/DB/HOST/PORT` | PostgreSQL connection (defaults: postgres/postgres/ai_news_aggregator/localhost/5432) |
| `MY_EMAIL` / `APP_PASSWORD` | Gmail sender credentials for email delivery |
| `PROXY_USERNAME` / `PROXY_PASSWORD` | Optional Webshare proxy for YouTube transcript fetching |

**Note:** If a local Postgres is already running on port 5432, it will intercept connections before Docker. Either stop the local instance (`brew services stop postgresql`) or remap Docker to a different port.

## Architecture

### Pipeline Flow

`main.py` runs only the scraping step. The full pipeline lives in `app/daily_runner.py` and runs five sequential steps:

1. **Scraping** (`app/runner.py`) — Fetches from three sources via RSS feeds and stores to Postgres:
   - YouTube: RSS feed per channel → transcript via `youtube-transcript-api`
   - OpenAI: RSS feed from `openai.com/news`
   - Anthropic: Three RSS feeds (news, research, engineering) via a third-party mirror

2. **Anthropic Markdown** (`app/services/process_anthropic.py`) — Fetches full article HTML using `requests` + `beautifulsoup4` and stores cleaned text in `anthropic_articles.markdown`.

3. **YouTube Transcripts** (`app/services/process_youtube.py`) — Fetches transcripts for videos stored without one; marks unavailable transcripts as `__UNAVAILABLE__`.

4. **Digest Generation** (`app/services/process_digest.py`) — For each article without a digest, calls `DigestAgent` (gpt-4o-mini) to generate a title + 2-3 sentence summary stored in the `digests` table.

5. **Email** (`app/services/process_email.py`) — Loads the user profile from `app/profiles/`, calls `CuratorAgent` (gpt-4.1) to rank all recent digests, then `EmailAgent` (gpt-4o-mini) to write the intro, and sends via Gmail SMTP.

### Key Design Decisions

- **Idempotency**: All write operations check for existing records by primary key before inserting. Digest IDs are composite strings `"{article_type}:{article_id}"`.
- **RSS fetching pattern**: All scrapers fetch RSS content via `requests` first, then pass `response.content` to `feedparser.parse()`. Direct URL parsing via feedparser fails silently on this platform.
- **Anthropic-only full text**: Only Anthropic articles get full-text fetching (via requests + bs4). OpenAI relies on RSS description; YouTube relies on transcripts.
- **Digest eligibility**: YouTube videos only get digests if they have a non-null, non-`__UNAVAILABLE__` transcript. Anthropic articles only get digests after markdown is populated.
- **User profiles** (`app/profiles/default_profile.py`): Dicts with keys `name`, `background`, `expertise_level`, `interests` (list), `preferences` (dict). Imported via `app/profiles/__init__.py` as `DEFAULT_PROFILE`. Both `CuratorAgent` and `EmailAgent` receive the profile at construction time.
- **OpenAI SDK usage**: All agents use `client.responses.parse(...)` with `text_format=<PydanticModel>` for structured output — this is the Responses API, not the Chat Completions API.
- **No docling**: `docling` cannot be installed on Intel Mac (macOS 13 x86_64) due to torch platform incompatibility. Use `requests` + `beautifulsoup4` for URL-to-text extraction instead.

### Database Schema

Four tables managed by SQLAlchemy ORM (`app/database/models.py`):
- `youtube_videos` — primary key: `video_id`
- `openai_articles` — primary key: `guid`
- `anthropic_articles` — primary key: `guid`; has `markdown` column populated by step 2
- `digests` — primary key: `"{article_type}:{article_id}"`; `created_at` is set to the source article's `published_at`
