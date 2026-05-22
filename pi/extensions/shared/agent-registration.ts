import { spawnSync } from "node:child_process";
import { logStructuredEvent } from "./structured-logging.js";

type AgentSessionResult = {
	ok: boolean;
	status: number | null;
	body: unknown;
	responseText?: string | null;
	url?: string | null;
	error: string | null;
	agentSessionUid: string | null;
	agentUid: string | null;
	agentType: string | null;
	agentUniqueId: string | null;
	threadId: string | null;
	startedAt: string | null;
};

export type BackendAgentSessionFetchResult = {
	ok: boolean;
	status: number | null;
	body: unknown;
	error: string | null;
	agentSessionUid: string | null;
	notFound: boolean;
	endpoint: string | null;
};

const DEFAULT_BACKEND = "https://api.main-sequence.app";

export type BackendAuthHeaders = Record<string, string>;

export function shouldRegisterAgents(env: NodeJS.ProcessEnv = process.env): boolean {
	return /^(1|true|yes|on)$/i.test(env.BUILD_AGENTS_IN_BACKEND ?? "");
}

function sanitizeId(value: string): string {
	return value.trim().replace(/\s+/g, "_");
}

function normalizeIdPart(value: unknown): string | null {
	if (typeof value === "number" && Number.isFinite(value)) {
		return sanitizeId(String(value));
	}
	if (typeof value === "string" && value.trim()) {
		return sanitizeId(value);
	}
	return null;
}

export function resolveBackendUrl(env: NodeJS.ProcessEnv): string {
	const raw =
		env.MAINSEQUENCE_BACKEND ||
		env.MAIN_SEQUENCE_BACKEND_URL ||
		env.MAINSEQUENCE_ENDPOINT ||
		env.TDAG_ENDPOINT ||
		DEFAULT_BACKEND;

	return raw.replace(/\/+$/, "");
}

export function resolveMainsequenceUserId(options: {
	userId?: unknown;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): string | null {
	const explicitUserId = normalizeIdPart(options.userId);
	if (explicitUserId) return explicitUserId;

	const env = options.env ?? process.env;
	const envUserId = normalizeIdPart(env.ASTRO_MAINSEQUENCE_USER_ID);
	if (envUserId) return envUserId;

	options.log?.(
		"Could not resolve Main Sequence user id from runtime credential auth alone; pass userId in the request or set ASTRO_MAINSEQUENCE_USER_ID.",
	);
	return null;
}

export function buildAgentUniqueId(options: {
	agentType: string;
	userId: string;
	projectId?: string | number | null;
}): string {
	if (options.agentType === "project-executor") {
		return "project-executor";
	}
	const projectId = normalizeIdPart(options.projectId);
	const safeAgentType = sanitizeId(options.agentType);
	return projectId ? `${safeAgentType}_${options.userId}_${projectId}` : `${safeAgentType}_${options.userId}`;
}

function buildMainsequenceSdkEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
	const backendUrl = resolveBackendUrl(env);
	return {
		...env,
		MAINSEQUENCE_ENDPOINT: env.MAINSEQUENCE_ENDPOINT ?? backendUrl,
		TDAG_ENDPOINT: env.TDAG_ENDPOINT ?? backendUrl,
	};
}

function normalizeBackendAuthHeaders(value: unknown): BackendAuthHeaders | null {
	if (!value || typeof value !== "object" || Array.isArray(value)) return null;
	const headers: BackendAuthHeaders = {};
	for (const [key, rawValue] of Object.entries(value)) {
		if (!key.trim() || typeof rawValue !== "string" || !rawValue.trim()) continue;
		headers[key] = rawValue;
	}
	return Object.keys(headers).length > 0 ? headers : null;
}

function resolveRuntimeCredentialAuthHeaders(
	env: NodeJS.ProcessEnv,
	log?: (message: string) => void,
): { headers: BackendAuthHeaders | null; error: string | null } {
	const script = [
		"import json",
		"from mainsequence.client.utils import get_authorization_headers",
		"print(json.dumps(dict(get_authorization_headers())))",
	].join("; ");
	const result = spawnSync("python3", ["-c", script], {
		stdio: ["ignore", "pipe", "pipe"],
		env: buildMainsequenceSdkEnv(env),
		encoding: "utf8",
	});

	if (result.error) {
		const message =
			(result.error as NodeJS.ErrnoException).code === "ENOENT"
				? "Missing required command: python3"
				: result.error.message;
		log?.(`Runtime credential auth header resolution failed: ${message}`);
		return { headers: null, error: message };
	}

	if (result.status !== 0) {
		const stderr = result.stderr.trim();
		const stdout = result.stdout.trim();
		const message =
			stderr || stdout || `mainsequence SDK auth header resolution failed with code ${result.status ?? 1}.`;
		log?.(`Runtime credential auth header resolution failed: ${message}`);
		return { headers: null, error: message };
	}

	try {
		const lastJsonLine = result.stdout
			.split(/\r?\n/)
			.map((line) => line.trim())
			.filter(Boolean)
			.at(-1);
		const headers = normalizeBackendAuthHeaders(JSON.parse(lastJsonLine ?? ""));
		if (!headers) {
			return {
				headers: null,
				error: "Main Sequence SDK did not return authorization headers for runtime credential auth.",
			};
		}
		return { headers, error: null };
	} catch (error) {
		const message = error instanceof Error ? error.message : "invalid JSON";
		return {
			headers: null,
			error: `Could not parse Main Sequence SDK auth headers: ${message}`,
		};
	}
}

export async function resolveBackendAuthHeaders(
	env: NodeJS.ProcessEnv,
	log?: (message: string) => void,
): Promise<{ headers: BackendAuthHeaders | null; error: string | null }> {
	return resolveRuntimeCredentialAuthHeaders(env, log);
}

async function postStartAgentSession(options: {
	backendUrl: string;
	authHeaders: BackendAuthHeaders;
	agentUid: string;
	payload: Record<string, unknown>;
}): Promise<Response> {
	return fetch(agentStartSessionEndpoint(options.backendUrl, options.agentUid), {
		method: "POST",
		headers: {
			...options.authHeaders,
			"Content-Type": "application/json",
		},
		body: JSON.stringify(options.payload),
	});
}

function agentStartSessionEndpoint(backendUrl: string, agentUid: string): string {
	return `${backendUrl}/orm/api/agents/v1/agents/${encodeURIComponent(agentUid)}/start_new_session/`;
}

async function getAgentSessionByEndpoint(options: {
	endpoint: string;
	authHeaders: BackendAuthHeaders;
}): Promise<Response> {
	return fetch(options.endpoint, {
		method: "GET",
		headers: {
			...options.authHeaders,
			"Content-Type": "application/json",
		},
	});
}

function normalizeBackendUid(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value.trim() : null;
}

function parseAgentUid(payload: any): string | null {
	return parseStringField(payload, "uid", "agent_uid", "agentUid");
}

function parseAgentSessionUid(payload: any): string | null {
	return parseStringField(payload, "uid", "agent_session_uid", "agentSessionUid");
}

function parseStringField(payload: any, ...keys: string[]): string | null {
	for (const key of keys) {
		const value = payload?.[key];
		if (typeof value === "string" && value.trim()) return value;
	}
	return null;
}

function parseObjectField(payload: any, ...keys: string[]): Record<string, unknown> | null {
	if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
	for (const key of keys) {
		const value = payload[key];
		if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
	}
	return null;
}

function parseBackendAgentType(agentPayload: any): string | null {
	return parseStringField(agentPayload, "agent_type", "agentType");
}

function parseAgentType(sessionPayload: any): string | null {
	const sessionMetadata = parseObjectField(sessionPayload, "session_metadata", "sessionMetadata");
	return (
		parseStringField(sessionMetadata, "agent_type", "agentType") ??
		parseStringField(sessionPayload, "agent_type", "agentType")
	);
}

function stringifyBackendErrorValue(value: unknown): string | null {
	if (typeof value === "string" && value.trim()) return value.trim();
	if (value === undefined || value === null) return null;
	try {
		return JSON.stringify(value);
	} catch {
		return String(value);
	}
}

function extractBackendErrorMessage(body: any, responseText: string, fallback: string): string {
	if (body && typeof body === "object" && !Array.isArray(body)) {
		for (const key of ["error_detail", "errorDetail", "detail", "message", "error"]) {
			const message = stringifyBackendErrorValue(body[key]);
			if (message) return message;
		}
	}
	return responseText || fallback;
}

function normalizeBackendLookupUid(value: unknown): string | null {
	return normalizeBackendUid(value);
}

export async function startBackendAgentSession(options: {
	agentUid: string;
	payload: Record<string, unknown>;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): Promise<AgentSessionResult> {
	const runtimeEnv = options.env ?? process.env;
	const backendUrl = resolveBackendUrl(runtimeEnv);
	const url = agentStartSessionEndpoint(backendUrl, options.agentUid);
	const authHeadersResult = await resolveBackendAuthHeaders(runtimeEnv, options.log);

	if (!authHeadersResult.headers) {
			return {
				ok: false,
				status: null,
				body: null,
				responseText: null,
				url,
				error: authHeadersResult.error ?? "Missing backend auth headers for agent session creation.",
				agentSessionUid: null,
				agentUid: null,
				agentType: null,
				agentUniqueId: null,
				threadId: null,
				startedAt: null,
			};
		}

	let response = await postStartAgentSession({
		backendUrl,
		authHeaders: authHeadersResult.headers,
		agentUid: options.agentUid,
		payload: options.payload,
	});

	if (response.status === 401 || response.status === 403) {
		const retryAuthHeaders = await resolveBackendAuthHeaders(runtimeEnv, options.log);
		if (retryAuthHeaders.headers) {
			response = await postStartAgentSession({
				backendUrl,
				authHeaders: retryAuthHeaders.headers,
				agentUid: options.agentUid,
				payload: options.payload,
			});
		}
	}

	const responseText = await response.text();
	let parsedBody: any = null;
	try {
		parsedBody = responseText ? JSON.parse(responseText) : null;
	} catch {
		parsedBody = null;
	}

	if (!response.ok) {
		const message = extractBackendErrorMessage(
			parsedBody,
			responseText,
			`Agent session start failed with status ${response.status}.`,
		);
		options.log?.(
			`Agent session start failed (${response.status}) backend=${backendUrl} agentUid=${options.agentUid}: ${message}`,
		);
		return {
			ok: false,
			status: response.status,
			body: parsedBody,
			responseText,
			url,
			error: String(message),
			agentSessionUid: null,
			agentUid: null,
			agentType: null,
			agentUniqueId: null,
			threadId: null,
			startedAt: null,
		};
	}

	const agentSessionUid = parseAgentSessionUid(parsedBody);
	if (agentSessionUid == null) {
		options.log?.("Agent session start succeeded but no `uid` was returned.");
		return {
			ok: false,
			status: response.status,
			body: parsedBody,
			responseText,
			url,
			error: "Agent session start did not return a `uid`.",
			agentSessionUid: null,
			agentUid: null,
			agentType: null,
			agentUniqueId: null,
			threadId: null,
			startedAt: null,
		};
	}

	const responseAgent = parsedBody && typeof parsedBody === "object" ? parsedBody.agent : null;
	return {
		ok: true,
		status: response.status,
		body: parsedBody,
		responseText,
		url,
		error: null,
		agentSessionUid,
		agentUid: parseAgentUid(responseAgent),
		agentType: parseAgentType(parsedBody) ?? parseBackendAgentType(responseAgent),
		agentUniqueId: parseStringField(responseAgent, "agent_unique_id", "agentUniqueId"),
		threadId: parseStringField(parsedBody, "thread_id", "threadId"),
		startedAt: parseStringField(parsedBody, "started_at", "startedAt"),
	};
}

export async function fetchBackendAgentSession(options: {
	agentSessionUid: string;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): Promise<BackendAgentSessionFetchResult> {
	const runtimeEnv = options.env ?? process.env;
	const backendUrl = resolveBackendUrl(runtimeEnv);
	const normalizedSessionUid = normalizeBackendLookupUid(options.agentSessionUid);

	if (normalizedSessionUid == null) {
		logStructuredEvent({
			severity: "WARNING",
			component: "agent-registration",
			event: "backend_session_fetch_invalid_uid",
			message: "Backend agent session fetch skipped because the session uid was invalid.",
			data: {
				agentSessionUid: options.agentSessionUid,
			},
		});
		return {
			ok: false,
			status: null,
			body: null,
			error: "Invalid backend agent session uid.",
			agentSessionUid: null,
			notFound: false,
			endpoint: null,
		};
	}

	const authHeadersResult = await resolveBackendAuthHeaders(runtimeEnv, options.log);
	if (!authHeadersResult.headers) {
		logStructuredEvent({
			severity: "ERROR",
			component: "agent-registration",
			event: "backend_session_fetch_missing_auth_headers",
			message: "Backend agent session fetch failed before the request because no backend auth headers were available.",
			data: {
				agentSessionUid: normalizedSessionUid,
				error: authHeadersResult.error ?? "missing backend auth headers",
			},
		});
		return {
			ok: false,
			status: null,
			body: null,
			error: authHeadersResult.error ?? "Missing backend auth headers for backend session fetch.",
			agentSessionUid: normalizedSessionUid,
			notFound: false,
			endpoint: null,
		};
	}

	const candidateEndpoints = [
		`${backendUrl}/orm/api/agents/v1/sessions/${encodeURIComponent(normalizedSessionUid)}/`,
		`${backendUrl}/orm/api/agents/v1/agent_sessions/${encodeURIComponent(normalizedSessionUid)}/`,
		`${backendUrl}/orm/api/agents/v1/agent-sessions/${encodeURIComponent(normalizedSessionUid)}/`,
	];

	let lastNon404Error: BackendAgentSessionFetchResult | null = null;

	for (const endpoint of candidateEndpoints) {
		logStructuredEvent({
			component: "agent-registration",
			event: "backend_session_fetch_attempt",
			message: "Trying backend agent session fetch.",
			data: {
				agentSessionUid: normalizedSessionUid,
				endpoint,
			},
		});
		let response = await getAgentSessionByEndpoint({
			endpoint,
			authHeaders: authHeadersResult.headers,
		});

		if (response.status === 401 || response.status === 403) {
			const retryAuthHeaders = await resolveBackendAuthHeaders(runtimeEnv, options.log);
			if (retryAuthHeaders.headers) {
				response = await getAgentSessionByEndpoint({
					endpoint,
					authHeaders: retryAuthHeaders.headers,
				});
			}
		}

		const responseText = await response.text();
		let parsedBody: any = null;
		try {
			parsedBody = responseText ? JSON.parse(responseText) : null;
		} catch {
			parsedBody = null;
		}

		if (response.status === 404) {
			logStructuredEvent({
				severity: "DEBUG",
				component: "agent-registration",
				event: "backend_session_fetch_endpoint_not_found",
				message: "Backend agent session was not found at this endpoint; trying the next candidate.",
				data: {
					agentSessionUid: normalizedSessionUid,
					endpoint,
				},
			});
			continue;
		}

		if (!response.ok) {
			const message = extractBackendErrorMessage(
				parsedBody,
				responseText,
				`Backend session fetch failed with status ${response.status}.`,
			);
			lastNon404Error = {
				ok: false,
				status: response.status,
				body: parsedBody,
				error: String(message),
				agentSessionUid: normalizedSessionUid,
				notFound: false,
				endpoint,
			};
			logStructuredEvent({
				severity: "ERROR",
				component: "agent-registration",
				event: "backend_session_fetch_rejected",
				message: "Backend agent session fetch was rejected.",
				data: {
					agentSessionUid: normalizedSessionUid,
					endpoint,
					status: response.status,
					error: message,
				},
			});
			break;
		}

		logStructuredEvent({
			component: "agent-registration",
			event: "backend_session_fetch_succeeded",
			message: "Backend agent session fetch succeeded.",
			data: {
				agentSessionUid: normalizedSessionUid,
				endpoint,
				status: response.status,
			},
		});
		return {
			ok: true,
			status: response.status,
			body: parsedBody,
			error: null,
			agentSessionUid: parseAgentSessionUid(parsedBody) ?? normalizedSessionUid,
			notFound: false,
			endpoint,
		};
	}

	if (lastNon404Error) {
		return lastNon404Error;
	}

	logStructuredEvent({
		severity: "WARNING",
		component: "agent-registration",
		event: "backend_session_fetch_not_found",
		message: "Backend agent session was not found on any known endpoint.",
		data: {
			agentSessionUid: normalizedSessionUid,
			candidateEndpoints,
		},
	});
	return {
		ok: false,
		status: 404,
		body: null,
		error: `Backend agent session ${normalizedSessionUid} was not found.`,
		agentSessionUid: normalizedSessionUid,
		notFound: true,
		endpoint: null,
	};
}
