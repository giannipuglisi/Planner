"""
Minimal FastAPI backend for Planner PWA.

The frontend is a static single-page app that stores everything in the user's
localStorage and syncs directly with Google Calendar. This backend exists to
satisfy the deployment platform's requirements (health check + /api prefix)
and to broker the Google OAuth2 authorization-code exchange, which requires a
client secret that must never reach the browser. No user data is persisted
here: the resulting refresh_token is handed back to the frontend, which is
the only place it is stored (localStorage), keeping this a single-user app
with no database.
"""
import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

APP_VERSION = "3.3.0"

app = FastAPI(title="Planner API", version=APP_VERSION)

# CORS: allow the frontend origin (and any other configured origin)
_cors_origins_env = os.environ.get("CORS_ORIGINS", "*")
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


@app.get("/api/", response_model=HealthResponse)
async def root():
    return HealthResponse(status="ok", service="planner-api", version=APP_VERSION)


@app.get("/api/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", service="planner-api", version=APP_VERSION)


# ==================== GOOGLE OAUTH TOKEN BROKER ====================
# Exchanges/refreshes tokens with Google's token endpoint using the client
# secret, which lives only in this process's environment. The frontend uses
# the Google Identity Services "code client" (authorization-code flow) to
# obtain a one-time code, then calls /api/auth/google/exchange to redeem it
# for an access_token + refresh_token. The refresh_token lets the frontend
# renew access_tokens for months without depending on the browser's Google
# session/cookies (unlike the old implicit-flow silent refresh).

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    "545488393108-ibhn8aahc7v8gngsoitmccn0l5dorp7d.apps.googleusercontent.com",
)
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")


class ExchangeRequest(BaseModel):
    code: str


class ConfigResponse(BaseModel):
    """Diagnostics: lets the frontend verify it talks to a server that can
    actually complete the OAuth exchange, and that both sides use the same
    OAuth client. A client_id mismatch between browser and server is the
    classic cause of an endless 'invalid_grant' loop."""
    client_id: str
    client_secret_configured: bool
    version: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600
    scope: str | None = None
    refresh_token: str | None = None


def _require_client_secret():
    if not GOOGLE_CLIENT_SECRET:
        logger.error("GOOGLE_CLIENT_SECRET is not configured")
        raise HTTPException(
            status_code=500,
            detail="Server non configurato per l'autenticazione Google (manca GOOGLE_CLIENT_SECRET)",
        )


async def _post_to_google_token_endpoint(data: dict) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=data)
        except httpx.HTTPError as e:
            logger.error("Google token endpoint request failed: %s", e)
            raise HTTPException(status_code=502, detail="Impossibile contattare Google") from e

    try:
        payload = resp.json()
    except ValueError:
        logger.error("Google token endpoint returned non-JSON (HTTP %s)", resp.status_code)
        raise HTTPException(status_code=502, detail="Risposta non valida da Google")

    if resp.status_code != 200:
        error = payload.get("error", "unknown_error")
        description = payload.get("error_description")
        logger.warning("Google token endpoint error: %s (%s)", error, description)
        # invalid_grant => refresh_token revoked/expired, frontend must re-login
        status_code = 401 if error == "invalid_grant" else 400
        # The description names the actual misconfiguration
        # (redirect_uri_mismatch, unauthorized_client, ...) instead of leaving
        # the frontend with a bare error code nobody can act on.
        detail = f"{error}: {description}" if description else error
        raise HTTPException(status_code=status_code, detail=detail)
    return payload


@app.get("/api/auth/google/config", response_model=ConfigResponse)
async def google_config():
    return ConfigResponse(
        client_id=GOOGLE_CLIENT_ID,
        client_secret_configured=bool(GOOGLE_CLIENT_SECRET),
        version=APP_VERSION,
    )


@app.post("/api/auth/google/exchange", response_model=TokenResponse)
async def exchange_google_code(body: ExchangeRequest):
    """Redeem a one-time authorization code (from initCodeClient) for tokens."""
    _require_client_secret()
    payload = await _post_to_google_token_endpoint({
        "code": body.code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": "postmessage",
        "grant_type": "authorization_code",
    })
    return TokenResponse(**payload)


@app.post("/api/auth/google/refresh", response_model=TokenResponse)
async def refresh_google_token(body: RefreshRequest):
    """Mint a new access_token from a previously stored refresh_token."""
    _require_client_secret()
    payload = await _post_to_google_token_endpoint({
        "refresh_token": body.refresh_token,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token",
    })
    # Google does not re-issue a refresh_token on refresh grants; the caller
    # keeps using the one it already has.
    return TokenResponse(**payload)
