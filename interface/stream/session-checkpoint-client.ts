import {
	resolveBackendAuthHeaders,
	resolveBackendUrl,
	type BackendAuthHeaders,
} from "../../pi/extensions/shared/agent-registration.js";

export type CheckpointBundle = {
	pi_session_jsonl: string;
	astro_metadata_json: Record<string, unknown>;
	thread_binding_json: Record<string, unknown>;
	session_overrides_json?: Record<string, unknown> | null;
};

export type CheckpointLeaseResponse = {
	agent_session_id: number;
	holder_id: string;
	lease_token: string;
	lease_expires_at: string;
	checkpoint_version: number;
	bundle_hash: string;
	agent_session_status?: string;
	working?: boolean;
	cancel_requested?: boolean;
	cancellation?: CheckpointCancellation | null;
};

export type CheckpointRestoreResponse = {
	agent_session_id: number;
	checkpoint_version: number;
	bundle_hash: string;
	updated_at: string | null;
	bundle: CheckpointBundle;
	retention?: CheckpointRetention | null;
};

export type CheckpointLatestResponse = CheckpointRestoreResponse;

export type CheckpointRetention = {
	latest_compaction_entry_id?: string | null;
	latest_compaction_at?: string | null;
	latest_compaction_first_kept_entry_id?: string | null;
	latest_compaction_tokens_before?: number | null;
	compaction_retention_applied_at?: string | null;
	compaction_pruned_entry_count?: number | null;
	pruned_entry_count?: number | null;
};

export type CheckpointFlushResponse = {
	agent_session_id: number;
	checkpoint_version: number;
	bundle_hash: string;
	noop: boolean;
	updated_at: string | null;
	normalized?: boolean;
	normalized_reason?: "compaction" | null;
	bundle?: CheckpointBundle | null;
	retention?: CheckpointRetention | null;
};

export type CheckpointLeasePurpose = "runtime_run" | "read_restore";

export type CheckpointCancellation = {
	cancellation_id: string;
	requested_at?: string | null;
	requested_by_user?: string | null;
	reason?: string | null;
	message?: string | null;
};

export type AgentSessionTerminalState = {
	status: "completed" | "error" | "cancelled";
	error_code: string | null;
	error_detail: string | null;
};

export type RuntimeCancelResponse = {
	agent_session_id: number;
	status: string;
	working: boolean;
	cancel_state: "not_running" | "requested" | string;
	cancellation_id: string | null;
	active_holder_id: string | null;
	lease_expires_at: string | null;
};

export type SessionInsightsUpdateResponse = {
	agent_session_id: number;
	checkpoint_version: number;
	bundle_hash: string;
	computed_at: string;
	flushed_at?: string | null;
	reason?: string | null;
	insights: Record<string, unknown>;
};

export type SessionCheckpointClientResult<T> =
	| {
			ok: true;
			status: number;
			body: T;
	  }
	| {
			ok: false;
			status: number | null;
			error: string;
			body: unknown;
			responseText: string | null;
			url: string | null;
	  };

async function readResponseBody(response: Response): Promise<{ text: string; json: unknown }> {
	const text = await response.text();
	if (!text.trim()) return { text, json: null };
	try {
		return { text, json: JSON.parse(text) };
	} catch {
		return { text, json: null };
	}
}

function endpoint(backendUrl: string, agentSessionId: number, suffix: string): string {
	return `${backendUrl}/orm/api/agents/v1/sessions/${agentSessionId}/${suffix}`;
}

export class SessionCheckpointClient {
	private readonly backendUrl: string;
	private readonly env: NodeJS.ProcessEnv;
	private readonly log?: (message: string) => void;

	constructor(options: { env?: NodeJS.ProcessEnv; log?: (message: string) => void } = {}) {
		this.env = options.env ?? process.env;
		this.backendUrl = resolveBackendUrl(this.env);
		this.log = options.log;
	}

	async acquireLease(input: {
		agentSessionId: number;
		holderId: string;
		ttlSeconds: number;
		leasePurpose: CheckpointLeasePurpose;
	}): Promise<SessionCheckpointClientResult<CheckpointLeaseResponse>> {
		return this.postJson<CheckpointLeaseResponse>(
			endpoint(this.backendUrl, input.agentSessionId, "checkpoint_lease/acquire/"),
			{
				holder_id: input.holderId,
				ttl_seconds: input.ttlSeconds,
				lease_purpose: input.leasePurpose,
			},
		);
	}

	async renewLease(input: {
		agentSessionId: number;
		holderId: string;
		leaseToken: string;
		ttlSeconds: number;
		leasePurpose: CheckpointLeasePurpose;
	}): Promise<SessionCheckpointClientResult<CheckpointLeaseResponse>> {
		return this.postJson<CheckpointLeaseResponse>(
			endpoint(this.backendUrl, input.agentSessionId, "checkpoint_lease/renew/"),
			{
				holder_id: input.holderId,
				lease_token: input.leaseToken,
				ttl_seconds: input.ttlSeconds,
				lease_purpose: input.leasePurpose,
			},
		);
	}

	async releaseLease(input: {
		agentSessionId: number;
		holderId: string;
		leaseToken: string;
		reason: string;
	}): Promise<SessionCheckpointClientResult<{ agent_session_id: number; released: boolean }>> {
		return this.postJson(endpoint(this.backendUrl, input.agentSessionId, "checkpoint_lease/release/"), {
			holder_id: input.holderId,
			lease_token: input.leaseToken,
			reason: input.reason,
		});
	}

	async restore(input: {
		agentSessionId: number;
		holderId: string;
		leaseToken: string;
	}): Promise<SessionCheckpointClientResult<CheckpointRestoreResponse>> {
		return this.postJson<CheckpointRestoreResponse>(
			endpoint(this.backendUrl, input.agentSessionId, "checkpoint/restore/"),
			{
				holder_id: input.holderId,
				lease_token: input.leaseToken,
			},
		);
	}

	async latest(input: {
		agentSessionId: number;
	}): Promise<SessionCheckpointClientResult<CheckpointLatestResponse>> {
		return this.getJson<CheckpointLatestResponse>(
			endpoint(this.backendUrl, input.agentSessionId, "checkpoint/latest/"),
		);
	}

	async flush(input: {
		agentSessionId: number;
		holderId: string;
		leaseToken: string;
		expectedCheckpointVersion: number;
		reason: string;
		bundleHash: string;
		bundle: CheckpointBundle;
		agentSessionTerminalState?: AgentSessionTerminalState | null;
	}): Promise<SessionCheckpointClientResult<CheckpointFlushResponse>> {
		return this.postJson<CheckpointFlushResponse>(
			endpoint(this.backendUrl, input.agentSessionId, "checkpoint/flush/"),
			{
				holder_id: input.holderId,
				lease_token: input.leaseToken,
				expected_checkpoint_version: input.expectedCheckpointVersion,
				reason: input.reason,
				bundle_hash: input.bundleHash,
				bundle: input.bundle,
				agent_session_terminal_state: input.agentSessionTerminalState ?? null,
				client_capabilities: {
					accepts_normalized_bundle: true,
				},
			},
		);
	}

	async requestRuntimeCancel(input: {
		agentSessionId: number;
		requestedByUser: string;
		requestedByHolderId: string;
		reason: string;
		message?: string | null;
	}): Promise<SessionCheckpointClientResult<RuntimeCancelResponse>> {
		return this.postJson<RuntimeCancelResponse>(
			endpoint(this.backendUrl, input.agentSessionId, "runtime_cancel_request/"),
			{
				requested_by_user: input.requestedByUser,
				requested_by_holder_id: input.requestedByHolderId,
				reason: input.reason,
				message: input.message ?? null,
			},
		);
	}

	async updateInsights(input: {
		agentSessionId: number;
		checkpointVersion: number;
		bundleHash: string;
		computedAt: string;
		reason: string;
		insights: Record<string, unknown>;
	}): Promise<SessionCheckpointClientResult<SessionInsightsUpdateResponse>> {
		return this.putJson<SessionInsightsUpdateResponse>(
			endpoint(this.backendUrl, input.agentSessionId, "insights/"),
			{
				checkpoint_version: input.checkpointVersion,
				bundle_hash: input.bundleHash,
				computed_at: input.computedAt,
				reason: input.reason,
				insights: input.insights,
			},
		);
	}

	private async getJson<T>(url: string): Promise<SessionCheckpointClientResult<T>> {
		const authHeadersResult = await resolveBackendAuthHeaders(this.env, this.log);
		if (!authHeadersResult.headers) {
			return {
				ok: false,
				status: null,
				error: authHeadersResult.error ?? "Missing backend auth headers for checkpoint request.",
				body: null,
				responseText: null,
				url,
			};
		}

		let response = await this.fetchGetJson(url, authHeadersResult.headers);
		if (response.status === 401 || response.status === 403) {
			const retryAuthHeaders = await resolveBackendAuthHeaders(this.env, this.log);
			if (retryAuthHeaders.headers) {
				response = await this.fetchGetJson(url, retryAuthHeaders.headers);
			}
		}

		const body = await readResponseBody(response);
		if (!response.ok) {
			const detail =
				body.json && typeof body.json === "object" && !Array.isArray(body.json)
					? (body.json as { detail?: unknown; error_detail?: unknown; error?: unknown }).detail ??
					  (body.json as { error_detail?: unknown }).error_detail ??
					  (body.json as { error?: unknown }).error
					: null;
			return {
				ok: false,
				status: response.status,
				error: String(detail || body.text || `Checkpoint request failed with status ${response.status}.`),
				body: body.json,
				responseText: body.text,
				url,
			};
		}

		return {
			ok: true,
			status: response.status,
			body: body.json as T,
		};
	}

	private async postJson<T>(
		url: string,
		payload: Record<string, unknown>,
	): Promise<SessionCheckpointClientResult<T>> {
		return this.writeJson<T>("POST", url, payload);
	}

	private async putJson<T>(
		url: string,
		payload: Record<string, unknown>,
	): Promise<SessionCheckpointClientResult<T>> {
		return this.writeJson<T>("PUT", url, payload);
	}

	private async writeJson<T>(
		method: "POST" | "PUT",
		url: string,
		payload: Record<string, unknown>,
	): Promise<SessionCheckpointClientResult<T>> {
		const authHeadersResult = await resolveBackendAuthHeaders(this.env, this.log);
		if (!authHeadersResult.headers) {
			return {
				ok: false,
				status: null,
				error: authHeadersResult.error ?? "Missing backend auth headers for checkpoint request.",
				body: null,
				responseText: null,
				url,
			};
		}

		let response = await this.fetchJson(method, url, authHeadersResult.headers, payload);
		if (response.status === 401 || response.status === 403) {
			const retryAuthHeaders = await resolveBackendAuthHeaders(this.env, this.log);
			if (retryAuthHeaders.headers) {
				response = await this.fetchJson(method, url, retryAuthHeaders.headers, payload);
			}
		}

		const body = await readResponseBody(response);
		if (!response.ok) {
			const detail =
				body.json && typeof body.json === "object" && !Array.isArray(body.json)
					? (body.json as { detail?: unknown; error_detail?: unknown; error?: unknown }).detail ??
					  (body.json as { error_detail?: unknown }).error_detail ??
					  (body.json as { error?: unknown }).error
					: null;
			return {
				ok: false,
				status: response.status,
				error: String(detail || body.text || `Checkpoint request failed with status ${response.status}.`),
				body: body.json,
				responseText: body.text,
				url,
			};
		}

		return {
			ok: true,
			status: response.status,
			body: body.json as T,
		};
	}

	private fetchJson(
		method: "POST" | "PUT",
		url: string,
		authHeaders: BackendAuthHeaders,
		payload: Record<string, unknown>,
	): Promise<Response> {
		return fetch(url, {
			method,
			headers: {
				...authHeaders,
				"Content-Type": "application/json",
			},
			body: JSON.stringify(payload),
		});
	}

	private fetchGetJson(url: string, authHeaders: BackendAuthHeaders): Promise<Response> {
		return fetch(url, {
			method: "GET",
			headers: {
				...authHeaders,
			},
		});
	}
}
