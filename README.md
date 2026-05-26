# 🗞️ AI News Aggregator

> I got tired of manually checking OpenAI, Anthropic, and YouTube every morning for AI updates. So I built a pipeline that does it for me — scrapes, summarizes, ranks by my interests, and emails me a personalized digest every day at 7 AM.

A production multi-agent pipeline built with three specialized LLM agents, PostgreSQL, and deployed as a Railway cron job. Not a wrapper — a real automated system that runs daily without any manual intervention.

---

## 🧠 How It's Built

Three specialized agents handle distinct responsibilities:

| Agent | Role |
|---|---|
| `DigestAgent` | Reads each raw article/transcript → generates a title + 2–3 sentence summary |
| `CuratorAgent` | Scores and ranks all digests against your interest profile |
| `EmailAgent` | Writes a personalized intro and assembles the final email |

Each agent is isolated — single responsibility, single prompt, testable independently.

---

## How it works

The pipeline runs five sequential steps each day:

```
Scrape → Fetch full text → Fetch transcripts → Generate digests → Curate & email
```

1. **Scrape** — Pulls RSS feeds from YouTube channels, openai.com/news, and Anthropic (news/research/engineering). Stores raw articles in PostgreSQL.
2. **Anthropic full text** — Fetches and cleans the full HTML of each Anthropic article into markdown.
3. **YouTube transcripts** — Fetches transcripts for each video via `youtube-transcript-api`.
4. **Digest generation** — For each undigested article, calls an LLM to produce a title + 2–3 sentence summary.
5. **Curate & email** — LLM ranks digests by your profile interests, writes a personalised intro, and sends via [Resend](https://resend.com).

All steps are idempotent — safe to re-run at any time; already-processed records are skipped.

---

## Setup

### Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv)
- Docker (for local PostgreSQL)

### 1. Install dependencies

```bash
uv sync
```

### 2. Start the database

```bash
cd docker && docker compose up -d
```

> If a local PostgreSQL is already running on port 5432, stop it first:
> ```bash
> pg_ctl -D /usr/local/var/postgresql@17 stop
> ```

Verify it's healthy:

```bash
docker ps  # STATUS should show (healthy)
docker exec -it ai-news-aggregator-db psql -U postgres -d ai_news_aggregator -c "\dt"
```

### 3. Configure environment

```bash
cp docker/example.env .env
```

Edit `.env` and fill in your credentials:

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | [Groq](https://console.groq.com) API key — used by all three agents |
| `OPENAI_API_KEY` | OpenAI API key — fallback if Groq is unavailable |
| `MY_EMAIL` | Your email address — where the digest is delivered |
| `RESEND_API_KEY` | [Resend](https://resend.com) API key for email delivery |
| `POSTGRES_USER` | DB username (default: `postgres`) |
| `POSTGRES_PASSWORD` | DB password (default: `postgres`) |
| `POSTGRES_DB` | DB name (default: `ai_news_aggregator`) |
| `POSTGRES_HOST` | DB host (default: `localhost`) |
| `POSTGRES_PORT` | DB port (default: `5432`) |
| `PROXY_USERNAME` / `PROXY_PASSWORD` | Optional Webshare proxy for YouTube transcripts |

> **Resend free tier note:** Without a verified domain, emails are sent from `onboarding@resend.dev` and can only be delivered to your own Resend account email. This is fine for personal use.

### 4. Run the pipeline

```bash
python -m app.daily_runner
```

Tables are created automatically on first run. No manual setup needed.

---

## Personalisation

Edit `app/profiles/user_profile.py` to match your background and interests. Both the curator (ranking) and email agent (intro) use this profile to tailor the output.

```python
DEFAULT_PROFILE = {
    "name": "Your Name",
    "background": "Software engineer working on AI applications",
    "expertise_level": "Intermediate to Advanced",
    "interests": ["LLMs", "AI agents", "Developer tools", ...],
    "preferences": {
        "content_depth": "Technical but accessible",
        "content_type": "Mix of research and practical applications",
        "format": "Concise summaries with key takeaways",
    },
}
```

To change which YouTube channels are scraped, edit `app/config.py`:

```python
YOUTUBE_CHANNELS = [
    "UCawZsQWqfGSbCI5yjkdVkTA",  # Matthew Berman
    # add more channel IDs here
]
```

---

## Running individual steps

```bash
python -m app.runner                        # scrape only
python app/services/process_anthropic.py   # fetch Anthropic full text
python app/services/process_youtube.py     # fetch YouTube transcripts
python app/services/process_digest.py      # generate digests
python app/services/process_email.py       # curate and send email
```

---

## Deployment (Railway)

The pipeline deploys to [Railway](https://railway.app) as a daily cron job.

1. Push to GitHub
2. Railway → New Project → Deploy from GitHub repo
3. Railway auto-detects `render.yaml` and provisions the cron service + Postgres
4. Add env vars in Railway dashboard: `GROQ_API_KEY`, `OPENAI_API_KEY`, `RESEND_API_KEY`, `MY_EMAIL`
5. `DATABASE_URL` is injected automatically from the linked Postgres service
6. Trigger a manual run to verify

**Schedule:** `0 7 * * *` (7 AM UTC daily)

After adding or updating dependencies, regenerate `requirements.txt` before deploying:

```bash
uv export --frozen --no-dev -o requirements.txt
```

---

## Project structure

```
app/
├── agent/
│   ├── digest_agent.py      # LLM: title + summary per article
│   ├── curator_agent.py     # LLM: ranks digests by user profile
│   └── email_agent.py       # LLM: writes personalized email intro
├── database/
│   ├── models.py            # SQLAlchemy ORM models
│   ├── repository.py        # All DB reads and writes
│   ├── connection.py        # Engine + session (DATABASE_URL → POSTGRES_* fallback)
│   └── create_tables.py     # Idempotent table creation
├── scrapers/
│   ├── youtube.py           # YouTube RSS + transcript fetching
│   ├── openai.py            # OpenAI RSS scraper
│   └── anthropic.py         # Anthropic RSS scraper + full-text fetching
├── services/
│   ├── process_anthropic.py # Full-text enrichment for Anthropic articles
│   ├── process_youtube.py   # Transcript fetching for YouTube videos
│   ├── process_digest.py    # Digest generation orchestration
│   ├── process_email.py     # Email curation and sending
│   └── email_utils.py       # Resend API wrapper + HTML rendering
├── profiles/
│   └── user_profile.py      # Your interests and preferences
├── runner.py                # Scraping entry point
└── daily_runner.py          # Full pipeline orchestrator
docker/
├── docker-compose.yaml
└── example.env
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.14, managed with `uv` |
| Database | PostgreSQL via SQLAlchemy ORM + psycopg2 |
| LLM | Groq (`llama-3.3-70b-versatile`) via OpenAI-compatible SDK |
| Scraping | `feedparser`, `requests`, `beautifulsoup4` |
| Transcripts | `youtube-transcript-api` |
| Email | [Resend](https://resend.com) API |
| Deployment | [Railway](https://railway.app) cron job |
