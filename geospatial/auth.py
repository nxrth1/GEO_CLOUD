import os
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWKClientConnectionError, PyJWKClientError


LOCAL_DEVELOPMENT_OWNER_ID = "local-development"
bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=8)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url)


def get_current_owner(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    environment = os.getenv("GEO_CLOUD_ENV", "production").lower()
    if environment in {"development", "local"}:
        # Local requests share one owner; this mode is not multi-user authentication.
        return LOCAL_DEVELOPMENT_OWNER_ID

    domain = os.getenv("AUTH0_DOMAIN")
    audience = os.getenv("AUTH0_AUDIENCE")
    issuer = os.getenv("AUTH0_ISSUER")
    if not domain or not audience:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth0 must be configured before running outside local development.",
        )

    domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
    issuer = issuer or f"https://{domain}/"
    jwks_url = f"{issuer.rstrip('/')}/.well-known/jwks.json"

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer access token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(
            credentials.credentials
        )
    except PyJWKClientConnectionError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach the Auth0 signing-key service.",
        ) from error
    except PyJWKClientError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    try:
        claims = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    owner_id = claims.get("sub")
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 255:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has no usable subject.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return owner_id
