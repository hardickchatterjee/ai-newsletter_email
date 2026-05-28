# AI News Aggregator

A personalized AI news digest platform that scrapes content from YouTube, OpenAI, and Anthropic, summarizes it with an LLM, and emails each user a curated daily briefing tailored to their interests.

Users sign up via a web app, configure their YouTube channels and interests, and receive a personalized digest every morning.

## How it works

### Daily pipeline

The pipeline is a **LangGraph `StateGraph`** with five nodes running in sequence:

```
scrape → process → digest → send_email → finalize
```

1. **scrape** — Pulls RSS feeds from YouTube channels, openai.com/news, and Anthropic (news/research/engineering). Stores raw articles in PostgreSQL.
2. **process** — Two enrichment steps: fetches full HTML of each Anthropic article into markdown, and fetches transcripts for YouTube videos via `youtube-transcript-api`.
3. **digest** — For each undigested article, `DigestAgent` generates a title + 2–3 sentence summary. `JudgeAgent` scores quality (0–1); only summaries scoring ≥ 0.7 are stored.
4. **send_email** — For each active user: `CuratorAgent` ranks digests by their profile interests, `EmailAgent` writes a personalised intro, sends via [Resend](https://resend.com), and records sent digest IDs to prevent duplicates.
5. **finalize** — Assembles the success flag from error counts.

All steps are idempotent — safe to re-run at any time; already-processed records are skipped.

### Web app

Users manage their account at `http://localhost:8000`:
- Sign up with email verification (link emailed via Resend; login is blocked until verified)
- Log in / log out (email + password, JWT in HTTP-only cookie, 7-day TTL)
- Forgot-password / reset-password flow (24h token, account-enumeration-safe)
- Edit profile: name, background, expertise level, interests, content depth, content type
- Add / remove YouTube channels (resolved by name from RSS)
- View available digests (last 240h, filtered to your channels) right on the dashboard

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
| `SECRET_KEY` | Secret for signing JWTs — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `POSTGRES_USER` | DB username (default: `postgres`) |
| `POSTGRES_PASSWORD` | DB password (default: `postgres`) |
| `POSTGRES_DB` | DB name (default: `ai_news_aggregator`) |
| `POSTGRES_HOST` | DB host (default: `localhost`) |
| `POSTGRES_PORT` | DB port (default: `5432`) |
| `PROXY_USERNAME` / `PROXY_PASSWORD` | Optional Webshare proxy for YouTube transcripts |

> **Resend free tier note:** Without a verified domain, emails are sent from `onboarding@resend.dev` and can only be delivered to the email address attached to your Resend account. This means signup-verification and password-reset emails will only work for that single address — any other signup will create the user row but fail to send the verification email. Verify a domain at [resend.com/domains](https://resend.com/domains) and update the `from` address in `app/services/email_utils.py` to support real signups.

### 4. Start the web app

```bash
uvicorn app.web.app:app --reload
```

Open `http://localhost:8000`, sign up, and configure your channels and interests.

### 5. Run the pipeline

```bash
python -m app.daily_runner
```

Tables are created automatically on first run. No manual setup needed.

**Quick test run** (cap each stage to 2 items, last 24 h only):

```bash
python -m app.daily_runner --hours 24 --limit 2
```

---

## Personalisation

User profiles and YouTube channels are managed through the web app dashboard. No file editing needed.

The pipeline reads each active user's profile from the database and sends them a personalized digest. The fallback channel list in `app/config.py` is used only if no users have added channels yet.

---

## Running individual steps

```bash
python -m app.runner                        # scrape only
python app/services/process_anthropic.py   # fetch Anthropic full text
python app/services/process_youtube.py     # fetch YouTube transcripts
python app/services/process_digest.py      # generate digests
python app/services/process_email.py       # curate and send email
```

## Tests

```bash
uv run pytest tests/ -v
```

50 tests covering:
- **Auth routes**: signup (success / duplicate / email-send failure), login (verified / unverified / wrong password / unknown email), logout
- **Email verification**: valid token, invalid token, token cannot be reused
- **Password reset**: forgot-password (existing / unknown email / email failure), reset-password (valid / mismatched / expired / invalid token)
- **Dashboard**: render, invalid-JWT redirect, profile update, channel add / remove / dedupe / invalid input, auth-required guards
- **Repository layer**: user CRUD, verification + reset token lookups (with expiry filtering), channel dedupe, `mark_digests_sent` idempotency, channel-id filtering of unsent digests
- **Auth helpers**: bcrypt round-trip, JWT encode/decode, tampered-signature rejection

Tests run against the local PostgreSQL instance (Docker must be up). An autouse fixture cleans test rows before and after every test, and `send_email` is patched so the suite never hits Resend.

---

## Deployment (Railway)

Two Railway services share the same Postgres instance:
- **web** — FastAPI app served by uvicorn
- **cron** — daily pipeline runner at 7 AM UTC

1. Push to GitHub
2. Railway → New Project → Deploy from GitHub repo
3. Railway auto-detects `render.yaml` and provisions both services + Postgres
4. Add env vars in Railway dashboard: `GROQ_API_KEY`, `OPENAI_API_KEY`, `RESEND_API_KEY`, `MY_EMAIL`, `SECRET_KEY`
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
│   ├── models.py            # SQLAlchemy ORM models (includes User, UserYouTubeChannel)
│   ├── repository.py        # All DB reads and writes
│   ├── connection.py        # Engine + session (DATABASE_URL → POSTGRES_* fallback)
│   └── create_tables.py     # Idempotent table creation
├── scrapers/
│   ├── youtube.py           # YouTube RSS + transcript fetching
│   ├── openai.py            # OpenAI RSS scraper
│   └── anthropic.py         # Anthropic RSS scraper + full-text fetching
├── pipeline/
│   ├── state.py             # PipelineState TypedDict shared across all nodes
│   ├── workflow.py          # LangGraph StateGraph definition
│   └── nodes/
│       ├── scrape.py        # Node: RSS scraping
│       ├── process.py       # Node: full-text + transcript enrichment
│       ├── digest.py        # Node: LLM digest generation
│       ├── email.py         # Node: per-user curation and sending
│       └── finalize.py      # Node: success flag assembly
├── services/
│   ├── process_anthropic.py # Full-text enrichment for Anthropic articles
│   ├── process_youtube.py   # Transcript fetching for YouTube videos
│   ├── process_digest.py    # Digest generation orchestration
│   ├── process_email.py     # Per-user email curation and sending
│   └── email_utils.py       # Resend API wrapper + HTML rendering
├── web/
│   ├── app.py               # FastAPI factory
│   ├── auth.py              # bcrypt hashing, JWT create/verify, get_current_user
│   ├── dependencies.py      # DB session dependency
│   ├── routes/
│   │   ├── auth.py          # /signup, /login, /logout, /verify-email/{token}, /forgot-password, /reset-password/{token}
│   │   └── dashboard.py     # GET /dashboard, POST /settings, /channels/add, /channels/remove
│   └── templates/
│       ├── base.html                    # Tailwind CDN layout
│       ├── signup.html
│       ├── signup_confirmation.html     # "check your email" page shown after signup
│       ├── login.html
│       ├── dashboard.html
│       ├── verify_email_result.html     # success / failure of /verify-email/{token}
│       ├── forgot_password.html
│       ├── forgot_password_sent.html
│       ├── reset_password.html
│       └── reset_password_success.html
├── profiles/
│   └── user_profile.py      # Fallback profile (used when no DB users exist)
├── runner.py                # Scraping entry point
└── daily_runner.py          # Full pipeline orchestrator
tests/
└── test_web.py              # Phase 2 auth + dashboard tests (pytest)
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
| Web framework | FastAPI + Jinja2 (server-side rendered) |
| Auth | bcrypt passwords, JWT in HTTP-only cookie (`python-jose`) |
| Pipeline orchestration | LangGraph `StateGraph` |
| LLM | Groq (`llama-3.3-70b-versatile`) via OpenAI-compatible SDK |
| Scraping | `feedparser`, `requests`, `beautifulsoup4` |
| Transcripts | `youtube-transcript-api` |
| Email | [Resend](https://resend.com) API |
| Testing | pytest + httpx (TestClient) |
| Deployment | [Railway](https://railway.app) — web service + cron job |
