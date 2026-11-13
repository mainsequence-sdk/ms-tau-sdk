"""The relay component of ADR 0030, section 8, against the published relay contract."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from structlog.testing import capture_logs

from ms_tau_sdk.model_relay import RelayRefused, RelayUpstream, create_model_relay_router
from ms_tau_sdk.model_relay.contract import (
    CONTRACT_ACCOUNT_ID,
    CONTRACT_PASS,
    CONTRACT_SECRET,
    RELAY_CONTRACT_CASES,
    RelayContractCase,
    fill,
)

RELAY_ORIGIN = "http://relay.test"
RELAY_PREFIX = "/model-relay"


class _Provider:
    """A fake provider that answers every call the same way and records it."""

    def __init__(self, answer: httpx.Response | Exception) -> None:
        self.answer = answer
        self.calls: list[httpx.Request] = []
        self.bodies: list[bytes] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        self.bodies.append(request.read())
        if isinstance(self.answer, Exception):
            raise self.answer
        # A fresh, unread stream per call, as a provider sends it.
        return httpx.Response(
            self.answer.status_code,
            headers=self.answer.headers,
            stream=httpx.ByteStream(self.answer.content),
        )


@asynccontextmanager
async def _relay(
    provider: _Provider,
    *,
    upstream: RelayUpstream,
) -> AsyncIterator[httpx.AsyncClient]:
    async def resolve_pass(value: str) -> str:
        if value != CONTRACT_PASS:
            raise RelayRefused("The session pass is not valid for a running session.")
        return "session-1"

    async def resolve_upstream(session: str) -> RelayUpstream:
        assert session == "session-1"
        return upstream

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider.handle), follow_redirects=False
    ) as provider_http:
        app = FastAPI()
        app.include_router(
            create_model_relay_router(
                resolve_pass=resolve_pass,
                resolve_upstream=resolve_upstream,
                http=provider_http,
            ),
            prefix=RELAY_PREFIX,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=RELAY_ORIGIN
        ) as runtime:
            yield runtime


def _upstream(case: RelayContractCase) -> RelayUpstream:
    return RelayUpstream(
        api=case.api,
        base_url=case.provider_base_url,
        secret=SecretStr(CONTRACT_SECRET),
        account_id=CONTRACT_ACCOUNT_ID if case.api == "openai-codex-responses" else None,
    )


async def _run(case: RelayContractCase) -> tuple[httpx.Response, _Provider]:
    provider = _Provider(
        httpx.Response(
            case.provider_answer.status,
            headers=dict(case.provider_answer.headers),
            content=case.provider_answer.body,
        )
    )
    async with _relay(provider, upstream=_upstream(case)) as runtime:
        answer = await runtime.request(
            case.request.method,
            RELAY_PREFIX + fill(case.request.path),
            headers={name: fill(value) for name, value in case.request.headers.items()},
            content=case.request.body,
        )
        await answer.aread()
    return answer, provider


@pytest.mark.asyncio
@pytest.mark.parametrize("case", RELAY_CONTRACT_CASES, ids=lambda case: case.name)
async def test_the_relay_component_meets_the_relay_contract(case: RelayContractCase):
    answer, provider = await _run(case)

    assert answer.status_code == case.expect.status
    if case.expect.body is not None:
        assert answer.content == case.expect.body
    for name, value in case.expect.headers.items():
        assert answer.headers[name] == value
    if case.expect.refusal:
        assert set(answer.json()) == {"detail"}
        assert CONTRACT_PASS not in answer.text
    expected = case.expect.provider_call
    if expected is None:
        assert provider.calls == []
        return
    # Exactly one call: a redirect is never followed.
    [call] = provider.calls
    assert call.method == expected.method
    assert str(call.url) == fill(expected.url)
    for name, value in expected.headers.items():
        assert call.headers.get(name) == fill(value), name
    for name in expected.absent_headers:
        assert name not in call.headers, name
    # The pass never reaches a provider, and the body is forwarded as it arrived.
    assert CONTRACT_PASS not in str(call.url)
    assert CONTRACT_PASS not in str(call.headers)
    assert provider.bodies == [case.request.body]


def test_the_contract_covers_every_transport_and_every_rule():
    apis = {case.api for case in RELAY_CONTRACT_CASES}
    assert apis == {
        "anthropic-messages",
        "google-generative-ai",
        "mistral-conversations",
        "openai-codex-responses",
        "openai-completions",
        "openai-responses",
    }
    for api in apis:
        names = {
            case.name.removeprefix(f"{api}: ") for case in RELAY_CONTRACT_CASES if case.api == api
        }
        assert {
            "a streamed answer is forwarded unchanged",
            "a tool call is forwarded unchanged",
            "an error answer is forwarded unchanged",
            "a redirect is returned and not followed",
            "an unknown pass is refused",
            "a call without a pass is refused",
            "another protocol's path is refused",
            "a path outside the protocol's is refused",
            "an address in the path is refused",
        } <= names


@pytest.mark.asyncio
async def test_the_address_comes_only_from_the_application():
    case = RELAY_CONTRACT_CASES[0]
    provider = _Provider(httpx.Response(200, content=b"ok"))
    async with _relay(provider, upstream=_upstream(case)) as runtime:
        await runtime.post(
            f"{RELAY_PREFIX}/chat/completions?base_url=https://elsewhere.test",
            headers={
                "authorization": f"Bearer {CONTRACT_PASS}",
                "host": "elsewhere.test",
                "x-forwarded-host": "elsewhere.test",
            },
            content=b"{}",
        )

    [call] = provider.calls
    assert call.url.host == "completions.provider.test"
    assert call.headers["host"] == "completions.provider.test"


@pytest.mark.asyncio
async def test_an_application_refusal_reaches_no_provider():
    case = RELAY_CONTRACT_CASES[0]
    provider = _Provider(httpx.Response(200))

    async def refuse(session: str) -> RelayUpstream:
        raise RelayRefused("The session's credential cannot be used.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)) as http:
        app = FastAPI()

        async def resolve_pass(value: str) -> str:
            return value

        app.include_router(
            create_model_relay_router(
                resolve_pass=resolve_pass, resolve_upstream=refuse, http=http
            ),
            prefix=RELAY_PREFIX,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=RELAY_ORIGIN
        ) as runtime:
            answer = await runtime.post(
                f"{RELAY_PREFIX}{case.request.path}",
                headers={"authorization": f"Bearer {CONTRACT_PASS}"},
            )

    assert answer.status_code == 403
    assert answer.json() == {"detail": "The session's credential cannot be used."}
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    ["ftp://provider.test", "https://user:pw@provider.test", "https://provider.test/?key=1", "/v1"],
)
async def test_an_address_the_relay_does_not_call_is_refused(base_url):
    case = RELAY_CONTRACT_CASES[0]
    provider = _Provider(httpx.Response(200))
    upstream = RelayUpstream(api=case.api, base_url=base_url, secret=SecretStr(CONTRACT_SECRET))
    async with _relay(provider, upstream=upstream) as runtime:
        answer = await runtime.post(
            f"{RELAY_PREFIX}/chat/completions",
            headers={"authorization": f"Bearer {CONTRACT_PASS}"},
        )

    assert answer.status_code == 403
    assert provider.calls == []


@pytest.mark.asyncio
async def test_a_protocol_the_relay_does_not_serve_is_refused():
    provider = _Provider(httpx.Response(200))
    upstream = RelayUpstream(
        api="bedrock-converse", base_url="https://provider.test", secret=SecretStr("s")
    )
    async with _relay(provider, upstream=upstream) as runtime:
        answer = await runtime.post(
            f"{RELAY_PREFIX}/chat/completions",
            headers={"authorization": f"Bearer {CONTRACT_PASS}"},
        )

    assert answer.status_code == 403
    assert provider.calls == []


@pytest.mark.asyncio
async def test_an_unreachable_provider_is_502_without_its_address():
    case = next(case for case in RELAY_CONTRACT_CASES if case.api == "google-generative-ai")
    provider = _Provider(httpx.ConnectError(f"cannot reach {case.provider_base_url}"))
    async with _relay(provider, upstream=_upstream(case)) as runtime:
        answer = await runtime.post(
            RELAY_PREFIX + fill(case.request.path),
            headers={name: fill(value) for name, value in case.request.headers.items()},
            content=case.request.body,
        )

    assert answer.status_code == 502
    assert answer.json() == {"detail": "The provider could not be reached."}
    assert "provider.test" not in answer.text


@pytest.mark.asyncio
async def test_a_large_body_is_forwarded_whole():
    # A call with images is larger than any framework's default cap; the relay sets none.
    case = RELAY_CONTRACT_CASES[0]
    provider = _Provider(httpx.Response(200, content=b"ok"))
    body = json.dumps({"image": "A" * (8 * 1024 * 1024)}).encode()
    async with _relay(provider, upstream=_upstream(case)) as runtime:
        answer = await runtime.post(
            f"{RELAY_PREFIX}/chat/completions",
            headers={"authorization": f"Bearer {CONTRACT_PASS}"},
            content=body,
        )

    assert answer.status_code == 200
    assert provider.bodies == [body]


@pytest.mark.asyncio
async def test_without_a_secret_the_pass_is_only_removed():
    case = RELAY_CONTRACT_CASES[0]
    provider = _Provider(httpx.Response(200))
    upstream = RelayUpstream(
        api=case.api,
        base_url=case.provider_base_url,
        headers={"X-Org-Auth": "org-header"},
    )
    async with _relay(provider, upstream=upstream) as runtime:
        await runtime.post(
            f"{RELAY_PREFIX}/chat/completions",
            headers={"authorization": f"Bearer {CONTRACT_PASS}"},
        )

    [call] = provider.calls
    assert "authorization" not in call.headers
    assert call.headers["x-org-auth"] == "org-header"


@pytest.mark.asyncio
async def test_no_pass_secret_or_url_reaches_a_log(caplog):
    caplog.set_level(logging.DEBUG)
    with capture_logs() as logs:
        for case in RELAY_CONTRACT_CASES:
            await _run(case)
        provider = _Provider(httpx.ConnectError("unreachable"))
        case = RELAY_CONTRACT_CASES[0]
        async with _relay(provider, upstream=_upstream(case)) as runtime:
            await runtime.post(
                f"{RELAY_PREFIX}/chat/completions",
                headers={"authorization": f"Bearer {CONTRACT_PASS}"},
            )

    # The test's own client stands for the runtime; only the relay's side is checked here.
    written = str(logs) + "\n".join(
        f"{record.name} {record.getMessage()} {record.args}"
        for record in caplog.records
        if "relay.test" not in record.getMessage()
    )
    assert logs
    for value in (CONTRACT_PASS, CONTRACT_SECRET, "provider.test", "/chat/completions", "key="):
        assert value not in written, value


def test_an_upstream_repr_holds_no_secret_or_header():
    upstream = RelayUpstream(
        api="openai-completions",
        base_url="https://provider.test/v1",
        secret=SecretStr(CONTRACT_SECRET),
        headers={"X-Org-Auth": "org-header"},
    )

    assert CONTRACT_SECRET not in repr(upstream)
    assert "org-header" not in repr(upstream)
