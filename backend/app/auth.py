from fastapi import Header, HTTPException

from app.db import get_client


def get_current_user_id(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        response = get_client().auth.get_user(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired session.") from e

    if not response or not response.user:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")

    return response.user.id
