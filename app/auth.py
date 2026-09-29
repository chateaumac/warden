"""Authentication for every path Warden serves: OIDC, HTTP Basic, or explicitly none.

Warden can force-stop apps and change channels on TVs over ADB, so it fails closed:
WARDEN_AUTH_MODE must be set, and only /healthz is reachable without credentials.

Every mode also requires an `X-Requested-With: warden` header on state-changing requests.
Browsers attach Basic credentials and session cookies to cross-site requests, and a
body-less POST is a "simple" request needing no CORS preflight, so without this check any
web page open on the LAN could drive the TVs through Warden.

Generate a Basic password hash with:  python -m app.auth hash-password
"""

import base64
import getpass
import hashlib
import hmac
import logging
import os
import secrets
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("warden.auth")

AUTH_MODES = ("oidc", "basic", "none")
PUBLIC_PATHS = frozenset({"/healthz"})
OIDC_PUBLIC_PATHS = frozenset({"/auth/login", "/auth/callback"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "warden"

SESSION_MAX_AGE_S = 12 * 3600
BASIC_CACHE_TTL_S = 300          # skip re-running scrypt for a recently verified header
FAILURE_WINDOW_S = 300
MAX_FAILURES_PER_WINDOW = 10     # per client IP, then 429 until the window slides

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


class AuthConfigError(RuntimeError):
    pass


# ------------------------------------------------------------------ passwords


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    b64 = base64.b64encode
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# ------------------------------------------------------------------ settings


def _secret(env, name: str) -> str:
    """Read NAME, or the file named by NAME_FILE (Docker/Vault-rendered secrets)."""
    path = env.get(f"{name}_FILE", "")
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return env.get(name, "")


@dataclass(frozen=True)
class AuthSettings:
    mode: str
    basic_user: str = ""
    basic_password_hash: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_allowed_groups: tuple[str, ...] = ()
    public_url: str = ""
    session_secret: str = ""
    metrics_token: str = ""

    @classmethod
    def load(cls, env=None) -> "AuthSettings":
        env = os.environ if env is None else env
        mode = env.get("WARDEN_AUTH_MODE", "").strip().lower()
        if mode not in AUTH_MODES:
            raise AuthConfigError(
                "WARDEN_AUTH_MODE must be one of oidc, basic or none "
                f"(got {mode or 'nothing'}). Warden controls TVs over ADB, so it will not "
                "start unprotected by default; set WARDEN_AUTH_MODE=none only on a trusted, "
                "isolated network."
            )
        groups = tuple(g.strip() for g in env.get("WARDEN_OIDC_ALLOWED_GROUPS", "").split(",") if g.strip())
        settings = cls(
            mode=mode,
            basic_user=env.get("WARDEN_BASIC_USER", ""),
            basic_password_hash=_secret(env, "WARDEN_BASIC_PASSWORD_HASH"),
            oidc_issuer=env.get("WARDEN_OIDC_ISSUER", "").rstrip("/"),
            oidc_client_id=env.get("WARDEN_OIDC_CLIENT_ID", ""),
            oidc_client_secret=_secret(env, "WARDEN_OIDC_CLIENT_SECRET"),
            oidc_allowed_groups=groups,
            public_url=env.get("WARDEN_PUBLIC_URL", "").rstrip("/"),
            session_secret=_secret(env, "WARDEN_SESSION_SECRET"),
            metrics_token=_secret(env, "WARDEN_METRICS_TOKEN"),
        )
        settings._validate()
        return settings

    def _validate(self) -> None:
        missing = []
        if self.mode == "basic":
            missing = [k for k, v in (("WARDEN_BASIC_USER", self.basic_user),
                                      ("WARDEN_BASIC_PASSWORD_HASH", self.basic_password_hash)) if not v]
            if self.basic_password_hash and not self.basic_password_hash.startswith("scrypt$"):
                raise AuthConfigError(
                    "WARDEN_BASIC_PASSWORD_HASH must be a hash from `python -m app.auth hash-password`, "
                    "not a plaintext password"
                )
        elif self.mode == "oidc":
            missing = [k for k, v in (("WARDEN_OIDC_ISSUER", self.oidc_issuer),
                                      ("WARDEN_OIDC_CLIENT_ID", self.oidc_client_id),
                                      ("WARDEN_OIDC_CLIENT_SECRET", self.oidc_client_secret),
                                      ("WARDEN_PUBLIC_URL", self.public_url),
                                      ("WARDEN_SESSION_SECRET", self.session_secret)) if not v]
            if self.session_secret and len(self.session_secret) < 32:
                raise AuthConfigError("WARDEN_SESSION_SECRET must be at least 32 characters")
        if missing:
            raise AuthConfigError(f"WARDEN_AUTH_MODE={self.mode} requires {', '.join(missing)}")


# ------------------------------------------------------------------ middleware


@dataclass
class _Throttle:
    failures: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def blocked(self, client: str) -> bool:
        q = self.failures[client]
        cutoff = time.monotonic() - FAILURE_WINDOW_S
        while q and q[0] < cutoff:
            q.popleft()
        return len(q) >= MAX_FAILURES_PER_WINDOW

    def fail(self, client: str) -> None:
        self.failures[client].append(time.monotonic())


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: AuthSettings):
        super().__init__(app)
        self.settings = settings
        self.throttle = _Throttle()
        self._basic_cache: dict[str, float] = {}

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS:
            return await call_next(request)

        if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return JSONResponse({"detail": f"missing {CSRF_HEADER}: {CSRF_VALUE} header"}, status_code=403)

        if path == "/metrics" and self._metrics_token_ok(request):
            return await call_next(request)

        mode = self.settings.mode
        if mode == "none":
            request.state.user = {"name": "anonymous"}
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        if self.throttle.blocked(client):
            return JSONResponse({"detail": "too many failed logins, try again later"}, status_code=429)

        if mode == "basic":
            user = self._basic_user(request)
            if not user:
                if request.headers.get("authorization"):
                    self.throttle.fail(client)
                return Response("Authentication required", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Warden", charset="UTF-8"'})
        else:
            if path in OIDC_PUBLIC_PATHS:
                return await call_next(request)
            user = request.session.get("user")
            if not user:
                login = f"/auth/login?next={quote(path)}"
                if request.method == "GET" and not path.startswith(("/api/", "/metrics")):
                    return RedirectResponse(login, status_code=303)
                return JSONResponse({"detail": "authentication required", "login_url": "/auth/login"},
                                    status_code=401)

        request.state.user = user
        return await call_next(request)

    def _metrics_token_ok(self, request: Request) -> bool:
        token = self.settings.metrics_token
        header = request.headers.get("authorization", "")
        return bool(token) and hmac.compare_digest(header.encode(), f"Bearer {token}".encode())

    def _basic_user(self, request: Request) -> dict | None:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return None
        key = hashlib.sha256(header.encode()).hexdigest()
        now = time.monotonic()
        if self._basic_cache.get(key, 0) > now:
            return {"name": self.settings.basic_user}
        try:
            user, _, password = base64.b64decode(header[6:], validate=True).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return None
        user_ok = hmac.compare_digest(user.encode(), self.settings.basic_user.encode())
        # Always run scrypt so a wrong username costs the same as a wrong password
        password_ok = verify_password(password, self.settings.basic_password_hash)
        if not (user_ok and password_ok):
            return None
        self._basic_cache = {k: v for k, v in self._basic_cache.items() if v > now}
        self._basic_cache[key] = now + BASIC_CACHE_TTL_S
        return {"name": user}


# ------------------------------------------------------------------ OIDC routes


def _safe_next(target: str | None) -> str:
    """Only allow local absolute paths as post-login redirects."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _oidc_router(settings: AuthSettings) -> APIRouter:
    from authlib.integrations.starlette_client import OAuth, OAuthError

    oauth = OAuth()
    oauth.register(
        "oidc",
        server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    router = APIRouter(prefix="/auth", tags=["auth"], include_in_schema=False)

    @router.get("/login")
    async def login(request: Request, next: str = "/"):
        request.session["next"] = _safe_next(next)
        return await oauth.oidc.authorize_redirect(request, f"{settings.public_url}/auth/callback")

    @router.get("/callback")
    async def callback(request: Request):
        try:
            token = await oauth.oidc.authorize_access_token(request)
        except OAuthError as exc:
            log.warning("OIDC login failed: %s", exc.error)
            return JSONResponse({"detail": f"login failed: {exc.error}"}, status_code=401)
        claims = token.get("userinfo") or {}
        if settings.oidc_allowed_groups:
            groups = set(claims.get("groups") or [])
            if not groups.intersection(settings.oidc_allowed_groups):
                log.warning("OIDC login denied for %s: not in %s",
                            claims.get("preferred_username"), settings.oidc_allowed_groups)
                return JSONResponse({"detail": "your account is not allowed to use Warden"}, status_code=403)
        request.session["user"] = {
            "sub": claims.get("sub"),
            "name": claims.get("preferred_username") or claims.get("name") or claims.get("email"),
            "email": claims.get("email"),
        }
        return RedirectResponse(_safe_next(request.session.pop("next", "/")), status_code=303)

    @router.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        metadata = await oauth.oidc.load_server_metadata()
        return {"ok": True, "logout_url": metadata.get("end_session_endpoint") or "/"}

    return router


def install(app: FastAPI, settings: AuthSettings) -> None:
    """Protect every route on `app` according to `settings`."""
    app.state.auth = settings

    @app.get("/auth/me", tags=["auth"])
    def me(request: Request) -> dict:
        return {"mode": settings.mode, "user": getattr(request.state, "user", None)}

    if settings.mode == "oidc":
        from starlette.middleware.sessions import SessionMiddleware

        app.include_router(_oidc_router(settings))
        app.add_middleware(AuthMiddleware, settings=settings)
        # Added last so it runs first: AuthMiddleware reads request.session
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_secret,
            session_cookie="warden_session",
            max_age=SESSION_MAX_AGE_S,
            same_site="lax",
            https_only=settings.public_url.startswith("https://"),
        )
    else:
        app.add_middleware(AuthMiddleware, settings=settings)
    log.info("Authentication mode: %s", settings.mode)
    if settings.mode == "none":
        log.warning("WARDEN_AUTH_MODE=none — anyone who can reach this port can control your TVs")


def _cli() -> None:
    if sys.argv[1:] != ["hash-password"]:
        sys.exit("usage: python -m app.auth hash-password")
    password = getpass.getpass("Password: ")
    if len(password) < 12:
        sys.exit("use at least 12 characters")
    if getpass.getpass("Repeat: ") != password:
        sys.exit("passwords do not match")
    print(hash_password(password))


if __name__ == "__main__":
    _cli()
