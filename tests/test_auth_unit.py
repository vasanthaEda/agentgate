"""Pure unit tests for token/key helpers in app.auth (no DB/HTTP)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from jose import jwt

from app.auth import (
    API_KEY_PREFIX,
    create_access_token,
    decode_access_token,
    generate_api_key,
    hash_api_key,
)
from app.config import settings


def test_generated_api_key_has_expected_prefix_and_is_unique():
    key1 = generate_api_key()
    key2 = generate_api_key()
    assert key1.startswith(API_KEY_PREFIX)
    assert key1 != key2


def test_hash_api_key_is_deterministic_and_one_way():
    key = generate_api_key()
    assert hash_api_key(key) == hash_api_key(key)
    assert hash_api_key(key) != key


def test_create_and_decode_access_token_roundtrip():
    token = create_access_token("agent-123")
    assert decode_access_token(token) == "agent-123"


def test_decode_rejects_garbage_token():
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token("not.a.jwt")
    assert exc_info.value.status_code == 401


def test_decode_rejects_expired_token():
    expired_payload = {
        "sub": "agent-123",
        "type": "agent_access",
        "exp": datetime.now(UTC) - timedelta(minutes=1),
    }
    token = jwt.encode(expired_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_decode_rejects_wrong_token_type():
    payload = {
        "sub": "agent-123",
        "type": "something_else",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_decode_rejects_token_signed_with_wrong_secret():
    payload = {
        "sub": "agent-123",
        "type": "agent_access",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    token = jwt.encode(payload, "some-other-secret", algorithm=settings.jwt_algorithm)
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_decode_rejects_token_missing_subject():
    payload = {"type": "agent_access", "exp": datetime.now(UTC) + timedelta(minutes=5)}
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401
