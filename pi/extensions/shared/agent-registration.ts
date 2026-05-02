import { spawnSync } from "node:child_process";
import { logStructuredEvent } from "./structured-logging.js";

type AgentRole = "orchestrator" | "specialist";

type RegistrationResult = {
	ok: boolean;
	exitCode: number | null;
	stdout: string;
	stderr: string;
	body: unknown;
	responseText?: string | null;
	url?: string | null;
	agentId: number | null;
	agentUniqueId: string | null;
	userId: string | null;
};

type AgentSessionResult = {
	ok: boolean;
	status: number | null;
	body: unknown;
	responseText?: string | null;
	url?: string | null;
	error: string | null;
	agentSessionId: number | null;
	agentId: number | null;
	agentName: string | null;
	agentUniqueId: string | null;
	threadId: string | null;
	startedAt: string | null;
};

export type BackendAgentSessionFetchResult = {
	ok: boolean;
	status: number | null;
	body: unknown;
	error: string | null;
	agentSessionId: number | null;
	notFound: boolean;
	endpoint: string | null;
};

type RegistrationOptions = {
	agentName: string;
	agentRole: AgentRole;
	cwd: string;
	projectId?: unknown;
	userId?: unknown;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
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
	agentName: string;
	userId: string;
	projectId?: string | number | null;
}): string {
	const safeAgentName = sanitizeId(options.agentName);
	const projectId = normalizeIdPart(options.projectId);
	return projectId ? `${safeAgentName}_${options.userId}_${projectId}` : `${safeAgentName}_${options.userId}`;
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

async function postGetOrCreateAgent(options: {
	backendUrl: string;
	authHeaders: BackendAuthHeaders;
	payload: Record<string, unknown>;
}): Promise<Response> {
	return fetch(agentGetOrCreateEndpoint(options.backendUrl), {
		method: "POST",
		headers: {
			...options.authHeaders,
			"Content-Type": "application/json",
		},
		body: JSON.stringify(options.payload),
	});
}

async function postStartAgentSession(options: {
	backendUrl: string;
	authHeaders: BackendAuthHeaders;
	agentId: number;
	payload: Record<string, unknown>;
}): Promise<Response> {
	return fetch(agentStartSessionEndpoint(options.backendUrl, options.agentId), {
		method: "POST",
		headers: {
			...options.authHeaders,
			"Content-Type": "application/json",
		},
		body: JSON.stringify(options.payload),
	});
}

function agentGetOrCreateEndpoint(backendUrl: string): string {
	return `${backendUrl}/orm/api/agents/v1/agents/get_or_create/`;
}

function agentStartSessionEndpoint(backendUrl: string, agentId: number): string {
	return `${backendUrl}/orm/api/agents/v1/agents/${agentId}/start_new_session/`;
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

function parseAgentId(payload: any): number | null {
	if (typeof payload?.id === "number" && Number.isFinite(payload.id)) {
		return payload.id;
	}
	if (typeof payload?.id === "string" && payload.id.trim()) {
		const parsed = Number.parseInt(payload.id, 10);
		return Number.isFinite(parsed) ? parsed : null;
	}
	return null;
}

function parseAgentSessionId(payload: any): number | null {
	if (typeof payload?.id === "number" && Number.isFinite(payload.id)) {
		return payload.id;
	}
	if (typeof payload?.id === "string" && payload.id.trim()) {
		const parsed = Number.parseInt(payload.id, 10);
		return Number.isFinite(parsed) ? parsed : null;
	}
	return null;
}

function parseStringField(payload: any, ...keys: string[]): string | null {
	for (const key of keys) {
		const value = payload?.[key];
		if (typeof value === "string" && value.trim()) return value;
	}
	return null;
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

function normalizeAgentSessionLookupId(value: unknown): number | null {
	if (typeof value === "number" && Number.isFinite(value)) {
		return Math.trunc(value);
	}
	if (typeof value === "string" && /^\d+$/.test(value.trim())) {
		const parsed = Number.parseInt(value.trim(), 10);
		return Number.isFinite(parsed) ? parsed : null;
	}
	return null;
}

export async function registerMainsequenceAgent(
	options: RegistrationOptions,
): Promise<RegistrationResult> {
	const { agentName, agentRole, projectId, userId, env, log } = options;
	const runtimeEnv = env ?? process.env;
	const safeAgentName = sanitizeId(agentName);
	const resolvedUserId = resolveMainsequenceUserId({ userId, env: runtimeEnv, log });

	if (!resolvedUserId) {
		log?.(`Agent registration failed for "${safeAgentName}": missing user id.`);
		return {
			ok: false,
				exitCode: null,
				stdout: "",
				stderr: "Missing required user id for deterministic registration.",
				body: null,
				agentId: null,
				agentUniqueId: null,
				userId: null,
		};
	}

	const isProjectCoder =
		agentName === "mainsequence-project-coder" || agentName === "mainsequence-project-executor";
	const resolvedProjectId = normalizeIdPart(projectId ?? runtimeEnv.ASTRO_TARGET_PROJECT_ID);
	if (isProjectCoder && !resolvedProjectId) {
		log?.(`Agent registration failed for "${safeAgentName}": missing project id.`);
		return {
			ok: false,
				exitCode: null,
				stdout: "",
				stderr: "Missing required project id for deterministic registration.",
				body: null,
				agentId: null,
				agentUniqueId: null,
				userId: resolvedUserId,
		};
	}

	const agentUniqueId = buildAgentUniqueId({
		agentName: safeAgentName,
		userId: resolvedUserId,
		projectId: resolvedProjectId,
	});
	const backendUrl = resolveBackendUrl(runtimeEnv);
	const url = agentGetOrCreateEndpoint(backendUrl);

	const authHeadersResult = await resolveBackendAuthHeaders(runtimeEnv, log);
	if (!authHeadersResult.headers) {
		log?.(
			`Agent registration failed for "${safeAgentName}": ${
				authHeadersResult.error ?? "missing backend auth headers"
			}.`,
		);
		return {
			ok: false,
				exitCode: null,
				stdout: "",
				stderr: authHeadersResult.error ?? "Missing backend auth headers for agent get_or_create.",
				body: null,
				agentId: null,
				agentUniqueId,
				userId: resolvedUserId,
		};
	}

	const payload: Record<string, unknown> = {
		name: safeAgentName,
		agent_unique_id: agentUniqueId,
	};

	let response = await postGetOrCreateAgent({
		backendUrl,
		authHeaders: authHeadersResult.headers,
		payload,
	});

	if (response.status === 401 || response.status === 403) {
		const retryAuthHeaders = await resolveBackendAuthHeaders(runtimeEnv, log);
		if (retryAuthHeaders.headers) {
			response = await postGetOrCreateAgent({
				backendUrl,
				authHeaders: retryAuthHeaders.headers,
				payload,
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
			`Agent get_or_create failed with status ${response.status}.`,
		);
		log?.(
			`Agent registration failed (${response.status}) backend=${backendUrl} agent_unique_id=${agentUniqueId}: ${message}`,
		);
			return {
				ok: false,
				exitCode: response.status,
				stdout: "",
				stderr: String(message),
				body: parsedBody,
				responseText,
				url,
				agentId: null,
				agentUniqueId,
				userId: resolvedUserId,
		};
	}

	const agentId = parseAgentId(parsedBody);
	if (agentId == null) {
		log?.("Agent get_or_create succeeded but no `id` was returned.");
		return {
			ok: false,
				exitCode: response.status,
				stdout: responseText,
				stderr: "Agent get_or_create did not return an `id`.",
				body: parsedBody,
				responseText,
				url,
				agentId: null,
				agentUniqueId,
				userId: resolvedUserId,
		};
	}

	log?.(
		`Resolved deterministic ${agentRole} agent "${agentUniqueId}" to backend id "${agentId}".`,
	);
	return {
		ok: true,
			exitCode: response.status,
			stdout: responseText,
			stderr: "",
			body: parsedBody,
			responseText,
			url,
			agentId,
			agentUniqueId,
			userId: resolvedUserId,
	};
}

export async function startBackendAgentSession(options: {
	agentId: number;
	payload: Record<string, unknown>;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): Promise<AgentSessionResult> {
	const runtimeEnv = options.env ?? process.env;
	const backendUrl = resolveBackendUrl(runtimeEnv);
	const url = agentStartSessionEndpoint(backendUrl, options.agentId);
	const authHeadersResult = await resolveBackendAuthHeaders(runtimeEnv, options.log);

	if (!authHeadersResult.headers) {
			return {
				ok: false,
				status: null,
				body: null,
				responseText: null,
				url,
				error: authHeadersResult.error ?? "Missing backend auth headers for agent session creation.",
				agentSessionId: null,
				agentId: null,
				agentName: null,
				agentUniqueId: null,
				threadId: null,
				startedAt: null,
			};
		}

	let response = await postStartAgentSession({
		backendUrl,
		authHeaders: authHeadersResult.headers,
		agentId: options.agentId,
		payload: options.payload,
	});

	if (response.status === 401 || response.status === 403) {
		const retryAuthHeaders = await resolveBackendAuthHeaders(runtimeEnv, options.log);
		if (retryAuthHeaders.headers) {
			response = await postStartAgentSession({
				backendUrl,
				authHeaders: retryAuthHeaders.headers,
				agentId: options.agentId,
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
			`Agent session start failed (${response.status}) backend=${backendUrl} agentId=${options.agentId}: ${message}`,
		);
		return {
			ok: false,
			status: response.status,
			body: parsedBody,
			responseText,
			url,
			error: String(message),
			agentSessionId: null,
			agentId: null,
			agentName: null,
			agentUniqueId: null,
			threadId: null,
			startedAt: null,
		};
	}

	const agentSessionId = parseAgentSessionId(parsedBody);
	if (agentSessionId == null) {
		options.log?.("Agent session start succeeded but no `id` was returned.");
		return {
			ok: false,
			status: response.status,
			body: parsedBody,
			responseText,
			url,
			error: "Agent session start did not return an `id`.",
			agentSessionId: null,
			agentId: null,
			agentName: null,
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
		agentSessionId,
		agentId: parseAgentId(responseAgent),
		agentName: parseStringField(responseAgent, "name", "agent_name", "agentName"),
		agentUniqueId: parseStringField(responseAgent, "agent_unique_id", "agentUniqueId"),
		threadId: parseStringField(parsedBody, "thread_id", "threadId"),
		startedAt: parseStringField(parsedBody, "started_at", "startedAt"),
	};
}

export async function fetchBackendAgentSession(options: {
	agentSessionId: number | string;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): Promise<BackendAgentSessionFetchResult> {
	const runtimeEnv = options.env ?? process.env;
	const backendUrl = resolveBackendUrl(runtimeEnv);
	const normalizedSessionId = normalizeAgentSessionLookupId(options.agentSessionId);

	if (normalizedSessionId == null) {
		logStructuredEvent({
			severity: "WARNING",
			component: "agent-registration",
			event: "backend_session_fetch_invalid_id",
			message: "Backend agent session fetch skipped because the session id was invalid.",
			data: {
				agentSessionId: options.agentSessionId,
			},
		});
		return {
			ok: false,
			status: null,
			body: null,
			error: "Invalid backend agent session id.",
			agentSessionId: null,
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
				agentSessionId: normalizedSessionId,
				error: authHeadersResult.error ?? "missing backend auth headers",
			},
		});
		return {
			ok: false,
			status: null,
			body: null,
			error: authHeadersResult.error ?? "Missing backend auth headers for backend session fetch.",
			agentSessionId: normalizedSessionId,
			notFound: false,
			endpoint: null,
		};
	}

	const candidateEndpoints = [
		`${backendUrl}/orm/api/agents/v1/sessions/${normalizedSessionId}/`,
		`${backendUrl}/orm/api/agents/v1/agent_sessions/${normalizedSessionId}/`,
		`${backendUrl}/orm/api/agents/v1/agent-sessions/${normalizedSessionId}/`,
	];

	let lastNon404Error: BackendAgentSessionFetchResult | null = null;

	for (const endpoint of candidateEndpoints) {
		logStructuredEvent({
			component: "agent-registration",
			event: "backend_session_fetch_attempt",
			message: "Trying backend agent session fetch.",
			data: {
				agentSessionId: normalizedSessionId,
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
					agentSessionId: normalizedSessionId,
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
				agentSessionId: normalizedSessionId,
				notFound: false,
				endpoint,
			};
			logStructuredEvent({
				severity: "ERROR",
				component: "agent-registration",
				event: "backend_session_fetch_rejected",
				message: "Backend agent session fetch was rejected.",
				data: {
					agentSessionId: normalizedSessionId,
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
				agentSessionId: normalizedSessionId,
				endpoint,
				status: response.status,
			},
		});
		return {
			ok: true,
			status: response.status,
			body: parsedBody,
			error: null,
			agentSessionId: parseAgentSessionId(parsedBody) ?? normalizedSessionId,
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
			agentSessionId: normalizedSessionId,
			candidateEndpoints,
		},
	});
	return {
		ok: false,
		status: 404,
		body: null,
		error: `Backend agent session ${normalizedSessionId} was not found.`,
		agentSessionId: normalizedSessionId,
		notFound: true,
		endpoint: null,
	};
}
