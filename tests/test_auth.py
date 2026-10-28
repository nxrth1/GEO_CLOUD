from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import geospatial.auth as auth


@pytest.fixture
def auth0_keys_and_settings(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    monkeypatch.setenv("GEO_CLOUD_ENV", "production")
    monkeypatch.setenv("AUTH0_DOMAIN", "example.us.auth0.com")
    monkeypatch.setenv("AUTH0_AUDIENCE", "https://geo-cloud.example/api")
    monkeypatch.setenv("AUTH0_ISSUER", "https://example.us.auth0.com/")
    monkeypatch.setattr(
        auth,
        "_jwks_client",
        lambda url: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=public_key)
        ),
    )
    return private_key


def access_token(private_key, **claim_overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "auth0|user-123",
        "iss": "https://example.us.auth0.com/",
        "aud": "https://geo-cloud.example/api",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, private_key, algorithm="RS256")


def credentials(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_current_owner_uses_verified_access_token_subject(
    auth0_keys_and_settings,
):
    token = access_token(auth0_keys_and_settings)

    owner_id = auth.get_current_owner(credentials(token))

    assert owner_id == "auth0|user-123"


def test_current_owner_rejects_missing_bearer_token(auth0_keys_and_settings):
    with pytest.raises(HTTPException) as error:
        auth.get_current_owner(None)

    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        {"aud": "different-api"},
        {"iss": "https://attacker.example/"},
    ],
)
def test_current_owner_rejects_expired_or_wrong_audience_or_issuer_tokens(
    auth0_keys_and_settings,
    claim_overrides,
):
    token = access_token(auth0_keys_and_settings, **claim_overrides)

    with pytest.raises(HTTPException) as error:
        auth.get_current_owner(credentials(token))

    assert error.value.status_code == 401


def test_current_owner_requires_auth0_configuration_outside_development(
    monkeypatch,
):
    monkeypatch.setenv("GEO_CLOUD_ENV", "production")
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.delenv("AUTH0_AUDIENCE", raising=False)

    with pytest.raises(HTTPException) as error:
        auth.get_current_owner(None)

    assert error.value.status_code == 503
