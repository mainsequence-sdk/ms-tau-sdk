"""Typed async client for Main Sequence TAU SDK's backend contracts."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Mapping
from typing import Any, TypeVar
from urllib.parse import urlencode

import httpx
import structlog
from pydantic import BaseModel, TypeAdapter, ValidationError
from tau_coding.oauth import account_id_from_access_token

from ms_tau_sdk.errors import BackendConflictError, BackendError, SessionNotFoundError
from ms_tau_sdk.settings import TauSDKSettings

from .auth import BackendAuth
from .models import (
    AgentCardEnvelope,
    AgentSession,
    AgentTask,
    AgentTaskCreateResult,
    AgentTaskDispatch,
    AgentTaskEventPage,
    AgentTaskExecutionAttempt,
    AgentTaskSnapshot,
    ProviderControl,
    ProviderCredential,
    ProviderExecutionEvidence,
    RuntimeActivityPatch,
    RuntimeLease,
    RuntimeLeaseReleaseRequest,
    RuntimeLeaseRenewRequest,
    RuntimeLeaseRequest,
    RuntimeState,
    RuntimeStatePatch,
    SessionEntryAppendRequest,
    SessionEntryBatchAppendRequest,
    SessionEntryBatchAppendResponse,
    SessionEntryList,
    SessionEntryRecord,
    TauResumeSnapshotUploadRequest,
    TauResumeSnapshotUploadResponse,
    TauRuntimeBootstrap,
    TauRuntimeBootstrapRequest,
)
from .routes import (
    AGENT_TASKS,
    agent_session,
    agent_session_agent_card,
    agent_session_entries,
    agent_session_entries_append,
    agent_session_entries_append_batch,
    agent_session_runtime_cancel_request,
    agent_session_runtime_lease,
    agent_session_runtime_state,
    agent_session_tau_resume_snapshot,
    agent_session_tau_runtime_activity,
    agent_session_tau_runtime_bootstrap,
    agent_task_operation,
    model_provider_credentials,
)

T = TypeVar("T")
RETRYABLE_BACKEND_STATUS_CODES = frozenset({500, 502, 503, 504})
logger = structlog.get_logger(__name__)
PATH_IDENTIFIER_PATTERN = re.compile(
    r"(?<=/)(?:[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9]+)(?=/|$)",
    re.IGNORECASE,
)


def _dependency_operation(method: str, path: str) -> str:
    normalized_path = PATH_IDENTIFIER_PATTERN.sub("{id}", path.split("?", 1)[0])
    return f"{method.upper()} {normalized_path[:192]}"


def _field_error_paths(value: object, *, prefix: str = "") -> list[str]:
    if isinstance(value, Mapping):
        paths: list[str] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_field_error_paths(child, prefix=child_prefix))
        return paths
    if isinstance(value, list):
        paths = []
        for index, child in enumerate(value):
            child_prefix = f"{prefix}.{index}" if prefix else str(index)
            paths.extend(_field_error_paths(child, prefix=child_prefix))
        return paths
    return [prefix] if prefix else []


def _backend_rejection_log_fields(detail: object) -> dict[str, object]:
    if not isinstance(detail, Mapping):
        return {}
    fields: dict[str, object] = {}
    error_code = detail.get("error_code")
    error_detail = detail.get("error_detail")
    if isinstance(error_code, str):
        fields["backend_error_code"] = error_code[:128]
    if isinstance(error_detail, str):
        fields["backend_error_detail"] = error_detail[:500]
    paths = sorted(set(_field_error_paths(detail.get("field_errors"))))
    if paths:
        fields["backend_field_error_paths"] = paths[:64]
    return fields


class MainSequenceClient:
    def __init__(
        self,
        settings: TauSDKSettings,
        auth: BackendAuth,
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
        include_status: bool = False,
        dependency_fields: Mapping[str, Any] | None = None,
    ) -> Any:
        attempts = 3 if idempotent else 1
        force_auth = False
        auth_retried = False
        transport_attempt = 0
        last_error: Exception | None = None
        while transport_attempt < attempts:
            attempt_started_at = time.monotonic()
            attempt_number = transport_attempt + 1
            operation = _dependency_operation(method, path)
            try:
                response = await self._client.request(
                    method,
                    path,
                    headers=await self.auth.headers(force=force_auth),
                    json=json,
                )
                response_detail = self._safe_body(response) if not response.is_success else None
                status_outcome = (
                    "success"
                    if response.is_success
                    else "rejected"
                    if response.status_code < 500
                    else "failed"
                )
                logger.info(
                    "dependency.call.completed",
                    message="Backend dependency call completed",
                    dependency_operation=operation,
                    target_system="mainsequence_backend",
                    dependency_attempt=attempt_number,
                    status_code=response.status_code,
                    duration_ms=round(
                        (time.monotonic() - attempt_started_at) * 1000,
                        3,
                    ),
                    outcome=status_outcome,
                    retryable=response.status_code in RETRYABLE_BACKEND_STATUS_CODES,
                    circuit_breaker_state="not_configured",
                    **_backend_rejection_log_fields(response_detail),
                    **dict(dependency_fields or {}),
                )
                if response.status_code == 401 and not auth_retried:
                    auth_retried = True
                    force_auth = True
                    continue
                if idempotent and response.status_code in RETRYABLE_BACKEND_STATUS_CODES:
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
                        detail=response_detail,
                    )
                if not response.is_success:
                    raise BackendError(
                        f"Backend request failed: {method} {path}",
                        status_code=response.status_code,
                        detail=response_detail,
                    )
                if len(response.content) > self.settings.backend_max_response_bytes:
                    raise BackendError("Backend response exceeded configured size limit")
                data = response.json() if response.content else None
                return (data, response.status_code) if include_status else data
            except httpx.TransportError as error:
                logger.warning(
                    "dependency.call.failed",
                    message="Backend dependency call failed",
                    dependency_operation=operation,
                    target_system="mainsequence_backend",
                    dependency_attempt=attempt_number,
                    duration_ms=round(
                        (time.monotonic() - attempt_started_at) * 1000,
                        3,
                    ),
                    error_type=type(error).__name__,
                    outcome="failed",
                    retryable=idempotent,
                    circuit_breaker_state="not_configured",
                    **dict(dependency_fields or {}),
                )
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

    async def append_entries(
        self,
        session_uid: str,
        request: SessionEntryBatchAppendRequest,
    ) -> SessionEntryBatchAppendResponse:
        canonical_bytes = sum(
            len(
                json.dumps(
                    item.entry,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
            for item in request.entries
        )
        data = await self._request(
            "POST",
            agent_session_entries_append_batch(session_uid),
            json=self._dump(request),
            idempotent=True,
            dependency_fields={
                "persistence_phase": "batch_append",
                "entry_count": len(request.entries),
                "canonical_bytes": canonical_bytes,
                "expected_sequence": request.expected_sequence,
            },
        )
        return SessionEntryBatchAppendResponse.model_validate(data)

    async def bootstrap_tau_runtime(
        self,
        session_uid: str,
        request: TauRuntimeBootstrapRequest,
    ) -> TauRuntimeBootstrap:
        data = await self._request(
            "POST",
            agent_session_tau_runtime_bootstrap(session_uid),
            json=self._dump(request),
            idempotent=True,
            dependency_fields={
                "bootstrap_request_uid": request.bootstrap_request_uid,
                "persistence_phase": "runtime_bootstrap",
            },
        )
        return TauRuntimeBootstrap.model_validate(data)

    async def upload_tau_resume_snapshot(
        self,
        session_uid: str,
        request: TauResumeSnapshotUploadRequest,
    ) -> TauResumeSnapshotUploadResponse:
        data = await self._request(
            "PUT",
            agent_session_tau_resume_snapshot(session_uid),
            json=self._dump(request),
            idempotent=True,
            dependency_fields={
                "turn_uid": request.last_committed_turn_uid,
                "persistence_phase": "snapshot_upload",
                "base_sequence": request.base_sequence,
            },
        )
        return TauResumeSnapshotUploadResponse.model_validate(data)

    async def patch_runtime_activity(
        self,
        session_uid: str,
        request: RuntimeActivityPatch,
    ) -> RuntimeState:
        data = await self._request(
            "PATCH",
            agent_session_tau_runtime_activity(session_uid),
            json=self._dump(request),
            idempotent=True,
        )
        return RuntimeState.model_validate(data)

    async def acquire_runtime_lease(
        self,
        session_uid: str,
        request: RuntimeLeaseRequest,
    ) -> RuntimeLease:
        data = await self._request(
            "POST",
            agent_session_runtime_lease(session_uid, "acquire"),
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
            agent_session_runtime_lease(session_uid, "renew"),
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
            agent_session_runtime_lease(session_uid, "release"),
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
        model: str,
        session_uid: str,
        holder_id: str,
    ) -> ProviderExecutionEvidence:
        data = await self._request(
            "POST",
            model_provider_credentials("hydrate"),
            json={
                "agent_session_uid": session_uid,
                "providers": [provider],
                "holder_id": holder_id,
                "supported_provider_control_schema_versions": [1],
                "execution_selection": {
                    "provider": provider,
                    "model": model,
                },
            },
            idempotent=True,
        )
        return self._provider_evidence(provider, data)

    async def hydrate_local_provider_credential(
        self,
        provider: str,
        *,
        model: str,
        thinking_level: str | None,
        holder_id: str,
    ) -> ProviderExecutionEvidence:
        """Hydrate one explicit selection for the authenticated local user."""

        selection: dict[str, str] = {"provider": provider, "model": model}
        if thinking_level:
            selection["thinking_level"] = thinking_level
        data = await self._request(
            "POST",
            model_provider_credentials("hydrate"),
            json={
                "providers": [provider],
                "holder_id": holder_id,
                "supported_provider_control_schema_versions": [1],
                "execution_selection": selection,
            },
            idempotent=True,
        )
        return self._provider_evidence(provider, data)

    def _provider_evidence(self, provider: str, data: object) -> ProviderExecutionEvidence:
        if not isinstance(data, dict):
            raise BackendError("Backend provider hydration response is invalid")
        try:
            provider_control = ProviderControl.model_validate(data.get("provider_control"))
        except ValidationError as error:
            raise BackendError(
                "Backend provider hydration omitted valid provider-control evidence"
            ) from error
        return ProviderExecutionEvidence(
            credential=self.provider_credential_from_hydration(provider, data),
            provider_control=provider_control,
        )

    @staticmethod
    def provider_credential_from_hydration(
        provider: str,
        data: object,
    ) -> ProviderCredential:
        credentials = data.get("credentials", {}) if isinstance(data, dict) else {}
        hydrated = credentials.get(provider)
        if not isinstance(hydrated, dict):
            raise BackendError(f"No active backend credential for provider {provider}")
        raw = hydrated.get("credential", {})
        if not isinstance(raw, dict):
            raise BackendError(f"Backend credential for {provider} is invalid")
        credential_kind = str(hydrated.get("credential_kind") or raw.get("type") or "api_key")
        access_token = raw.get("access_token") or raw.get("access")
        account_id = raw.get("account_id")
        if provider == "openai-codex" and isinstance(access_token, str) and not account_id:
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

    async def create_task(self, payload: Mapping[str, Any]) -> AgentTaskCreateResult:
        data, status_code = await self._request(
            "POST",
            AGENT_TASKS,
            json=payload,
            idempotent=True,
            include_status=True,
        )
        return AgentTaskCreateResult(
            task=AgentTask.model_validate(data),
            created=status_code == 201,
        )

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

    async def get_task(self, task_uid: str) -> AgentTask:
        data = await self._request(
            "GET",
            f"{AGENT_TASKS}{task_uid}/",
            idempotent=True,
        )
        return AgentTask.model_validate(data)

    async def get_task_snapshot(self, task_uid: str) -> AgentTaskSnapshot:
        data = await self._request(
            "GET",
            agent_task_operation(task_uid, "snapshot"),
            idempotent=True,
        )
        return AgentTaskSnapshot.model_validate(data)

    async def get_task_snapshot_by_protocol_id(self, task_id: str) -> AgentTaskSnapshot:
        task = await self.get_task_by_protocol_id(task_id)
        return await self.get_task_snapshot(task.uid)

    async def list_task_events(
        self,
        task_uid: str,
        *,
        after_sequence: int,
        limit: int = 100,
    ) -> AgentTaskEventPage:
        query = urlencode({"after_sequence": max(0, int(after_sequence)), "limit": int(limit)})
        data = await self._request(
            "GET",
            f"{agent_task_operation(task_uid, 'events')}?{query}",
            idempotent=True,
        )
        return AgentTaskEventPage.model_validate(data)

    async def claim_task_dispatch(
        self,
        task_uid: str,
        *,
        holder_id: str,
        lease_token: str,
        dispatch_uid: str,
        executor_runtime_id: str = "",
        executor_instance_id: str = "",
    ) -> AgentTaskExecutionAttempt:
        payload: dict[str, Any] = {
            "holder_id": holder_id,
            "lease_token": lease_token,
            "dispatch_uid": dispatch_uid,
            "executor_runtime_id": executor_runtime_id,
            "executor_instance_id": executor_instance_id,
        }
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "dispatches/claim"),
            json=payload,
            idempotent=True,
        )
        return AgentTaskExecutionAttempt.model_validate(data)

    async def list_task_dispatches(self, task_uid: str) -> list[AgentTaskDispatch]:
        data = await self._request(
            "GET",
            agent_task_operation(task_uid, "dispatches"),
            idempotent=True,
        )
        return TypeAdapter(list[AgentTaskDispatch]).validate_python(data)

    async def start_task_attempt(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
    ) -> AgentTaskExecutionAttempt:
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "attempts/start"),
            json={
                "attempt_uid": attempt_uid,
                "holder_id": holder_id,
                "lease_token": lease_token,
            },
            idempotent=True,
        )
        return AgentTaskExecutionAttempt.model_validate(data)

    async def create_task_output(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        artifact_id: str,
        parts: list[dict[str, Any]],
        name: str = "",
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attempt_uid": attempt_uid,
            "holder_id": holder_id,
            "lease_token": lease_token,
            "artifact_id": artifact_id,
            "parts": parts,
            "name": name,
            "description": description,
        }
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        if extensions is not None:
            payload["extensions"] = dict(extensions)
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "outputs/create"),
            json=payload,
            idempotent=True,
        )
        if not isinstance(data, dict):
            raise BackendError("Backend Task output-create response is invalid")
        return data

    async def append_task_output(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        output_uid: str,
        expected_revision: int,
        parts: list[dict[str, Any]],
        metadata: Mapping[str, Any] | None = None,
        extensions: Mapping[str, Any] | None = None,
        last_chunk: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attempt_uid": attempt_uid,
            "holder_id": holder_id,
            "lease_token": lease_token,
            "output_uid": output_uid,
            "expected_revision": expected_revision,
            "parts": parts,
            "last_chunk": last_chunk,
        }
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        if extensions is not None:
            payload["extensions"] = dict(extensions)
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "outputs/append"),
            json=payload,
            idempotent=True,
        )
        if not isinstance(data, dict):
            raise BackendError("Backend Task output-append response is invalid")
        return data

    async def finalize_task_output(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        output_uid: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "outputs/finalize"),
            json={
                "attempt_uid": attempt_uid,
                "holder_id": holder_id,
                "lease_token": lease_token,
                "output_uid": output_uid,
                "expected_revision": expected_revision,
            },
            idempotent=True,
        )
        if not isinstance(data, dict):
            raise BackendError("Backend Task output-finalize response is invalid")
        return data

    async def settle_task_attempt(
        self,
        task_uid: str,
        *,
        attempt_uid: str,
        holder_id: str,
        lease_token: str,
        status: str,
        status_message: Mapping[str, Any] | None = None,
        outcome_category: str = "",
        failure_detail: str = "",
    ) -> AgentTaskExecutionAttempt:
        payload: dict[str, Any] = {
            "attempt_uid": attempt_uid,
            "holder_id": holder_id,
            "lease_token": lease_token,
            "status": status,
        }
        if status_message is not None:
            payload["status_message"] = dict(status_message)
        if outcome_category:
            payload["outcome_category"] = outcome_category
        if failure_detail:
            payload["failure_detail"] = failure_detail
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "attempts/settle"),
            json=payload,
            idempotent=True,
        )
        return AgentTaskExecutionAttempt.model_validate(data)

    async def continue_task(
        self,
        task_uid: str,
        message: Mapping[str, Any],
    ) -> AgentTask:
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "continue"),
            json=message,
            idempotent=True,
        )
        return AgentTask.model_validate(data)

    async def cancel_task(self, task_uid: str) -> AgentTask:
        data = await self._request(
            "POST",
            agent_task_operation(task_uid, "cancel"),
            json={},
            idempotent=True,
        )
        return AgentTask.model_validate(data)
