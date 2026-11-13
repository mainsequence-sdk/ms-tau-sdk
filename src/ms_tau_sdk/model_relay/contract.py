"""The model relay contract every relay runs, as data (ADR 0030, section 9).

The SDK's relay component and the platform's relay must behave the same towards a runtime. Each
``RelayContractCase`` is one call a runtime's transport makes to a relay, the answer a fake
provider gives, and what the relay must do. A relay's own test suite runs every case of
``RELAY_CONTRACT_CASES``:

1. Arrange that ``CONTRACT_PASS`` names a running session whose provider uses ``case.api``, whose
   address is ``case.provider_base_url``, whose secret is ``CONTRACT_SECRET`` and, for
   ``openai-codex-responses``, whose account id is ``CONTRACT_ACCOUNT_ID``. Any other pass names
   no session.
2. Answer every provider call with ``case.provider_answer``, and record the calls.
3. Send ``case.request`` to the relay, under its base.
4. Check the relay's answer and the recorded provider calls against ``case.expect``.

``"{pass}"``, ``"{secret}"`` and ``"{account_id}"`` in a case stand for the three values above.
Nothing in this module calls a relay; the SDK's own runner is
``tests/unit/test_model_relay_component.py``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

CONTRACT_PASS = "contract-session-pass"
CONTRACT_SECRET = "contract-provider-secret"
CONTRACT_ACCOUNT_ID = "contract-account-id"
CONTRACT_UNKNOWN_PASS = "contract-unknown-pass"


def fill(value: str) -> str:
    """A case's text with the contract's pass, secret and account id in place."""

    return (
        value.replace("{pass}", CONTRACT_PASS)
        .replace("{secret}", CONTRACT_SECRET)
        .replace("{account_id}", CONTRACT_ACCOUNT_ID)
    )


@dataclass(frozen=True, slots=True)
class RelayRequest:
    """What the runtime sends: ``path`` is below the relay's base, query included."""

    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True, slots=True)
class ProviderAnswer:
    status: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True, slots=True)
class ExpectedProviderCall:
    """The one call the provider must receive.

    ``url`` is exact, query included. ``headers`` must be present with these values, header
    names in lower case; ``absent_headers`` must not be present at all.
    """

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    absent_headers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RelayExpectation:
    """What the relay must do.

    ``status`` is the relay's answer. ``provider_call`` is the one provider call it makes, or
    ``None`` when it must make none. ``body`` is the exact answer body when set, and
    ``headers`` must be among the answer's headers. A refusal is ``403`` with a JSON ``detail``.
    """

    status: int
    provider_call: ExpectedProviderCall | None
    body: bytes | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    refusal: bool = False


@dataclass(frozen=True, slots=True)
class RelayContractCase:
    name: str
    api: str
    provider_base_url: str
    request: RelayRequest
    provider_answer: ProviderAnswer
    expect: RelayExpectation


@dataclass(frozen=True, slots=True)
class _Transport:
    """How one Tau wire protocol calls a relay base, and where its pass travels."""

    api: str
    provider_base_url: str
    method: str
    path: str
    # The URL the provider must receive, before the query.
    provider_url: str
    pass_place: str
    stream: bytes
    tool_call: bytes
    other_protocols_path: str


_SSE = {"content-type": "text/event-stream"}
_TRANSPORTS = (
    _Transport(
        api="openai-completions",
        provider_base_url="https://completions.provider.test/v1",
        method="POST",
        path="/chat/completions",
        provider_url="https://completions.provider.test/v1/chat/completions",
        pass_place="bearer",
        stream=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
        tool_call=(
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
            b'"function":{"name":"read","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n'
        ),
        other_protocols_path="/messages",
    ),
    _Transport(
        api="openai-responses",
        provider_base_url="https://responses.provider.test/v1",
        method="POST",
        path="/responses",
        provider_url="https://responses.provider.test/v1/responses",
        pass_place="bearer",
        stream=b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
        tool_call=(
            b'data: {"type":"response.output_item.done","item":{"type":"function_call",'
            b'"call_id":"call_1","name":"read","arguments":"{}"}}\n\n'
        ),
        other_protocols_path="/messages",
    ),
    _Transport(
        api="anthropic-messages",
        provider_base_url="https://anthropic.provider.test/v1",
        method="POST",
        path="/messages",
        provider_url="https://anthropic.provider.test/v1/messages",
        pass_place="x-api-key",
        stream=(
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
        ),
        tool_call=(
            b'data: {"type":"content_block_start","index":0,"content_block":'
            b'{"type":"tool_use","id":"toolu_1","name":"read","input":{}}}\n\n'
        ),
        other_protocols_path="/chat/completions",
    ),
    _Transport(
        api="openai-codex-responses",
        provider_base_url="https://codex.provider.test/backend-api",
        method="POST",
        path="/codex/responses",
        provider_url="https://codex.provider.test/backend-api/codex/responses",
        pass_place="bearer",
        stream=b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
        tool_call=(
            b'data: {"type":"response.output_item.done","item":{"type":"function_call",'
            b'"call_id":"call_1","name":"read","arguments":"{}"}}\n\n'
        ),
        other_protocols_path="/chat/completions",
    ),
    _Transport(
        api="google-generative-ai",
        provider_base_url="https://google.provider.test/v1beta",
        method="POST",
        path="/models/gemini-contract:streamGenerateContent",
        provider_url="https://google.provider.test/v1beta/models/gemini-contract:streamGenerateContent",
        pass_place="query",
        stream=b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}\n\n',
        tool_call=(
            b'data: {"candidates":[{"content":{"parts":[{"functionCall":'
            b'{"name":"read","args":{}}}]}}]}\n\n'
        ),
        other_protocols_path="/chat/completions",
    ),
    _Transport(
        api="mistral-conversations",
        # Mistral's transport adds /v1 itself; this provider address does not end in it.
        provider_base_url="https://mistral.provider.test",
        method="POST",
        path="/v1/chat/completions",
        provider_url="https://mistral.provider.test/v1/chat/completions",
        pass_place="bearer",
        stream=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
        tool_call=(
            b'data: {"choices":[{"delta":{"tool_calls":[{"id":"call_1",'
            b'"function":{"name":"read","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n'
        ),
        other_protocols_path="/messages",
    ),
)

_REQUEST_BODY = json.dumps({"model": "contract-model", "stream": True}).encode()


def _with_pass(transport: _Transport, value: str) -> tuple[dict[str, str], str]:
    """The headers and query string a transport sends a pass in."""

    if transport.pass_place == "x-api-key":
        return {"x-api-key": value}, ""
    if transport.pass_place == "query":
        return {}, f"?alt=sse&key={value}"
    return {"authorization": f"Bearer {value}"}, ""


def _secret_call(transport: _Transport) -> ExpectedProviderCall:
    absent = ["cookie", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip"]
    headers = {"content-type": "application/json", "x-runtime-header": "kept"}
    if transport.pass_place == "x-api-key":
        headers["x-api-key"] = "{secret}"
        absent.append("authorization")
        url = transport.provider_url
    elif transport.pass_place == "query":
        absent.extend(["authorization", "x-api-key"])
        url = f"{transport.provider_url}?alt=sse&key={{secret}}"
    else:
        headers["authorization"] = "Bearer {secret}"
        absent.append("x-api-key")
        url = transport.provider_url
    if transport.api == "openai-codex-responses":
        headers["chatgpt-account-id"] = "{account_id}"
    return ExpectedProviderCall(
        method=transport.method,
        url=url,
        headers=headers,
        absent_headers=tuple(absent),
    )


def _request(
    transport: _Transport, *, session_pass: str = "{pass}", path: str = ""
) -> RelayRequest:
    headers, query = _with_pass(transport, session_pass)
    return RelayRequest(
        method=transport.method,
        path=(path or transport.path) + query,
        headers={
            **headers,
            "content-type": "application/json",
            "x-runtime-header": "kept",
            # Nothing that names the runtime's side reaches a provider.
            "cookie": "runtime=1",
            "forwarded": "for=10.0.0.1",
            "x-forwarded-for": "10.0.0.1",
            "x-forwarded-host": "elsewhere.test",
            "x-real-ip": "10.0.0.1",
            # The runtime's own account id header is replaced by the stored one.
            **(
                {"chatgpt-account-id": "runtime-account-id"}
                if transport.api == "openai-codex-responses"
                else {}
            ),
        },
        body=_REQUEST_BODY,
    )


def _refused(name: str, transport: _Transport, request: RelayRequest) -> RelayContractCase:
    return RelayContractCase(
        name=name,
        api=transport.api,
        provider_base_url=transport.provider_base_url,
        request=request,
        provider_answer=ProviderAnswer(status=200, headers=_SSE, body=transport.stream),
        expect=RelayExpectation(status=403, provider_call=None, refusal=True),
    )


def _cases() -> tuple[RelayContractCase, ...]:
    cases: list[RelayContractCase] = []
    for transport in _TRANSPORTS:
        api = transport.api
        for kind, body in (
            ("streamed answer", transport.stream),
            ("tool call", transport.tool_call),
        ):
            cases.append(
                RelayContractCase(
                    name=f"{api}: a {kind} is forwarded unchanged",
                    api=api,
                    provider_base_url=transport.provider_base_url,
                    request=_request(transport),
                    provider_answer=ProviderAnswer(
                        status=200, headers={**_SSE, "x-provider-header": "kept"}, body=body
                    ),
                    expect=RelayExpectation(
                        status=200,
                        provider_call=_secret_call(transport),
                        body=body,
                        headers={**_SSE, "x-provider-header": "kept"},
                    ),
                )
            )
        error = json.dumps({"error": {"type": "rate_limit_error", "message": "slow"}}).encode()
        cases.append(
            RelayContractCase(
                name=f"{api}: an error answer is forwarded unchanged",
                api=api,
                provider_base_url=transport.provider_base_url,
                request=_request(transport),
                provider_answer=ProviderAnswer(
                    status=429,
                    headers={"content-type": "application/json", "retry-after": "7"},
                    body=error,
                ),
                expect=RelayExpectation(
                    status=429,
                    provider_call=_secret_call(transport),
                    body=error,
                    headers={"retry-after": "7"},
                ),
            )
        )
        cases.append(
            RelayContractCase(
                name=f"{api}: a redirect is returned and not followed",
                api=api,
                provider_base_url=transport.provider_base_url,
                request=_request(transport),
                provider_answer=ProviderAnswer(
                    status=307, headers={"location": "https://elsewhere.test/collect"}
                ),
                expect=RelayExpectation(
                    status=307,
                    provider_call=_secret_call(transport),
                    headers={"location": "https://elsewhere.test/collect"},
                ),
            )
        )
        cases.append(
            _refused(
                f"{api}: an unknown pass is refused",
                transport,
                _request(transport, session_pass=CONTRACT_UNKNOWN_PASS),
            )
        )
        no_pass = _request(transport)
        cases.append(
            _refused(
                f"{api}: a call without a pass is refused",
                transport,
                RelayRequest(
                    method=no_pass.method,
                    path=transport.path,
                    headers={
                        name: value
                        for name, value in no_pass.headers.items()
                        if name not in {"authorization", "x-api-key"}
                    },
                    body=no_pass.body,
                ),
            )
        )
        cases.append(
            _refused(
                f"{api}: another protocol's path is refused",
                transport,
                _request(transport, path=transport.other_protocols_path),
            )
        )
        cases.append(
            _refused(
                f"{api}: a path outside the protocol's is refused",
                transport,
                _request(transport, path="/files"),
            )
        )
        cases.append(
            _refused(
                f"{api}: an address in the path is refused",
                transport,
                _request(transport, path="//elsewhere.test" + transport.path),
            )
        )
    codex = next(
        transport for transport in _TRANSPORTS if transport.api == "openai-codex-responses"
    )
    headers, _query = _with_pass(codex, "{pass}")
    cases.append(
        RelayContractCase(
            name="openai-codex-responses: model discovery is forwarded",
            api=codex.api,
            provider_base_url=codex.provider_base_url,
            request=RelayRequest(
                method="GET", path="/codex/models?client_version=1", headers=headers
            ),
            provider_answer=ProviderAnswer(
                status=200, headers={"content-type": "application/json"}, body=b'{"models":[]}'
            ),
            expect=RelayExpectation(
                status=200,
                provider_call=ExpectedProviderCall(
                    method="GET",
                    url="https://codex.provider.test/backend-api/codex/models?client_version=1",
                    headers={
                        "authorization": "Bearer {secret}",
                        "chatgpt-account-id": "{account_id}",
                    },
                ),
                body=b'{"models":[]}',
            ),
        )
    )
    return tuple(cases)


RELAY_CONTRACT_CASES: tuple[RelayContractCase, ...] = _cases()
