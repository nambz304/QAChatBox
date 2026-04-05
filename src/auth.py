"""
JWT authentication utilities.

  create_token(username, role) → signed JWT string
  decode_token(token)          → {"sub": username, "role": role}
  require_auth                 → FastAPI dependency, returns decoded payload
  require_admin                → FastAPI dependency, additionally asserts admin role
"""
import time

import jwt
from fastapi import Depends, Header, HTTPException

from .config import get_settings

settings = get_settings()

_ALGORITHM = "HS256"
_EXPIRE_SECONDS = 8 * 3600  # 8 hours


def create_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": int(time.time()) + _EXPIRE_SECONDS,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """Returns {"sub": username, "role": role} or raises HTTPException."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_auth(authorization: str = Header(...)) -> dict:
    """FastAPI dependency — validates Bearer JWT and returns decoded payload."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    return decode_token(authorization[7:])


def require_admin(token: dict = Depends(require_auth)) -> dict:
    """FastAPI dependency — additionally asserts admin role."""
    if token["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return token
