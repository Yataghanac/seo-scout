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


@login_router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, str]:
    expected = request.app.state.token
    if not expected or not secrets.compare_digest(body.token, expected):
        raise HTTPException(status_code=401, detail="that token is not right")
    response.set_cookie(COOKIE, expected, httponly=True, samesite="strict")
    return {"status": "ok"}
