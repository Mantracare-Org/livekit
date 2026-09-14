""" JWT login route for the UI server. """

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Request
from mantra.dependencies.auth import ADMIN_PASSWORD_HASH, ADMIN_USERNAME_HASH, JWT_ALGORITHM, JWT_EXPIRY_HOURS, JWT_SECRET
import hashlib
import jwt

import logging

logger = logging.getLogger("mantra.auth")
router = APIRouter()

@router.post("/v1/auth/login")
async def login(request: Request):
    """Authenticate with username/password, return JWT."""
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")

    username_hash = hashlib.sha256(username.encode()).hexdigest()
    password_hash = hashlib.sha256(password.encode()).hexdigest()

    if not ADMIN_USERNAME_HASH or not ADMIN_PASSWORD_HASH:
        raise HTTPException(status_code=500, detail="Auth not configured")

    if username_hash != ADMIN_USERNAME_HASH or password_hash != ADMIN_PASSWORD_HASH:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    expiry = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
    token = jwt.encode(
        {"sub": username, "exp": expiry, "iat": datetime.now(timezone.utc)},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    return {"token": token, "expires_in": JWT_EXPIRY_HOURS * 3600, "username": username}

