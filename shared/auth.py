"""FastAPI dependency that reads trusted identity headers injected by KrakenD.

Per ADR-0010: KrakenD validates the JWT once at the edge, strips any
client-supplied identity headers, then injects X-User-Id / X-User-Email /
X-Auth-Scopes. Internal services trust these blindly because they are
reachable only on the private Docker network.

If a service is ever exposed publicly or calls the open internet, it MUST
NOT use this dependency — re-validate the JWT instead.
"""
from fastapi import Header, HTTPException, status
from pydantic import BaseModel, EmailStr


class CurrentUser(BaseModel):
    owner_id: str
    email: EmailStr | None = None
    scopes: set[str] = set()


def current_user(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    x_auth_scopes: str | None = Header(default=None, alias="X-Auth-Scopes"),
) -> CurrentUser:
    """Resolve the caller from KrakenD trusted headers.

    Raises 401 when X-User-Id is absent — meaning the request did not come
    through KrakenD, or KrakenD rejected the JWT. Either way, refuse.
    """
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-User-Id; requests must go through KrakenD",
        )
    scopes = set(x_auth_scopes.split()) if x_auth_scopes else set()
    return CurrentUser(owner_id=x_user_id, email=x_user_email, scopes=scopes)