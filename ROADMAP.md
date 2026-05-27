# Roadmap: Multi-User Web App

## Context
The pipeline currently works end-to-end for a single user (hardcoded profile, one recipient email). The goal is to turn it into a real product where multiple users can sign up, configure their YouTube channels and interests, and receive personalized daily digests.

---

## Design Decisions
- **Web framework**: FastAPI + Jinja2 templates (server-side rendered, no separate frontend build)
- **Auth**: Email + password with bcrypt, JWT stored in HTTP-only cookie (7-day TTL)
- **YouTube channel input**: Paste a channel URL or raw ID; channel name resolved once from RSS on add
- **Digests stay global**: Digest generation runs once per article (not per user). Per-user filtering happens at email time using `channel_id` on the Digest row.
- **Railway**: Two services — `web` (uvicorn) + `cron` (daily_runner), sharing the same Postgres

---

## Phase 1 — Database

### New models in `app/database/models.py`

**`users` table**
```
id              UUID PK default uuid4
email           VARCHAR(255) UNIQUE NOT NULL
name            VARCHAR(255) NOT NULL
password_hash   VARCHAR(255) NOT NULL
background      TEXT nullable
expertise_level VARCHAR(50) default "Intermediate"
interests       ARRAY(String) nullable
content_depth   VARCHAR(100) nullable
content_type    VARCHAR(100) nullable
is_active       BOOLEAN default True
created_at / updated_at TIMESTAMP
```

**`user_youtube_channels` table**
```
id           INTEGER PK autoincrement
user_id      UUID FK → users.id CASCADE DELETE
channel_id   VARCHAR(100) NOT NULL
channel_name VARCHAR(255) nullable
added_at     TIMESTAMP
UNIQUE(user_id, channel_id)
```

**Update `Digest` model** — add nullable `channel_id VARCHAR(100)` (populated for YouTube, NULL for openai/anthropic).

### New Repository methods (`app/database/repository.py`)
- `create_user / get_user_by_email / get_user_by_id / update_user_profile / get_all_active_users`
- `add_user_channel / remove_user_channel / get_user_channels / get_all_active_channel_ids`
- Update `create_digest()` — add optional `channel_id` param
- Update `get_recent_digests()` — include `channel_id` in returned dicts

---

## Phase 2 — FastAPI Web App

### New dependencies
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
jinja2>=3.1.0
python-multipart>=0.0.9
passlib[bcrypt]>=1.7.4
python-jose[cryptography]>=3.3.0
```

### File structure
```
app/web/
├── app.py              # FastAPI factory
├── auth.py             # bcrypt + JWT utilities, get_current_user Depends()
├── dependencies.py     # db session Depends()
├── routes/
│   ├── auth.py         # GET/POST /signup, /login, /logout
│   └── dashboard.py    # GET /dashboard, POST /settings, /channels/add, /channels/remove
└── templates/
    ├── base.html       # Tailwind CDN layout
    ├── signup.html
    ├── login.html
    └── dashboard.html
```

### Auth flow
- Signup → bcrypt hash → insert User → set JWT cookie → redirect `/dashboard`
- Login → verify password → set JWT cookie → redirect `/dashboard`
- Logout → delete cookie → redirect `/login`

### Dashboard
- Profile section: name, background, expertise level, interests (comma-separated textarea)
- YouTube channels: list with Remove buttons + text input to add by URL or channel ID
- Channel resolution: fetch `https://www.youtube.com/feeds/videos.xml?channel_id={id}` to resolve name

---

## Phase 3 — Pipeline Multi-User Adaptation

### `app/runner.py`
```python
channel_ids = repo.get_all_active_channel_ids()
# Falls back to config.DEFAULT_YOUTUBE_CHANNELS if empty
```

### `app/services/process_email.py` — add:
```python
def build_user_profile_dict(user: User) -> dict: ...
def get_user_digests(repo, user, hours) -> list[dict]: ...  # filters youtube by user's channels
def send_digest_email_for_user(user, hours=240, top_n=10) -> dict: ...
```

### `app/daily_runner.py` — step 5 becomes:
```python
for user in repo.get_all_active_users():
    send_digest_email_for_user(user, hours=hours, top_n=top_n)
```

### `app/config.py`
Rename `YOUTUBE_CHANNELS` → `DEFAULT_YOUTUBE_CHANNELS`.

---

## Phase 4 — Railway Deployment

### New `railway.toml`
```toml
[build]
builder = "nixpacks"

[[services]]
name = "web"
startCommand = "uvicorn app.web.app:app --host 0.0.0.0 --port $PORT"

[[services]]
name = "cron"
startCommand = "python -m app.daily_runner"
cronSchedule = "0 7 * * *"
```

### New env var
- `SECRET_KEY` — `python -c "import secrets; print(secrets.token_hex(32))"`

---

## Verification Checklist
- [ ] Sign up via web form → row in `users` table
- [ ] Add YouTube channel → row in `user_youtube_channels`, channel name resolved
- [ ] Run `python -m app.daily_runner` → personalized email per user
- [ ] Deploy to Railway → web service reachable, cron fires at 7 AM UTC

---

## Post-MVP Ideas
- Email verification on signup
- Password reset flow
- Unsubscribe link in emails
- Per-user `top_n` / `hours` preferences
- Popular AI channels quick-add list
- Payment/subscription gating
- LLM-as-a-judge for digest quality scoring
- Temporal for workflow orchestration






//
What was done — and why
1. LangGraph Workflow
What: The old daily_runner.py was a flat sequence of try/except blocks calling each service step. It's now a StateGraph defined in app/pipeline/workflow.py.

How it works: LangGraph passes a shared PipelineState TypedDict between nodes. Each node receives the full state, does its work, and returns a partial dict — LangGraph merges the returned keys back in. The graph is compiled once at module load (pipeline_graph = build_pipeline_graph()) and invoked with pipeline_graph.invoke(initial_state).

Why it's better: Each step is now an isolated, composable unit. If a node raises an exception, the error is captured in state["errors"] and the next node still runs — unlike before, where one exception would abort the whole pipeline.

2. LLM-as-a-Judge (JudgeAgent)
What: After DigestAgent generates a summary, a second LLM call (JudgeAgent) independently scores it from 0–1 on accuracy, completeness, clarity, length, and absence of hallucinations. Only summaries scoring ≥ 0.7 are stored.

How it works: The judge uses temperature=0.1 (near-deterministic) and evaluates the original article content alongside the generated summary. Articles that fail remain "without digest" in the DB and are retried on the next daily run.

Why it matters: Without this, a vague or hallucinated summary could end up in a user's inbox. The judge acts as a quality filter between generation and storage.

3. Email Idempotency (user_digest_sends)
What: A new user_digest_sends table with a composite primary key (user_id, digest_id). Before sending, get_unsent_digests_for_user() filters out any digests already in that table. After sending, mark_digests_sent() records exactly which digest IDs were included.

Key nuance: Only articles actually included in the email (up to top_n) are marked as sent — not everything that was curated. So if an article was ranked 11th today and fell below the cutoff, it stays available for tomorrow's email.

4. Phase 3 Multi-User
What: The send_email node loops over every active user from the DB. Per-user logic: YouTube digests are filtered to channels the user has subscribed to (via user_youtube_channels). The channel list for scraping also comes from the DB now, falling back to DEFAULT_YOUTUBE_CHANNELS only if no users have added channels.