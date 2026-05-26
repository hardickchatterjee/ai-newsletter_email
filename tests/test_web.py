import pytest
from fastapi.testclient import TestClient

from app.database.connection import get_session
from app.database.models import User
from app.web.app import app
from app.web.auth import create_access_token, hash_password

TEST_EMAIL = "test_phase2@test.com"
TEST_PASSWORD = "password123"
TEST_NAME = "Phase 2 Tester"


# --- fixtures ---

@pytest.fixture()
def client():
    with TestClient(app, follow_redirects=False) as c:
        yield c
    _delete_test_user()


@pytest.fixture()
def existing_user():
    """Insert a user directly into the DB; clean up after."""
    _delete_test_user()
    db = get_session()
    user = User(email=TEST_EMAIL, name=TEST_NAME, password_hash=hash_password(TEST_PASSWORD))
    db.add(user)
    db.commit()
    user_id = str(user.id)
    db.close()
    yield user_id
    _delete_test_user()


@pytest.fixture()
def auth_client(existing_user):
    """Anonymous client pre-loaded with a valid session cookie."""
    token = create_access_token(existing_user)
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("access_token", token)
        yield c


def _delete_test_user():
    db = get_session()
    db.query(User).filter(User.email == TEST_EMAIL).delete()
    db.commit()
    db.close()


# --- tests ---

def test_unauthenticated_dashboard_redirects_to_login(client):
    r = client.get("/dashboard")
    assert r.status_code == 307
    assert "/login" in r.headers["location"]


def test_login_page_loads(client):
    assert client.get("/login").status_code == 200


def test_signup_page_loads(client):
    assert client.get("/signup").status_code == 200


def test_signup_creates_user_and_sets_cookie(client):
    r = client.post("/signup", data={"name": TEST_NAME, "email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    assert "access_token" in r.cookies


def test_signup_duplicate_email_returns_error(existing_user, client):
    r = client.post("/signup", data={"name": TEST_NAME, "email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 400
    assert "already exists" in r.text


def test_login_correct_credentials_sets_cookie(existing_user, client):
    r = client.post("/login", data={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 303
    assert "access_token" in r.cookies


def test_login_wrong_password_returns_error(existing_user, client):
    r = client.post("/login", data={"email": TEST_EMAIL, "password": "wrongpassword"})
    assert r.status_code == 401
    assert "Invalid" in r.text


def test_authenticated_dashboard_renders(auth_client):
    r = auth_client.get("/dashboard")
    assert r.status_code == 200
    assert TEST_NAME in r.text


def test_logout_clears_cookie_and_redirects(auth_client):
    r = auth_client.get("/logout")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    # server instructs browser to delete the cookie via Max-Age=0
    assert "access_token" in r.headers.get("set-cookie", "")
