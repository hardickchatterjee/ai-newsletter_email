from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.database.connection import get_session
from app.database.models import (
    Digest,
    User,
    UserDigestSend,
    UserYouTubeChannel,
)
from app.database.repository import Repository
from app.web.app import app
from app.web.auth import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    hash_password,
    verify_password,
)

TEST_EMAIL = "test_phase2@test.com"
ALT_TEST_EMAIL = "test_phase2_alt@test.com"
TEST_PASSWORD = "password123"
TEST_NEW_PASSWORD = "new_password456"
TEST_NAME = "Phase 2 Tester"
TEST_EMAILS = [TEST_EMAIL, ALT_TEST_EMAIL]

# Real-looking YouTube channel id (UC + 22 chars)
TEST_CHANNEL_ID = "UCBjURrPoezykLs9EqgamOBA"
TEST_CHANNEL_NAME = "Fireship"
TEST_DIGEST_ID = f"youtube:test_video_phase2"


# ---------------------------------------------------------------------------
# helpers / cleanup
# ---------------------------------------------------------------------------

def _clean_db():
    """Remove all test artifacts. Cascades clean channels + digest-sends via FK."""
    db = get_session()
    try:
        db.query(UserDigestSend).filter(
            UserDigestSend.digest_id.like("youtube:test_video_phase2%")
        ).delete(synchronize_session=False)
        db.query(Digest).filter(Digest.id.like("youtube:test_video_phase2%")).delete(
            synchronize_session=False
        )
        db.query(User).filter(User.email.in_(TEST_EMAILS)).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _cleanup_around_each_test():
    """Clean before AND after each test so a previous failure can't poison the next run."""
    _clean_db()
    yield
    _clean_db()


@pytest.fixture()
def mock_send_email():
    """Stub send_email everywhere it's imported so we never hit Resend."""
    with patch("app.web.routes.auth.send_email") as m:
        m.return_value = {"id": "mock-email-id"}
        yield m


@pytest.fixture()
def client():
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def verified_user():
    """A pre-existing, verified user — eligible to log in."""
    db = get_session()
    user = User(
        email=TEST_EMAIL,
        name=TEST_NAME,
        password_hash=hash_password(TEST_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    db.commit()
    user_id = str(user.id)
    db.close()
    return user_id


@pytest.fixture()
def unverified_user():
    """A user who signed up but hasn't clicked the verification link yet."""
    db = get_session()
    user = User(
        email=TEST_EMAIL,
        name=TEST_NAME,
        password_hash=hash_password(TEST_PASSWORD),
        email_verified=False,
        email_verification_token="verify-token-abc",
    )
    db.add(user)
    db.commit()
    user_id = str(user.id)
    db.close()
    return user_id


@pytest.fixture()
def user_with_reset_token():
    """A user with an active password-reset token (1h from now)."""
    db = get_session()
    user = User(
        email=TEST_EMAIL,
        name=TEST_NAME,
        password_hash=hash_password(TEST_PASSWORD),
        email_verified=True,
        password_reset_token="reset-token-xyz",
        password_reset_expires=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(user)
    db.commit()
    user_id = str(user.id)
    db.close()
    return user_id


@pytest.fixture()
def user_with_expired_reset_token():
    db = get_session()
    user = User(
        email=TEST_EMAIL,
        name=TEST_NAME,
        password_hash=hash_password(TEST_PASSWORD),
        email_verified=True,
        password_reset_token="expired-token",
        password_reset_expires=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(user)
    db.commit()
    user_id = str(user.id)
    db.close()
    return user_id


@pytest.fixture()
def auth_client(verified_user):
    """Client with a valid session cookie for a verified user."""
    token = create_access_token(verified_user)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("access_token", token)
        yield c


# ---------------------------------------------------------------------------
# Smoke / page-load tests
# ---------------------------------------------------------------------------

def test_unauthenticated_dashboard_redirects_to_login(client):
    r = client.get("/dashboard")
    assert r.status_code == 307
    assert "/login" in r.headers["location"]


def test_login_page_loads(client):
    assert client.get("/login").status_code == 200


def test_signup_page_loads(client):
    assert client.get("/signup").status_code == 200


def test_forgot_password_page_loads(client):
    assert client.get("/forgot-password").status_code == 200


# ---------------------------------------------------------------------------
# Signup flow
# ---------------------------------------------------------------------------

def test_signup_creates_user_and_sends_verification(client, mock_send_email):
    """Signup creates the user (unverified) and triggers a verification email — no cookie yet."""
    r = client.post(
        "/signup",
        data={"name": TEST_NAME, "email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert r.status_code == 200
    assert "verification" in r.text.lower() or "verify" in r.text.lower()
    assert "access_token" not in r.cookies

    mock_send_email.assert_called_once()
    call_kwargs = mock_send_email.call_args.kwargs
    assert call_kwargs["recipients"] == [TEST_EMAIL]
    assert "verify-email/" in call_kwargs["body_text"]

    db = get_session()
    created = db.query(User).filter_by(email=TEST_EMAIL).first()
    assert created is not None
    assert created.email_verified is False
    assert created.email_verification_token  # non-empty
    db.close()


def test_signup_duplicate_email_returns_error(verified_user, client, mock_send_email):
    r = client.post(
        "/signup",
        data={"name": TEST_NAME, "email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert r.status_code == 400
    assert "already exists" in r.text
    mock_send_email.assert_not_called()


def test_signup_email_failure_returns_500(client):
    """If Resend blows up after the user row is written, the user sees an error page."""
    with patch("app.web.routes.auth.send_email", side_effect=RuntimeError("boom")):
        r = client.post(
            "/signup",
            data={"name": TEST_NAME, "email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
    assert r.status_code == 500
    assert "verification email" in r.text.lower() or "failed" in r.text.lower()


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

def test_login_correct_credentials_sets_cookie(verified_user, client):
    r = client.post("/login", data={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    assert "access_token" in r.cookies


def test_login_wrong_password_returns_error(verified_user, client):
    r = client.post("/login", data={"email": TEST_EMAIL, "password": "wrongpassword"})
    assert r.status_code == 401
    assert "Invalid" in r.text
    assert "access_token" not in r.cookies


def test_login_unknown_email_returns_error(client):
    r = client.post("/login", data={"email": "nobody@example.com", "password": "x"})
    assert r.status_code == 401
    assert "Invalid" in r.text


def test_login_unverified_email_returns_403(unverified_user, client):
    r = client.post("/login", data={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 403
    assert "verify" in r.text.lower()
    assert "access_token" not in r.cookies


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

def test_verify_email_valid_token_marks_user_verified(unverified_user, client):
    r = client.get("/verify-email/verify-token-abc")
    assert r.status_code == 200
    assert "verified" in r.text.lower()

    db = get_session()
    user = db.query(User).filter_by(email=TEST_EMAIL).first()
    assert user.email_verified is True
    assert user.email_verification_token is None
    db.close()


def test_verify_email_invalid_token_returns_400(client):
    r = client.get("/verify-email/does-not-exist")
    assert r.status_code == 400
    assert "invalid" in r.text.lower() or "expired" in r.text.lower()


def test_verify_email_consumed_token_cant_be_reused(unverified_user, client):
    # first verify burns the token
    assert client.get("/verify-email/verify-token-abc").status_code == 200
    # second attempt with same token is now invalid
    r = client.get("/verify-email/verify-token-abc")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Forgot password
# ---------------------------------------------------------------------------

def test_forgot_password_existing_user_emails_reset_link(verified_user, client, mock_send_email):
    r = client.post("/forgot-password", data={"email": TEST_EMAIL})
    assert r.status_code == 200
    mock_send_email.assert_called_once()
    assert "reset-password/" in mock_send_email.call_args.kwargs["body_text"]

    db = get_session()
    user = db.query(User).filter_by(email=TEST_EMAIL).first()
    assert user.password_reset_token is not None
    # DB column is naive — coerce both sides to naive UTC for comparison
    expires = user.password_reset_expires
    if expires.tzinfo is not None:
        expires = expires.replace(tzinfo=None)
    assert expires > datetime.utcnow()
    db.close()


def test_forgot_password_unknown_email_does_not_send_email(client, mock_send_email):
    """Unknown email should still 200 (avoid enumeration) and never call send_email."""
    r = client.post("/forgot-password", data={"email": "nobody@example.com"})
    assert r.status_code == 200
    mock_send_email.assert_not_called()


def test_forgot_password_email_failure_does_not_500(verified_user, client):
    """If the email provider fails, we still show the 'sent' page (token is persisted)."""
    with patch("app.web.routes.auth.send_email", side_effect=RuntimeError("boom")):
        r = client.post("/forgot-password", data={"email": TEST_EMAIL})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------

def test_reset_password_form_valid_token_loads(user_with_reset_token, client):
    r = client.get("/reset-password/reset-token-xyz")
    assert r.status_code == 200


def test_reset_password_form_invalid_token_shows_error(client):
    r = client.get("/reset-password/garbage-token")
    assert r.status_code == 200
    assert "invalid" in r.text.lower() or "expired" in r.text.lower()


def test_reset_password_with_valid_token_changes_password(user_with_reset_token, client):
    r = client.post(
        "/reset-password/reset-token-xyz",
        data={"password": TEST_NEW_PASSWORD, "password_confirm": TEST_NEW_PASSWORD},
    )
    assert r.status_code == 200
    assert "success" in r.text.lower() or "reset" in r.text.lower()

    # token should be cleared and login should work with new password
    db = get_session()
    user = db.query(User).filter_by(email=TEST_EMAIL).first()
    assert user.password_reset_token is None
    assert user.password_reset_expires is None
    assert verify_password(TEST_NEW_PASSWORD, user.password_hash)
    db.close()


def test_reset_password_mismatched_passwords_returns_form_error(user_with_reset_token, client):
    r = client.post(
        "/reset-password/reset-token-xyz",
        data={"password": "a", "password_confirm": "b"},
    )
    assert r.status_code == 200
    assert "match" in r.text.lower()

    db = get_session()
    user = db.query(User).filter_by(email=TEST_EMAIL).first()
    # password unchanged
    assert verify_password(TEST_PASSWORD, user.password_hash)
    db.close()


def test_reset_password_invalid_token_does_not_update(client):
    r = client.post(
        "/reset-password/no-such-token",
        data={"password": TEST_NEW_PASSWORD, "password_confirm": TEST_NEW_PASSWORD},
    )
    assert r.status_code == 200
    assert "invalid" in r.text.lower() or "expired" in r.text.lower()


def test_reset_password_expired_token_is_rejected(user_with_expired_reset_token, client):
    r = client.post(
        "/reset-password/expired-token",
        data={"password": TEST_NEW_PASSWORD, "password_confirm": TEST_NEW_PASSWORD},
    )
    assert r.status_code == 200
    assert "invalid" in r.text.lower() or "expired" in r.text.lower()

    db = get_session()
    user = db.query(User).filter_by(email=TEST_EMAIL).first()
    assert verify_password(TEST_PASSWORD, user.password_hash)
    db.close()


# ---------------------------------------------------------------------------
# Dashboard render + profile settings
# ---------------------------------------------------------------------------

def test_authenticated_dashboard_renders(auth_client):
    r = auth_client.get("/dashboard")
    assert r.status_code == 200
    assert TEST_NAME in r.text or TEST_EMAIL in r.text


def test_dashboard_with_invalid_jwt_redirects_to_login(client):
    client.cookies.set("access_token", "not-a-real-jwt")
    r = client.get("/dashboard")
    assert r.status_code == 307
    assert "/login" in r.headers["location"]


def test_update_settings_saves_profile(auth_client, verified_user):
    r = auth_client.post(
        "/settings",
        data={
            "name": "Updated Name",
            "background": "ML engineer",
            "expertise_level": "Expert",
            "interests": "LLMs, Agents, Eval",
            "content_depth": "deep",
            "content_type": "research",
        },
    )
    assert r.status_code == 200
    assert "Profile saved" in r.text

    db = get_session()
    user = db.query(User).filter_by(id=verified_user).first()
    assert user.name == "Updated Name"
    assert user.background == "ML engineer"
    assert user.expertise_level == "Expert"
    assert user.interests == ["LLMs", "Agents", "Eval"]
    db.close()


def test_update_settings_with_empty_interests_clears_field(auth_client, verified_user):
    r = auth_client.post(
        "/settings",
        data={"name": TEST_NAME, "background": "", "interests": ""},
    )
    assert r.status_code == 200
    db = get_session()
    user = db.query(User).filter_by(id=verified_user).first()
    assert user.interests is None
    db.close()


# ---------------------------------------------------------------------------
# YouTube channels
# ---------------------------------------------------------------------------

def test_add_channel_valid_id_succeeds(auth_client, verified_user):
    with patch("app.web.routes.dashboard._resolve_channel_name", return_value=TEST_CHANNEL_NAME):
        r = auth_client.post("/channels/add", data={"channel_input": TEST_CHANNEL_ID})
    assert r.status_code == 200
    assert TEST_CHANNEL_NAME in r.text or "Added" in r.text

    db = get_session()
    channels = db.query(UserYouTubeChannel).filter_by(user_id=verified_user).all()
    assert len(channels) == 1
    assert channels[0].channel_id == TEST_CHANNEL_ID
    assert channels[0].channel_name == TEST_CHANNEL_NAME
    db.close()


def test_add_channel_extracts_id_from_url(auth_client, verified_user):
    url = f"https://www.youtube.com/channel/{TEST_CHANNEL_ID}"
    with patch("app.web.routes.dashboard._resolve_channel_name", return_value=None):
        r = auth_client.post("/channels/add", data={"channel_input": url})
    assert r.status_code == 200

    db = get_session()
    channels = db.query(UserYouTubeChannel).filter_by(user_id=verified_user).all()
    assert len(channels) == 1
    assert channels[0].channel_id == TEST_CHANNEL_ID
    db.close()


def test_add_channel_invalid_input_returns_400(auth_client):
    r = auth_client.post("/channels/add", data={"channel_input": "not a channel"})
    assert r.status_code == 400
    assert "valid channel" in r.text.lower() or "could not find" in r.text.lower()


def test_add_channel_duplicate_returns_400(auth_client, verified_user):
    repo = Repository(get_session())
    repo.add_user_channel(verified_user, TEST_CHANNEL_ID, TEST_CHANNEL_NAME)
    repo.session.close()

    with patch("app.web.routes.dashboard._resolve_channel_name", return_value=TEST_CHANNEL_NAME):
        r = auth_client.post("/channels/add", data={"channel_input": TEST_CHANNEL_ID})
    assert r.status_code == 400
    assert "already" in r.text.lower()


def test_remove_channel_deletes_record(auth_client, verified_user):
    repo = Repository(get_session())
    repo.add_user_channel(verified_user, TEST_CHANNEL_ID, TEST_CHANNEL_NAME)
    repo.session.close()

    r = auth_client.post("/channels/remove", data={"channel_id": TEST_CHANNEL_ID})
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"

    db = get_session()
    remaining = db.query(UserYouTubeChannel).filter_by(user_id=verified_user).all()
    assert remaining == []
    db.close()


def test_channel_routes_require_auth(client):
    """Anonymous users must be redirected when hitting channel routes."""
    r = client.post("/channels/add", data={"channel_input": TEST_CHANNEL_ID})
    assert r.status_code == 307
    r = client.post("/channels/remove", data={"channel_id": TEST_CHANNEL_ID})
    assert r.status_code == 307


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

def test_logout_clears_cookie_and_redirects(auth_client):
    r = auth_client.get("/logout")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert "access_token" in r.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# Repository unit tests
# ---------------------------------------------------------------------------

class TestRepository:
    def test_create_user_persists(self):
        db = get_session()
        repo = Repository(db)
        user = repo.create_user(
            email=TEST_EMAIL,
            name=TEST_NAME,
            password_hash=hash_password(TEST_PASSWORD),
        )
        assert user is not None
        assert user.email == TEST_EMAIL
        assert user.email_verified is False
        db.close()

    def test_create_user_returns_none_for_duplicate(self):
        db = get_session()
        repo = Repository(db)
        repo.create_user(email=TEST_EMAIL, name=TEST_NAME, password_hash="h")
        dup = repo.create_user(email=TEST_EMAIL, name=TEST_NAME, password_hash="h")
        assert dup is None
        db.close()

    def test_get_user_by_email_missing_returns_none(self):
        db = get_session()
        repo = Repository(db)
        assert repo.get_user_by_email("never@example.com") is None
        db.close()

    def test_get_user_by_verification_token(self):
        db = get_session()
        repo = Repository(db)
        repo.create_user(
            email=TEST_EMAIL,
            name=TEST_NAME,
            password_hash="h",
            email_verification_token="tok-1",
        )
        u = repo.get_user_by_verification_token("tok-1")
        assert u is not None
        assert u.email == TEST_EMAIL
        assert repo.get_user_by_verification_token("nope") is None
        db.close()

    def test_get_user_by_reset_token_ignores_expired(self):
        db = get_session()
        repo = Repository(db)
        user = repo.create_user(email=TEST_EMAIL, name=TEST_NAME, password_hash="h")
        repo.update_user(
            user.id,
            password_reset_token="tok-2",
            password_reset_expires=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        assert repo.get_user_by_reset_token("tok-2") is None
        db.close()

    def test_update_user_changes_fields(self):
        db = get_session()
        repo = Repository(db)
        user = repo.create_user(email=TEST_EMAIL, name=TEST_NAME, password_hash="h")
        ok = repo.update_user(user.id, name="Renamed", email_verified=True)
        assert ok is True
        refreshed = repo.get_user_by_id(user.id)
        assert refreshed.name == "Renamed"
        assert refreshed.email_verified is True
        db.close()

    def test_update_user_unknown_id_returns_false(self):
        import uuid
        db = get_session()
        repo = Repository(db)
        assert repo.update_user(uuid.uuid4(), name="x") is False
        db.close()

    def test_get_all_active_users_excludes_inactive(self):
        db = get_session()
        repo = Repository(db)
        active = repo.create_user(email=TEST_EMAIL, name="A", password_hash="h")
        inactive = repo.create_user(email=ALT_TEST_EMAIL, name="B", password_hash="h")
        repo.update_user(inactive.id, is_active=False)
        emails = {u.email for u in repo.get_all_active_users()}
        assert TEST_EMAIL in emails
        assert ALT_TEST_EMAIL not in emails
        db.close()

    def test_add_user_channel_dedupes(self):
        db = get_session()
        repo = Repository(db)
        user = repo.create_user(email=TEST_EMAIL, name=TEST_NAME, password_hash="h")
        first = repo.add_user_channel(user.id, TEST_CHANNEL_ID, "n")
        second = repo.add_user_channel(user.id, TEST_CHANNEL_ID, "n")
        assert first is not None
        assert second is None
        db.close()

    def test_remove_user_channel(self):
        db = get_session()
        repo = Repository(db)
        user = repo.create_user(email=TEST_EMAIL, name=TEST_NAME, password_hash="h")
        repo.add_user_channel(user.id, TEST_CHANNEL_ID, "n")
        assert repo.remove_user_channel(user.id, TEST_CHANNEL_ID) is True
        assert repo.remove_user_channel(user.id, TEST_CHANNEL_ID) is False
        db.close()

    def test_mark_digests_sent_is_idempotent(self):
        db = get_session()
        repo = Repository(db)
        user = repo.create_user(email=TEST_EMAIL, name=TEST_NAME, password_hash="h")
        digest = repo.create_digest(
            article_type="youtube",
            article_id="test_video_phase2",
            url="http://example.com",
            title="t",
            summary="s",
            published_at=datetime.now(timezone.utc),
        )
        assert digest is not None
        first = repo.mark_digests_sent(user.id, [digest.id])
        second = repo.mark_digests_sent(user.id, [digest.id])
        assert first == 1
        assert second == 0
        db.close()

    def test_unsent_digests_filters_already_sent(self):
        db = get_session()
        repo = Repository(db)
        user = repo.create_user(email=TEST_EMAIL, name=TEST_NAME, password_hash="h")
        digest = repo.create_digest(
            article_type="youtube",
            article_id="test_video_phase2",
            url="http://example.com",
            title="t",
            summary="s",
            published_at=datetime.now(timezone.utc),
        )
        unsent_before = repo.get_unsent_digests_for_user(user.id, hours=240)
        assert any(d["id"] == digest.id for d in unsent_before)

        repo.mark_digests_sent(user.id, [digest.id])
        unsent_after = repo.get_unsent_digests_for_user(user.id, hours=240)
        assert not any(d["id"] == digest.id for d in unsent_after)
        db.close()

    def test_unsent_digests_filters_by_channel_id(self):
        db = get_session()
        repo = Repository(db)
        user = repo.create_user(email=TEST_EMAIL, name=TEST_NAME, password_hash="h")
        repo.create_digest(
            article_type="youtube",
            article_id="test_video_phase2_match",
            url="http://example.com/a",
            title="match",
            summary="s",
            published_at=datetime.now(timezone.utc),
            channel_id=TEST_CHANNEL_ID,
        )
        repo.create_digest(
            article_type="youtube",
            article_id="test_video_phase2_skip",
            url="http://example.com/b",
            title="skip",
            summary="s",
            published_at=datetime.now(timezone.utc),
            channel_id="UC_OTHER_CHANNEL_XXXXXXXXX",
        )
        result = repo.get_unsent_digests_for_user(
            user.id, hours=240, channel_ids=[TEST_CHANNEL_ID]
        )
        titles = {d["title"] for d in result}
        assert "match" in titles
        assert "skip" not in titles
        db.close()


# ---------------------------------------------------------------------------
# Auth helper unit tests
# ---------------------------------------------------------------------------

class TestAuthHelpers:
    def test_hash_and_verify_password_roundtrip(self):
        h = hash_password("hunter2")
        assert h != "hunter2"
        assert verify_password("hunter2", h) is True
        assert verify_password("wrong", h) is False

    def test_create_access_token_encodes_user_id_with_expiry(self):
        token = create_access_token("user-123")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == "user-123"
        assert "exp" in payload

    def test_invalid_jwt_signature_is_rejected(self):
        token = create_access_token("user-456")
        # Tamper with the last char so the signature check fails
        bad = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(Exception):
            jwt.decode(bad, SECRET_KEY, algorithms=[ALGORITHM])
