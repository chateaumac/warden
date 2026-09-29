"""Authentication, CSRF guard and ADB keystore hardening."""

import base64
import os
import stat
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.connectors import adb
from app.main import create_app

PASSWORD = "correct horse battery staple"
PASSWORD_HASH = auth.hash_password(PASSWORD)
CSRF = {"X-Requested-With": "warden"}


def basic(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def make_client(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("WARDEN_DATA_DIR", str(tmp_path / "data"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(create_app(), follow_redirects=False)


@pytest.fixture
def basic_client(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, WARDEN_AUTH_MODE="basic", WARDEN_BASIC_USER="admin",
                     WARDEN_BASIC_PASSWORD_HASH=PASSWORD_HASH,
                     WARDEN_METRICS_TOKEN="m" * 32) as c:
        yield c


@pytest.fixture
def oidc_client(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, WARDEN_AUTH_MODE="oidc",
                     WARDEN_OIDC_ISSUER="https://auth.example.com/application/o/warden/",
                     WARDEN_OIDC_CLIENT_ID="warden", WARDEN_OIDC_CLIENT_SECRET="s3cret",
                     WARDEN_PUBLIC_URL="https://warden.example.com",
                     WARDEN_SESSION_SECRET="x" * 48) as c:
        yield c


# ------------------------------------------------------------------ configuration


def test_refuses_to_start_without_auth_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("WARDEN_AUTH_MODE", raising=False)
    monkeypatch.setenv("WARDEN_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(auth.AuthConfigError, match="WARDEN_AUTH_MODE must be one of"):
        create_app()


@pytest.mark.parametrize("env, message", [
    ({"WARDEN_AUTH_MODE": "basic", "WARDEN_BASIC_USER": "admin"}, "WARDEN_BASIC_PASSWORD_HASH"),
    ({"WARDEN_AUTH_MODE": "basic", "WARDEN_BASIC_USER": "admin",
      "WARDEN_BASIC_PASSWORD_HASH": "hunter2"}, "not a plaintext password"),
    ({"WARDEN_AUTH_MODE": "oidc", "WARDEN_OIDC_ISSUER": "https://idp"}, "WARDEN_OIDC_CLIENT_ID"),
    ({"WARDEN_AUTH_MODE": "oidc", "WARDEN_OIDC_ISSUER": "https://idp", "WARDEN_OIDC_CLIENT_ID": "w",
      "WARDEN_OIDC_CLIENT_SECRET": "s", "WARDEN_PUBLIC_URL": "https://w",
      "WARDEN_SESSION_SECRET": "short"}, "at least 32"),
])
def test_incomplete_auth_config_is_rejected(env, message):
    with pytest.raises(auth.AuthConfigError, match=message):
        auth.AuthSettings.load(env)


def test_secrets_can_come_from_files(tmp_path):
    secret_file = tmp_path / "hash"
    secret_file.write_text(PASSWORD_HASH + "\n")
    settings = auth.AuthSettings.load({"WARDEN_AUTH_MODE": "basic", "WARDEN_BASIC_USER": "admin",
                                       "WARDEN_BASIC_PASSWORD_HASH_FILE": str(secret_file)})
    assert settings.basic_password_hash == PASSWORD_HASH


def test_password_hashing():
    assert auth.verify_password(PASSWORD, PASSWORD_HASH)
    assert not auth.verify_password("wrong", PASSWORD_HASH)
    assert not auth.verify_password(PASSWORD, "garbage")


# ------------------------------------------------------------------ basic mode


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/api/devices", "/api/guard/rules",
                                  "/docs", "/openapi.json", "/metrics", "/auth/me"])
def test_basic_protects_every_path(basic_client, path):
    res = basic_client.get(path)
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"].startswith("Basic")


def test_healthz_is_public_and_minimal(basic_client):
    res = basic_client.get("/healthz")
    assert res.status_code == 200
    assert set(res.json()) == {"status", "version"}


def test_basic_accepts_valid_credentials(basic_client):
    assert basic_client.get("/", headers=basic("admin", PASSWORD)).status_code == 200
    me = basic_client.get("/auth/me", headers=basic("admin", PASSWORD)).json()
    assert me == {"mode": "basic", "user": {"name": "admin"}}


@pytest.mark.parametrize("user, password", [("admin", "wrong"), ("root", PASSWORD)])
def test_basic_rejects_bad_credentials(basic_client, user, password):
    assert basic_client.get("/api/devices", headers=basic(user, password)).status_code == 401


def test_repeated_failures_are_throttled(basic_client):
    for _ in range(auth.MAX_FAILURES_PER_WINDOW):
        assert basic_client.get("/api/devices", headers=basic("admin", "nope")).status_code == 401
    assert basic_client.get("/api/devices", headers=basic("admin", PASSWORD)).status_code == 429


def test_metrics_bearer_token(basic_client):
    assert basic_client.get("/metrics", headers={"Authorization": "Bearer " + "m" * 32}).status_code == 200
    assert basic_client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401


# ------------------------------------------------------------------ CSRF guard


def test_state_changes_need_csrf_header_even_when_authenticated(basic_client):
    creds = basic("admin", PASSWORD)
    body = {"name": "x", "channels": ["FOX News"]}
    res = basic_client.post("/api/guard/rules", json=body, headers=creds)
    assert res.status_code == 403
    assert basic_client.post("/api/guard/rules", json=body, headers={**creds, **CSRF}).status_code == 201


def test_csrf_guard_applies_in_none_mode(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, WARDEN_AUTH_MODE="none") as c:
        # A body-less cross-site POST like this needs no CORS preflight
        assert c.post("/api/guard/devices/1/test-action?action=force_stop").status_code == 403
        assert c.get("/api/devices").status_code == 200


# ------------------------------------------------------------------ OIDC mode


def test_oidc_redirects_browsers_to_login(oidc_client):
    res = oidc_client.get("/")
    assert res.status_code == 303
    assert res.headers["location"] == "/auth/login?next=/"


def test_oidc_api_gets_401_with_login_url(oidc_client):
    res = oidc_client.get("/api/devices")
    assert res.status_code == 401
    assert res.json()["login_url"] == "/auth/login"


def test_oidc_protects_static_files(oidc_client):
    assert oidc_client.get("/static/app.js").status_code == 303


@pytest.mark.parametrize("target, expected", [
    ("/", "/"),
    ("/#guard", "/#guard"),
    ("//evil.example", "/"),
    ("https://evil.example", "/"),
    ("/\\evil.example", "/"),
    (None, "/"),
])
def test_post_login_redirect_stays_local(target, expected):
    assert auth._safe_next(target) == expected


# ------------------------------------------------------------------ ADB keystore


@pytest.fixture
def fresh_signer(monkeypatch):
    monkeypatch.setattr(adb, "_signer", None)


def key_settings(tmp_path, **overrides):
    return SimpleNamespace(keys_dir=tmp_path / "keys", adb_key_file=None, adb_keygen=True, **overrides)


def test_generated_key_is_private(tmp_path, fresh_signer):
    adb.get_signer(key_settings(tmp_path))
    assert stat.S_IMODE((tmp_path / "keys").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "keys" / "adb_key").stat().st_mode) == 0o600


def test_keygen_disabled_refuses_new_identity(tmp_path, fresh_signer):
    with pytest.raises(adb.KeystoreError, match="WARDEN_ADB_KEYGEN=false"):
        adb.get_signer(SimpleNamespace(keys_dir=tmp_path / "keys", adb_key_file=None, adb_keygen=False))
    assert not (tmp_path / "keys" / "adb_key").exists()


def test_missing_provisioned_key_file_fails(tmp_path, fresh_signer):
    s = SimpleNamespace(keys_dir=tmp_path / "keys", adb_key_file=tmp_path / "nope", adb_keygen=True)
    with pytest.raises(adb.KeystoreError, match="does not exist"):
        adb.get_signer(s)


def test_readable_key_is_tightened(tmp_path, fresh_signer):
    adb.get_signer(key_settings(tmp_path))
    key = tmp_path / "keys" / "adb_key"
    os.chmod(key, 0o644)
    adb._signer = None
    adb.get_signer(key_settings(tmp_path))
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_provisioned_key_file_is_used(tmp_path, fresh_signer):
    adb.get_signer(key_settings(tmp_path))  # create a keypair to "provision"
    adb._signer = None
    provisioned = tmp_path / "keys" / "adb_key"
    signer = adb.get_signer(SimpleNamespace(keys_dir=tmp_path / "unused", adb_key_file=provisioned,
                                            adb_keygen=False))
    assert signer is adb._signer
    assert not (tmp_path / "unused").exists()
