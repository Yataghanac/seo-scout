"""One shared token, compared in constant time.

Deliberately not user accounts: the README lists those under "Explicitly not built", and a
single secret the operator rotates by editing `.env` is the honest minimum for a dashboard
one client opens. An unset token means the deployment is open, so local use is unchanged.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

COOKIE = "seo_scout_token"
login_router = APIRouter(prefix="/api")


class LoginRequest(BaseModel):
    token: str


def token_guard(request: Request) -> None:
    """Refuse the request unless it carries the configured token. No token, no guard."""
    expected = request.app.state.token
    if not expected:
        return
    supplied = request.cookies.get(COOKIE) or ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="authentication required")


def _request_is_https(request: Request) -> bool:
    """True when this request reached us over TLS, directly or via a TLS-terminating proxy.

    The README tells operators hosting this for someone else to put it behind such a proxy,
    which terminates TLS itself and forwards plain HTTP internally — `request.url.scheme`
    alone would then read `http` even though the client used `https`, so the proxy's own
    `X-Forwarded-Proto` is trusted too. Local HTTP use (no proxy, no header) reads False,
    which is what keeps a token working there.
    """
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


@login_router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, str]:
    expected = request.app.state.token
    if not expected or not secrets.compare_digest(body.token, expected):
        raise HTTPException(status_code=401, detail="that token is not right")
    response.set_cookie(
        COOKIE, expected, httponly=True, samesite="strict", secure=_request_is_https(request)
    )
    return {"status": "ok"}
