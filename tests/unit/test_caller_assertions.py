"""The verifier of the platform's caller and platform assertions (issue #59).

The platform side is played by the `platform_keys` fixture: it signs assertions with an Ed25519 key
and serves the key set through an HTTPX mock transport. Nothing leaves the process.
"""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ms_tau_sdk.backend.assertions import (
    PLATFORM_ASSERTION_TYPE,
    AssertionKeysUnavailableError,
    AssertionVerifier,
    InvalidAssertionError,
    VerifiedAssertion,
    VerifiedCaller,
)
from ms_tau_sdk.errors import ConfigurationError

TEAM_UIDS = sorted(
    [
        "8c7d6e5f-4a3b-4c2d-9e1f-0a9b8c7d6e5f",
        "1f2e3d4c-5b6a-4978-8a9b-0c1d2e3f4a5b",
    ]
)
OTHER_UID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
# The release UID the `platform_keys` fixture deploys the runtime with.
RELEASE_UID = "6f1c2b8e-4d3a-4e5f-9a8b-7c6d5e4f3a2b"
CALLER_TYPE = "mainsequence-caller-assertion+jwt"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _verifier(platform_keys, **overrides) -> AssertionVerifier:
    values = {
        "issuer": platform_keys.issuer,
        "jwks_url": platform_keys.jwks_url,
        "resource_release_uid": platform_keys.release_uid,
        "organization_environment_uid": platform_keys.environment_uid,
        "timeout": httpx.Timeout(5.0),
        "max_response_bytes": 1024 * 1024,
        "transport": platform_keys.transport,
    }
    values.update(overrides)
    return AssertionVerifier(**values)


async def test_a_valid_caller_assertion_names_the_caller(platform_keys):
    token = platform_keys.caller_assertion(team_uids=TEAM_UIDS, is_organization_admin=True)

    verified = await _verifier(platform_keys).verify(token, kind="caller")

    assert verified.kind == "caller"
    assert verified.caller == VerifiedCaller(
        uid=platform_keys.user_uid,
        team_uids=tuple(TEAM_UIDS),
        is_organization_admin=True,
    )
    assert verified.expires_at - verified.issued_at == 300
    assert platform_keys.fetches == 1


async def test_a_valid_platform_assertion_names_no_caller(platform_keys):
    verified = await _verifier(platform_keys).verify(
        platform_keys.platform_assertion(),
        kind="platform",
    )

    assert isinstance(verified, VerifiedAssertion)
    assert verified.kind == "platform"
    assert verified.caller is None


@pytest.mark.parametrize(
    ("assertion", "kind"),
    [
        ("caller", "platform"),
        ("platform", "caller"),
        ("plain_jwt", "caller"),
        ("plain_jwt", "platform"),
    ],
)
async def test_an_assertion_of_another_type_is_rejected(platform_keys, assertion, kind):
    tokens = {
        "caller": platform_keys.caller_assertion(),
        "platform": platform_keys.platform_assertion(),
        "plain_jwt": platform_keys.sign(platform_keys.caller_claims(), typ="JWT"),
    }

    with pytest.raises(InvalidAssertionError):
        await _verifier(platform_keys).verify(tokens[assertion], kind=kind)


@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("caller", {"add": {"username": "jose"}}),
        ("caller", {"remove": "team_uids"}),
        ("caller", {"remove": "is_organization_admin"}),
        ("caller", {"remove": "sub"}),
        ("caller", {"remove": "nbf"}),
        ("platform", {"add": {"sub": "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d"}}),
        ("platform", {"add": {"team_uids": []}}),
        ("platform", {"add": {"is_organization_admin": False}}),
        ("platform", {"remove": "organization_environment_uid"}),
    ],
)
async def test_an_extra_or_missing_claim_is_rejected(platform_keys, kind, change):
    claims = platform_keys.caller_claims() if kind == "caller" else platform_keys.platform_claims()
    claims.update(change.get("add", {}))
    claims.pop(change.get("remove", ""), None)
    typ = PLATFORM_ASSERTION_TYPE if kind == "platform" else CALLER_TYPE

    with pytest.raises(InvalidAssertionError):
        await _verifier(platform_keys).verify(platform_keys.sign(claims, typ=typ), kind=kind)


async def test_an_expired_assertion_is_rejected(platform_keys):
    token = platform_keys.caller_assertion(now=int(time.time()) - 301)

    with pytest.raises(InvalidAssertionError):
        await _verifier(platform_keys).verify(token, kind="caller")


async def test_an_assertion_not_yet_valid_is_rejected(platform_keys):
    issued_at = int(time.time()) + 120
    token = platform_keys.caller_assertion(now=issued_at)

    with pytest.raises(InvalidAssertionError):
        await _verifier(platform_keys).verify(token, kind="caller")


@pytest.mark.parametrize("kind", ["caller", "platform"])
async def test_a_lifetime_over_300_seconds_is_rejected(platform_keys, kind):
    now = int(time.time())
    if kind == "caller":
        token = platform_keys.caller_assertion(now=now, exp=now + 301)
    else:
        token = platform_keys.platform_assertion(now=now, exp=now + 301)

    with pytest.raises(InvalidAssertionError):
        await _verifier(platform_keys).verify(token, kind=kind)


async def test_a_lifetime_of_exactly_300_seconds_is_accepted(platform_keys):
    now = int(time.time())

    verified = await _verifier(platform_keys).verify(
        platform_keys.caller_assertion(now=now, exp=now + 300),
        kind="caller",
    )

    assert verified.expires_at == now + 300


@pytest.mark.parametrize("case", ["float_iat", "nbf_after_iat", "boolean_exp"])
async def test_times_must_be_integers_in_order(platform_keys, case):
    now = int(time.time())
    overrides = {
        "float_iat": {"iat": float(now)},
        "nbf_after_iat": {"nbf": now + 10},
        "boolean_exp": {"exp": True},
    }[case]

    with pytest.raises(InvalidAssertionError):
        await _verifier(platform_keys).verify(
            platform_keys.caller_assertion(now=now, **overrides),
            kind="caller",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": f"urn:mainsequence:fapi:{OTHER_UID}"},
        {"aud": [f"urn:mainsequence:fapi:{RELEASE_UID}"]},
        {"aud": "urn:mainsequence:fapi:"},
        {"resource_release_uid": OTHER_UID},
        {"organization_environment_uid": OTHER_UID},
        {"iss": "https://elsewhere.test"},
    ],
    ids=["aud", "aud-list", "aud-empty-release", "release", "environment", "issuer"],
)
async def test_an_assertion_for_another_target_is_rejected(platform_keys, overrides):
    for kind in ("caller", "platform"):
        if kind == "caller":
            token = platform_keys.caller_assertion(**overrides)
        else:
            token = platform_keys.platform_assertion(**overrides)
        with pytest.raises(InvalidAssertionError):
            await _verifier(platform_keys).verify(token, kind=kind)


@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": "2B7F1C48-3D1E-4A5B-9C6D-0E1F2A3B4C5D"},
        {"sub": "not-a-uid"},
        {"team_uids": list(reversed(TEAM_UIDS))},
        {"team_uids": [TEAM_UIDS[0], TEAM_UIDS[0]]},
        {"team_uids": ["team-1"]},
        {"team_uids": TEAM_UIDS[0]},
        {"is_organization_admin": "true"},
        {"is_organization_admin": 1},
    ],
)
async def test_an_invalid_caller_is_rejected(platform_keys, overrides):
    with pytest.raises(InvalidAssertionError):
        await _verifier(platform_keys).verify(
            platform_keys.caller_assertion(**overrides),
            kind="caller",
        )


async def test_a_signature_by_another_key_is_rejected(platform_keys):
    intruder = Ed25519PrivateKey.generate()
    token = platform_keys.sign(
        platform_keys.caller_claims(),
        key=intruder,
        kid=platform_keys.kid(platform_keys.signing_key),
    )

    with pytest.raises(InvalidAssertionError):
        await _verifier(platform_keys).verify(token, kind="caller")


async def test_a_token_with_another_algorithm_or_header_is_rejected(platform_keys):
    kid = platform_keys.kid(platform_keys.signing_key)
    symmetric = jwt.encode(
        platform_keys.caller_claims(),
        "shared-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
        headers={"kid": kid, "typ": CALLER_TYPE},
    )
    extra_header = jwt.encode(
        platform_keys.caller_claims(),
        platform_keys.signing_key,
        algorithm="EdDSA",
        headers={"kid": kid, "typ": CALLER_TYPE, "jku": "https://keys.test/"},
    )
    verifier = _verifier(platform_keys)

    for token in (symmetric, extra_header, "not-a-token"):
        with pytest.raises(InvalidAssertionError):
            await verifier.verify(token, kind="caller")


async def test_an_unknown_kid_refreshes_the_key_set_once(platform_keys):
    clock = _Clock()
    verifier = _verifier(platform_keys, clock=clock)
    await verifier.verify(platform_keys.caller_assertion(), kind="caller")
    assert platform_keys.fetches == 1

    # The platform replaced its key. The cached set is still fresh, but a token signed with the
    # new key names a kid the cache lacks, so the verifier fetches the set again, once.
    rotated = Ed25519PrivateKey.generate()
    platform_keys.published = [rotated]
    verified = await verifier.verify(
        platform_keys.sign(platform_keys.caller_claims(), key=rotated),
        kind="caller",
    )
    assert verified.caller is not None
    assert platform_keys.fetches == 2

    # A kid the platform does not publish costs one more fetch and is then rejected.
    unpublished = Ed25519PrivateKey.generate()
    with pytest.raises(InvalidAssertionError):
        await verifier.verify(
            platform_keys.sign(platform_keys.caller_claims(), key=unpublished),
            kind="caller",
        )
    assert platform_keys.fetches == 3

    # The replaced key left the set with that refresh.
    with pytest.raises(InvalidAssertionError):
        await verifier.verify(platform_keys.caller_assertion(), kind="caller")


async def test_the_key_set_is_cached_for_its_max_age(platform_keys):
    clock = _Clock()
    verifier = _verifier(platform_keys, clock=clock)

    await verifier.verify(platform_keys.caller_assertion(), kind="caller")
    clock.now += 59
    await verifier.verify(platform_keys.platform_assertion(), kind="platform")
    assert platform_keys.fetches == 1

    clock.now += 1
    await verifier.verify(platform_keys.caller_assertion(), kind="caller")
    assert platform_keys.fetches == 2


async def test_a_key_set_without_max_age_is_kept_until_an_unknown_kid(platform_keys):
    platform_keys.cache_control = None
    clock = _Clock()
    verifier = _verifier(platform_keys, clock=clock)

    await verifier.verify(platform_keys.caller_assertion(), kind="caller")
    clock.now += 24 * 3600
    await verifier.verify(platform_keys.caller_assertion(), kind="caller")

    assert platform_keys.fetches == 1


@pytest.mark.parametrize(
    "failure",
    ["connection", "status", "not_json", "no_usable_key", "kid_is_not_thumbprint", "too_large"],
)
async def test_an_unavailable_key_set_is_reported(platform_keys, failure):
    max_response_bytes = 1024 * 1024
    jwk = platform_keys.jwk(platform_keys.signing_key)
    documents = {
        "not_json": "not json",
        "no_usable_key": {"keys": [{"kty": "RSA", "kid": "rsa-1", "n": "AQAB", "e": "AQAB"}]},
        "kid_is_not_thumbprint": {"keys": [{**jwk, "kid": "chosen-by-hand"}]},
    }
    if failure == "connection":
        platform_keys.failure = httpx.ConnectError("platform unreachable")
    elif failure == "status":
        platform_keys.status_code = 503
    elif failure == "too_large":
        max_response_bytes = 16
    else:
        platform_keys.jwks = lambda: documents[failure]

    with pytest.raises(AssertionKeysUnavailableError):
        await _verifier(platform_keys, max_response_bytes=max_response_bytes).verify(
            platform_keys.caller_assertion(),
            kind="caller",
        )


async def test_a_failed_refresh_keeps_the_fresh_key_set(platform_keys):
    verifier = _verifier(platform_keys)
    await verifier.verify(platform_keys.caller_assertion(), kind="caller")
    platform_keys.failure = httpx.ConnectError("platform unreachable")

    with pytest.raises(AssertionKeysUnavailableError):
        await verifier.verify(
            platform_keys.sign(
                platform_keys.caller_claims(),
                key=Ed25519PrivateKey.generate(),
            ),
            kind="caller",
        )
    verified = await verifier.verify(platform_keys.caller_assertion(), kind="caller")

    assert verified.caller is not None
    assert platform_keys.fetches == 2


async def test_an_expired_key_set_is_never_used_when_the_refresh_fails(platform_keys):
    clock = _Clock()
    verifier = _verifier(platform_keys, clock=clock)
    await verifier.verify(platform_keys.caller_assertion(), kind="caller")
    clock.now += 61
    platform_keys.status_code = 503

    with pytest.raises(AssertionKeysUnavailableError):
        await verifier.verify(platform_keys.caller_assertion(), kind="caller")


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"issuer": " "}, "MAINSEQUENCE_CALLER_ASSERTION_ISSUER"),
        ({"jwks_url": "http://platform.test/keys/"}, "MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL"),
        (
            {"jwks_url": "https://user:pw@platform.test/keys/"},
            "MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL",
        ),
        ({"resource_release_uid": "my-app"}, "APP_NAME"),
        ({"organization_environment_uid": ""}, "MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID"),
    ],
)
def test_an_unusable_trust_configuration_is_a_configuration_error(
    platform_keys,
    overrides,
    named,
):
    with pytest.raises(ConfigurationError, match=named):
        _verifier(platform_keys, **overrides)
