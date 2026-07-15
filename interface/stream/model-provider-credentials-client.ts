import {
	resolveBackendAuthHeaders,
	resolveBackendUrl,
	type BackendAuthHeaders,
} from "../../pi/extensions/shared/agent-registration.js";

export type PiCredential =
	| {
			type: "api_key";
			key: string;
	  }
	| ({
			type: "oauth";
	  } & Record<string, unknown>);

export type ProviderCredentialStatus = {
	status: "active" | "revoked";
	credential_kind: "api_key" | "oauth";
	version: number;
	credential_hash: string;
	last_flushed_at: string | null;
};

export type ProviderCredentialStatusResponse = {
	providers: Record<string, ProviderCredentialStatus>;
};

export type ProviderCredentialHydrateResponse = {
	credentials: Record<
		string,
		ProviderCredentialStatus & {
			pi_credential: PiCredential;
		}
	>;
	missing: string[];
	revoked: string[];
};

export type ProviderCredentialFlushResponse = {
	provider: string;
	status: "active" | "revoked";
	version: number;
	credential_hash: string;
	accepted: boolean;
};

export type ProviderCredentialRevokeResponse = {
	provider: string;
	status: "revoked";
	version: number;
	revoked_at: string | null;
};

export type ProviderCredentialClientResult<T> =
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

function extractError(body: { text: string; json: unknown }, fallback: string): string {
	if (body.json && typeof body.json === "object" && !Array.isArray(body.json)) {
		const detail =
			(body.json as { detail?: unknown }).detail ??
			(body.json as { error_detail?: unknown }).error_detail ??
			(body.json as { message?: unknown }).message ??
			(body.json as { error?: unknown }).error;
		if (detail) {
			if (typeof detail === "string") return detail;
			try {
				return JSON.stringify(detail);
			} catch {
				return String(detail);
			}
		}
	}
	return body.text || fallback;
}

function endpoint(backendUrl: string, suffix: string): string {
	return `${backendUrl}/orm/api/agents/v1/model_provider_credentials/${suffix}`;
}

export class ModelProviderCredentialClient {
	private readonly backendUrl: string;
	private readonly env: NodeJS.ProcessEnv;
	private readonly log?: (message: string) => void;

	constructor(options: { env?: NodeJS.ProcessEnv; log?: (message: string) => void } = {}) {
		this.env = options.env ?? process.env;
		this.backendUrl = resolveBackendUrl(this.env);
		this.log = options.log;
	}

	async status(input: {
		createdByUser: string;
	}): Promise<ProviderCredentialClientResult<ProviderCredentialStatusResponse>> {
		const url = new URL(endpoint(this.backendUrl, "status/"));
		url.searchParams.set("created_by_user_uid", input.createdByUser);
		return this.getJson(url.toString());
	}

	async hydrate(input: {
		createdByUser: string;
		agentSessionId: number | null;
		providers: string[];
		holderId: string;
	}): Promise<ProviderCredentialClientResult<ProviderCredentialHydrateResponse>> {
		return this.postJson(endpoint(this.backendUrl, "hydrate/"), {
			created_by_user_uid: input.createdByUser,
			agent_session_id: input.agentSessionId,
			providers: input.providers,
			holder_id: input.holderId,
		});
	}

	async flush(input: {
		createdByUser: string;
		agentSessionId: number | null;
		provider: string;
		baseVersion: number;
		reason: string;
		piCredential: PiCredential;
	}): Promise<ProviderCredentialClientResult<ProviderCredentialFlushResponse>> {
		return this.postJson(endpoint(this.backendUrl, "flush/"), {
			created_by_user_uid: input.createdByUser,
			agent_session_id: input.agentSessionId,
			provider: input.provider,
			base_version: input.baseVersion,
			reason: input.reason,
			pi_credential: input.piCredential,
		});
	}

	async revoke(input: {
		createdByUser: string;
		provider: string;
		reason: string;
	}): Promise<ProviderCredentialClientResult<ProviderCredentialRevokeResponse>> {
		return this.postJson(endpoint(this.backendUrl, "revoke/"), {
			created_by_user_uid: input.createdByUser,
			provider: input.provider,
			reason: input.reason,
		});
	}

	private async getJson<T>(url: string): Promise<ProviderCredentialClientResult<T>> {
		const authHeadersResult = await resolveBackendAuthHeaders(this.env, this.log);
		if (!authHeadersResult.headers) {
			return {
				ok: false,
				status: null,
				error: authHeadersResult.error ?? "Missing backend auth headers for provider credential request.",
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
			return {
				ok: false,
				status: response.status,
				error: extractError(body, `Provider credential request failed with status ${response.status}.`),
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
	): Promise<ProviderCredentialClientResult<T>> {
		const authHeadersResult = await resolveBackendAuthHeaders(this.env, this.log);
		if (!authHeadersResult.headers) {
			return {
				ok: false,
				status: null,
				error: authHeadersResult.error ?? "Missing backend auth headers for provider credential request.",
				body: null,
				responseText: null,
				url,
			};
		}

		let response = await this.fetchJson(url, authHeadersResult.headers, payload);
		if (response.status === 401 || response.status === 403) {
			const retryAuthHeaders = await resolveBackendAuthHeaders(this.env, this.log);
			if (retryAuthHeaders.headers) {
				response = await this.fetchJson(url, retryAuthHeaders.headers, payload);
			}
		}

		const body = await readResponseBody(response);
		if (!response.ok) {
			return {
				ok: false,
				status: response.status,
				error: extractError(body, `Provider credential request failed with status ${response.status}.`),
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
		url: string,
		authHeaders: BackendAuthHeaders,
		payload: Record<string, unknown>,
	): Promise<Response> {
		return fetch(url, {
			method: "POST",
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
