# AI News Aggregator

A personalized AI news digest pipeline that scrapes content from YouTube, OpenAI, and Anthropic, summarizes it with GPT, and emails you a curated daily briefing tailored to your interests.

## How it works

The pipeline runs five sequential steps:

```
Scrape → Fetch full text → Fetch transcripts → Generate digests → Curate & email
```

1. **Scrape** — Pulls RSS feeds from YouTube channels, openai.com/news, and Anthropic (news/research/engineering). Stores raw articles in PostgreSQL.
2. **Anthropic full text** — Fetches and cleans the full HTML of each Anthropic article into markdown.
3. **YouTube transcripts** — Fetches transcripts for each video via `youtube-transcript-api`.
4. **Digest generation** — For each undigested article, calls GPT-4o-mini to produce a title + 2–3 sentence summary.
5. **Curate & email** — GPT-4.1 ranks digests by your profile interests; GPT-4o-mini writes a personalised intro; sends via Gmail SMTP.

## Setup

### Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv)
- Docker (for PostgreSQL)

### Install dependencies

```bash
uv sync
```

### Start the database

```bash
cd docker && docker compose up -d
```

> **Note:** If a local PostgreSQL is already running on port 5432, stop it first with `pg_ctl -D /usr/local/var/postgresql@17 stop` (brew stop alone may not fully kill the process), or remap Docker to a different port in `docker/docker-compose.yaml`.

**Verify the database is up:**

```bash
# Container should show (healthy)
docker ps

# Tables should exist
docker exec -it ai-news-aggregator-db psql -U postgres -d ai_news_aggregator -c "\dt"

# Row counts across all tables
docker exec -it ai-news-aggregator-db psql -U postgres -d ai_news_aggregator -c "
SELECT 'youtube_videos' AS table, COUNT(*) FROM youtube_videos
UNION ALL SELECT 'openai_articles', COUNT(*) FROM openai_articles
UNION ALL SELECT 'anthropic_articles', COUNT(*) FROM anthropic_articles
UNION ALL SELECT 'digests', COUNT(*) FROM digests;"
```

If `\dt` returns no tables, run the one-time setup first:
```bash
uv run python app/database/create_tables.py
```

### Configure environment

```bash
cp docker/example.env .env
```

Edit `.env` and fill in your credentials:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key (used by all three agents) |
| `MY_EMAIL` | Gmail address to send from |
| `APP_PASSWORD` | Gmail [App Password](https://myaccount.google.com/apppasswords) (requires 2FA enabled) |
| `POSTGRES_USER` | DB username (default: `postgres`) |
| `POSTGRES_PASSWORD` | DB password (default: `postgres`) |
| `POSTGRES_DB` | DB name (default: `ai_news_aggregator`) |
| `POSTGRES_HOST` | DB host (default: `localhost`) |
| `POSTGRES_PORT` | DB port (default: `5432`) |
| `PROXY_USERNAME` / `PROXY_PASSWORD` | Optional Webshare proxy for YouTube transcripts |

> **Note:** If a local PostgreSQL is already running on port 5432, stop it first (`brew services stop postgresql`) or remap Docker to a different port in `docker/docker-compose.yaml`.

### Create database tables

```bash
python app/database/create_tables.py
```

## Running the pipeline

**Full daily pipeline (all 5 steps):**
```bash
python -m app.daily_runner
```

**Individual steps:**
```bash
python -m app.runner                        # scrape only
python app/services/process_anthropic.py   # fetch Anthropic full text
python app/services/process_youtube.py     # fetch YouTube transcripts
python app/services/process_digest.py      # generate digests
python app/services/process_email.py       # curate and send email
```

**Scrape only (alias):**
```bash
python main.py
```

All steps are idempotent — safe to re-run; already-processed records are skipped.

## Personalisation

Edit `app/profiles/user_profile.py` to match your background and interests. Both the curator (ranking) and email agent (intro writing) use this profile to tailor the output.

```python
DEFAULT_PROFILE = {
    "name": "Your Name",
    "background": "...",
    "interests": ["LLMs", "AI agents", ...],
    "preferences": {
        "content_depth": "Technical but accessible",
        ...
    },
}
```

## Project structure

```
app/
├── agent/
│   ├── digest_agent.py      # GPT-4o-mini: title + summary per article
│   ├── curator_agent.py     # GPT-4.1: ranks digests by user profile
│   └── email_agent.py       # GPT-4o-mini: writes email intro
├── database/
│   ├── models.py            # SQLAlchemy ORM models
│   ├── repository.py        # DB read/write operations
│   ├── connection.py        # Engine + session setup
│   └── create_tables.py     # One-time table creation
├── scrapers/
│   ├── youtube.py           # YouTube RSS + transcript fetching
│   ├── openai.py            # OpenAI RSS scraper
│   └── anthropic.py         # Anthropic RSS scraper
├── services/
│   ├── process_anthropic.py # Full-text fetching for Anthropic articles
│   ├── process_youtube.py   # Transcript fetching for YouTube videos
│   ├── process_digest.py    # Digest generation orchestration
│   └── process_email.py     # Email curation and sending
├── profiles/
│   └── user_profile.py      # User interests and preferences
├── runner.py                # Scraping entry point
└── daily_runner.py          # Full pipeline entry point
docker/
├── docker-compose.yaml
└── example.env
```

## Tech stack

- **Python 3.14** with `uv` for dependency management
- **PostgreSQL** via SQLAlchemy ORM + psycopg2
- **OpenAI Responses API** for structured agent outputs (Pydantic models)
- **feedparser** + **requests** + **beautifulsoup4** for scraping
- **youtube-transcript-api** for video transcripts
- **Gmail SMTP** for email delivery
