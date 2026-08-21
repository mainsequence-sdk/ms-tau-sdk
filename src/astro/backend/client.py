"""Typed async client for Astro's backend contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, TypeVar
from urllib.parse import urlencode

import httpx
import structlog
from pydantic import BaseModel, TypeAdapter, ValidationError
from tau_coding.oauth import account_id_from_access_token

from astro.errors import BackendConflictError, BackendError, SessionNotFoundError
from astro.settings import Settings

from .auth import RuntimeCredentialAuth
from .models import (
    AgentCardEnvelope,
    AgentSession,
    AgentTask,
    CapabilityContent,
    ProviderCredential,
    ProviderStatus,
    RuntimeLease,
    RuntimeLeaseReleaseRequest,
    RuntimeLeaseRenewRequest,
    RuntimeLeaseRequest,
    RuntimeState,
    RuntimeStatePatch,
    SessionCapabilityBinding,
    SessionEntryAppendRequest,
    SessionEntryList,
    SessionEntryRecord,
)
from .routes import (
    AGENT_TASKS,
    agent_capability_content,
    agent_session,
    agent_session_agent_card,
    agent_session_capabilities,
    agent_session_checkpoint_lease,
    agent_session_entries,
    agent_session_entries_append,
    agent_session_runtime_cancel_request,
    agent_session_runtime_state,
    agent_task_operation,
    model_provider_credentials,
)

T = TypeVar("T")
RETRYABLE_BACKEND_STATUS_CODES = frozenset({500, 502, 503, 504})
logger = structlog.get_logger(__name__)


class MainSequenceClient:
    def __init__(
        self,
        settings: Settings,
        auth: RuntimeCredentialAuth,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.auth = auth
        self._client = client or httpx.AsyncClient(
            base_url=settings.backend_url,
            timeout=httpx.Timeout(
                connect=settings.backend_connect_timeout_seconds,
                read=settings.backend_read_timeout_seconds,
                write=settings.backend_write_timeout_seconds,
                pool=settings.backend_pool_timeout_seconds,
            ),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        self._owns_client = client is None
        self.auth.bind_client(self._client)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        idempotent: bool = False,
    ) -> Any:
        attempts = 3 if idempotent else 1
        force_auth = False
        auth_retried = False
        transport_attempt = 0
        last_error: Exception | None = None
        while transport_attempt < attempts:
            try:
                response = await self._client.request(
                    method,
                    path,
                    headers=await self.auth.headers(force=force_auth),
                    json=json,
                )
                if response.status_code == 401 and not auth_retried:
                    auth_retried = True
                    force_auth = True
                    continue
                if (
                    idempotent
                    and response.status_code in RETRYABLE_BACKEND_STATUS_CODES
                ):
                    transport_attempt += 1
                    if transport_attempt < attempts:
                        delay_seconds = 0.25 * (2 ** (transport_attempt - 1))
                        logger.warning(
                            "backend.request.retrying",
                            message="Retrying idempotent backend request",
                            method=method,
                            path=path,
                            status_code=response.status_code,
                            attempt=transport_attempt + 1,
                            max_attempts=attempts,
                            delay_seconds=delay_seconds,
                        )
                        await asyncio.sleep(delay_seconds)
                        continue
                if response.status_code == 404:
                    raise SessionNotFoundError(f"Backend resource not found: {path}")
                if response.status_code == 409:
                    raise BackendConflictError(
                        f"Backend conflict for {path}",
                        status_code=response.status_code,
                        detail=self._safe_body(response),
                    )
                if not response.is_success:
                    raise BackendError(
                        f"Backend request failed: {method} {path}",
                        status_code=response.status_code,
                        detail=self._safe_body(response),
                    )
                if len(response.content) > self.settings.backend_max_response_bytes:
                    raise BackendError("Backend response exceeded configured size limit")
                return response.json() if response.content else None
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
                transport_attempt += 1
                if transport_attempt >= attempts:
                    break
                await asyncio.sleep(0.25 * (2 ** (transport_attempt - 1)))
        raise BackendError(f"Backend transport failed: {last_error}") from last_error

    @staticmethod
    def _safe_body(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError:
            return response.text[:500]

    @staticmethod
    def _dump(model: BaseModel) -> dict[str, Any]:
        return model.model_dump(mode="json", exclude_none=True)

    async def get_session(self, session_uid: str) -> AgentSession:
        data = await self._request(
            "GET",
            agent_session(session_uid),
            idempotent=True,
        )
        try:
            return AgentSession.model_validate(data)
        except ValidationError as error:
            invalid_fields = sorted(
                {
                    ".".join(str(part) for part in item["loc"])
                    for item in error.errors(include_input=False)
                }
            )
            raise BackendError(
                "Backend session response does not satisfy the required harness "
                f"contract: {', '.join(invalid_fields)}",
                detail=error.errors(include_input=False),
            ) from error

    async def update_session_config(
        self,
        session_uid: str,
        *,
        provider: str,
        model: str,
        thinking_level: str | None,
    ) -> AgentSession:
        payload: dict[str, Any] = {
            "llm_provider": provider,
            "llm_model": model,
        }
        if thinking_level is not None:
            payload["llm_thinking"] = thinking_level
        data = await self._request(
            "PATCH",
            agent_session(session_uid),
            json=payload,
            idempotent=True,
        )
        return AgentSession.model_validate(data)

    async def get_agent_card(self, session_uid: str) -> AgentCardEnvelope:
        data = await self._request(
            "GET",
            agent_session_agent_card(session_uid),
            idempotent=True,
        )
        return AgentCardEnvelope.model_validate(data)

    async def list_session_capabilities(
        self,
        session_uid: str,
    ) -> list[SessionCapabilityBinding]:
        data = await self._request(
            "GET",
            agent_session_capabilities(session_uid),
            idempotent=True,
        )
        values = data.get("results", []) if isinstance(data, dict) else data
        return TypeAdapter(list[SessionCapabilityBinding]).validate_python(values or [])

    async def get_capability_content(self, capability_uid: str) -> CapabilityContent:
        data = await self._request(
            "GET",
            agent_capability_content(capability_uid),
            idempotent=True,
        )
        return CapabilityContent.model_validate(data)

    async def get_entries(self, session_uid: str) -> SessionEntryList:
        entries: list[SessionEntryRecord] = []
        after_sequence: int | None = None
        next_sequence = 0
        while True:
            query = {"limit": 500}
            if after_sequence is not None:
                query["after_sequence"] = after_sequence
            data = await self._request(
                "GET",
                f"{agent_session_entries(session_uid)}?{urlencode(query)}",
                idempotent=True,
            )
            if isinstance(data, list):
                records = TypeAdapter(list[SessionEntryRecord]).validate_python(data)
                entries.extend(records)
                next_sequence = len(entries)
                break
            page = SessionEntryList.model_validate(data)
            entries.extend(page.entries)
            next_sequence = page.next_sequence
            if not page.has_more:
                break
            if page.next_after_sequence is None:
                raise BackendError(
                    "Backend entry page reports has_more without next_after_sequence"
                )
            if after_sequence == page.next_after_sequence:
                raise BackendError("Backend entry pagination did not advance")
            after_sequence = page.next_after_sequence
        return SessionEntryList(entries=entries, next_sequence=next_sequence)

    async def append_entry(
        self,
        session_uid: str,
        request: SessionEntryAppendRequest,
    ) -> SessionEntryRecord:
        data = await self._request(
            "POST",
            agent_session_entries_append(session_uid),
            json=self._dump(request),
            idempotent=True,
        )
        return SessionEntryRecord.model_validate(data)

    async def acquire_runtime_lease(
        self,
        session_uid: str,
        request: RuntimeLeaseRequest,
    ) -> RuntimeLease:
        data = await self._request(
            "POST",
            agent_session_checkpoint_lease(session_uid, "acquire"),
            json=self._dump(request),
        )
        return RuntimeLease.model_validate(data)

    async def renew_runtime_lease(
        self,
        session_uid: str,
        request: RuntimeLeaseRenewRequest,
    ) -> RuntimeLease:
        data = await self._request(
            "POST",
            agent_session_checkpoint_lease(session_uid, "renew"),
            json=self._dump(request),
            idempotent=True,
        )
        return RuntimeLease.model_validate(data)

    async def release_runtime_lease(
        self,
        session_uid: str,
        request: RuntimeLeaseReleaseRequest,
    ) -> None:
        await self._request(
            "POST",
            agent_session_checkpoint_lease(session_uid, "release"),
            json=self._dump(request),
            idempotent=True,
        )

    async def get_runtime_state(self, session_uid: str) -> RuntimeState:
        data = await self._request(
            "GET",
            agent_session_runtime_state(session_uid),
            idempotent=True,
        )
        return RuntimeState.model_validate(data)

    async def patch_runtime_state(
        self,
        session_uid: str,
        request: RuntimeStatePatch,
    ) -> RuntimeState:
        data = await self._request(
            "PATCH",
            agent_session_runtime_state(session_uid),
            json=self._dump(request),
            idempotent=True,
        )
        return RuntimeState.model_validate(data)

    async def request_runtime_cancel(
        self,
        session_uid: str,
        *,
        reason: str = "user_requested",
        message: str = "",
        requested_by_holder_id: str = "",
    ) -> RuntimeState:
        data = await self._request(
            "POST",
            agent_session_runtime_cancel_request(session_uid),
            json={
                "reason": reason,
                "message": message,
                "requested_by_holder_id": requested_by_holder_id,
            },
            idempotent=True,
        )
        return RuntimeState.model_validate(data)

    async def hydrate_provider_credential(
        self,
        provider: str,
        *,
        created_by_user_uid: str | None,
        session_uid: str | None,
        holder_id: str,
    ) -> ProviderCredential:
        if not created_by_user_uid:
            raise BackendError("Provider hydration requires created-by user identity")
        data = await self._request(
            "POST",
            model_provider_credentials("hydrate"),
            json={
                "created_by_user_uid": created_by_user_uid,
                "agent_session_uid": session_uid,
                "providers": [provider],
                "holder_id": holder_id,
            },
            idempotent=True,
        )
        credentials = data.get("credentials", {}) if isinstance(data, dict) else {}
        hydrated = credentials.get(provider)
        if not isinstance(hydrated, dict):
            raise BackendError(f"No active backend credential for provider {provider}")
        raw = hydrated.get("credential", {})
        if not isinstance(raw, dict):
            raise BackendError(f"Backend credential for {provider} is invalid")
        credential_kind = str(
            hydrated.get("credential_kind") or raw.get("type") or "api_key"
        )
        access_token = raw.get("access_token") or raw.get("access")
        account_id = raw.get("account_id")
        if (
            provider == "openai-codex"
            and isinstance(access_token, str)
            and not account_id
        ):
            account_id = account_id_from_access_token(access_token)
        return ProviderCredential.model_validate(
            {
                "provider": provider,
                "credential_kind": credential_kind,
                "api_key": raw.get("api_key") or raw.get("key"),
                "access_token": access_token,
                "expires_at": raw.get("expires_at") or raw.get("expires"),
                "account_id": account_id,
                "base_url": raw.get("base_url"),
                "headers": raw.get("headers", {}),
                "api": raw.get("api"),
                "metadata": {
                    "version": hydrated.get("version"),
                    "credential_hash": hydrated.get("credential_hash"),
                },
            }
        )

    async def list_provider_statuses(
        self,
        *,
        created_by_user_uid: str | None,
    ) -> list[ProviderStatus]:
        data = await self._request(
            "GET",
            (
                model_provider_credentials("status")
                + f"?created_by_user_uid={created_by_user_uid or ''}"
            ),
            idempotent=True,
        )
        values = data.get("providers", {}) if isinstance(data, dict) else {}
        if not isinstance(values, dict):
            raise BackendError("Backend provider status response is invalid")
        return [
            ProviderStatus.model_validate({"provider": name, **value})
            for name, value in values.items()
            if isinstance(value, dict)
        ]

    async def flush_provider_credential(
        self,
        *,
        provider: str,
        created_by_user_uid: str,
        session_uid: str | None,
        credential: Mapping[str, Any],
        base_version: int = 0,
        reason: str = "signin_completed",
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            model_provider_credentials("flush"),
            json={
                "created_by_user_uid": created_by_user_uid,
                "agent_session_uid": session_uid,
                "provider": provider,
                "base_version": base_version,
                "reason": reason,
                "credential": dict(credential),
            },
            idempotent=True,
        )
        if not isinstance(data, dict):
            raise BackendError("Backend credential flush response is invalid")
        return data

    async def revoke_provider_credential(
        self,
        *,
        provider: str,
        created_by_user_uid: str,
        reason: str = "user_signoff",
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            model_provider_credentials("revoke"),
            json={
                "created_by_user_uid": created_by_user_uid,
                "provider": provider,
                "reason": reason,
            },
            idempotent=True,
        )
        if not isinstance(data, dict):
            raise BackendError("Backend credential revoke response is invalid")
        return data

    async def create_task(self, payload: Mapping[str, Any]) -> AgentTask:
        data = await self._request(
            "POST",
            AGENT_TASKS,
            json=payload,
            idempotent=True,
        )
        return AgentTask.model_validate(data)

    async def list_tasks(self, **filters: str) -> list[AgentTask]:
        query = urlencode({key: value for key, value in filters.items() if value})
        path = AGENT_TASKS + (f"?{query}" if query else "")
        data = await self._request("GET", path, idempotent=True)
        values = data.get("results", []) if isinstance(data, dict) else data
        return TypeAdapter(list[AgentTask]).validate_python(values or [])

    async def get_task_by_protocol_id(self, task_id: str) -> AgentTask:
        tasks = await self.list_tasks(task_id=task_id)
        if not tasks:
            raise SessionNotFoundError(f"A2A task not found: {task_id}")
        return tasks[0]

    async def update_task_status(
        self,
        task_uid: str,
        *,
        status: str,
        status_message: object | None = None,
    ) -> AgentTask:
        payload: dict[str, Any] = {"status": status}
        if status_message is not None:
            payload["status_message"] = status_message
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "status"),
            json=payload,
            idempotent=True,
        )
        return AgentTask.model_validate(data)

    async def add_task_message(
        self,
        task_uid: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "messages"),
            json=payload,
            idempotent=True,
        )
        if not isinstance(data, dict):
            raise BackendError("Backend task message response is invalid")
        return data

    async def cancel_task(self, task_uid: str) -> AgentTask:
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "cancel"),
            json={},
            idempotent=True,
        )
        return AgentTask.model_validate(data)
