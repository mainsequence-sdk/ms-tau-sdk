import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { logStructuredEvent } from "./structured-logging.js";

type AgentRole = "orchestrator" | "specialist";

type RegistrationResult = {
	ok: boolean;
	exitCode: number | null;
	stdout: string;
	stderr: string;
	agentId: number | null;
	agentUniqueId: string | null;
	userId: string | null;
};

type AgentSessionResult = {
	ok: boolean;
	status: number | null;
	body: unknown;
	error: string | null;
	agentSessionId: number | null;
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

type MainsequenceCredentials = {
	accessToken: string | null;
	refreshToken: string | null;
};

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

function decodeBase64Url(value: string): string | null {
	try {
		const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
		const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
		return Buffer.from(padded, "base64").toString("utf8");
	} catch {
		return null;
	}
}

function decodeJwtPayload(token: string | undefined): Record<string, unknown> | null {
	if (!token) return null;
	const parts = token.split(".");
	if (parts.length !== 3) return null;
	const decoded = decodeBase64Url(parts[1]);
	if (!decoded) return null;

	try {
		const parsed = JSON.parse(decoded);
		return parsed && typeof parsed === "object" ? parsed : null;
	} catch {
		return null;
	}
}

function resolveBackendUrl(env: NodeJS.ProcessEnv): string {
	const raw =
		env.MAINSEQUENCE_BACKEND ||
		env.MAIN_SEQUENCE_BACKEND_URL ||
		env.MAINSEQUENCE_ENDPOINT ||
		env.TDAG_ENDPOINT ||
		DEFAULT_BACKEND;

	return raw.replace(/\/+$/, "");
}

function normalizeMainsequenceToken(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function resolveMainsequenceAuthStorePath(env: NodeJS.ProcessEnv): string {
	const configuredDir = env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim();
	if (configuredDir) {
		return path.join(path.resolve(configuredDir), "auth.json");
	}

	const homeDir = env.HOME?.trim() || homedir();
	return path.join(homeDir, ".config", "mainsequence", "auth.json");
}

function readPersistedMainsequenceAuth(env: NodeJS.ProcessEnv): MainsequenceCredentials {
	const authPath = resolveMainsequenceAuthStorePath(env);
	if (!existsSync(authPath)) {
		return {
			accessToken: null,
			refreshToken: null,
		};
	}

	try {
		const parsed = JSON.parse(readFileSync(authPath, "utf8")) as Record<string, unknown>;
		return {
			accessToken: normalizeMainsequenceToken(parsed.access),
			refreshToken: normalizeMainsequenceToken(parsed.refresh),
		};
	} catch {
		return {
			accessToken: null,
			refreshToken: null,
		};
	}
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

	const jwtPayload =
		decodeJwtPayload(env.MAINSEQUENCE_ACCESS_TOKEN) ?? decodeJwtPayload(env.MAINSEQUENCE_REFRESH_TOKEN);
	const jwtUserId =
		normalizeIdPart(jwtPayload?.user_id) ??
		normalizeIdPart(jwtPayload?.userId) ??
		normalizeIdPart(jwtPayload?.id) ??
		normalizeIdPart(jwtPayload?.sub);

	if (jwtUserId) return jwtUserId;

	options.log?.("Could not resolve Main Sequence user id from request context, env, or tokens.");
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

async function refreshAccessToken(env: NodeJS.ProcessEnv): Promise<{ token: string | null; error: string | null }> {
	const backendUrl = resolveBackendUrl(env);
	const persistedCredentials = readPersistedMainsequenceAuth(env);
	const refreshToken =
		normalizeMainsequenceToken(env.MAINSEQUENCE_REFRESH_TOKEN) ?? persistedCredentials.refreshToken;
	if (!refreshToken) {
		return { token: null, error: "Missing refresh token." };
	}

	const response = await fetch(`${backendUrl}/auth/jwt-token/token/refresh/`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
		},
		body: JSON.stringify({ refresh: refreshToken }),
	});

	if (!response.ok) {
		const body = await response.text();
		const message = body || `Refresh failed with status ${response.status}.`;
		return { token: null, error: message };
	}

	const payload = await response.json();
	const accessToken = typeof payload?.access === "string" && payload.access.trim() ? payload.access.trim() : null;
	const refreshedRefreshToken =
		typeof payload?.refresh === "string" && payload.refresh.trim() ? payload.refresh.trim() : null;
	if (accessToken) {
		env.MAINSEQUENCE_ACCESS_TOKEN = accessToken;
	}
	if (refreshedRefreshToken) {
		env.MAINSEQUENCE_REFRESH_TOKEN = refreshedRefreshToken;
	}
	return accessToken
		? { token: accessToken, error: null }
		: { token: null, error: "Refresh response did not include access token." };
}

async function resolveAccessToken(
	env: NodeJS.ProcessEnv,
	log?: (message: string) => void,
): Promise<{ token: string | null; error: string | null }> {
	const refreshResult = await refreshAccessToken(env);
	if (refreshResult.token) return refreshResult;

	log?.(`Token refresh failed: ${refreshResult.error ?? "unknown error"}`);
	return { token: null, error: refreshResult.error ?? "Token refresh failed." };
}

async function postGetOrCreateAgent(options: {
	backendUrl: string;
	accessToken: string;
	payload: Record<string, unknown>;
}): Promise<Response> {
	return fetch(`${options.backendUrl}/orm/api/agents/v1/agents/get_or_create/`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${options.accessToken}`,
			"Content-Type": "application/json",
		},
		body: JSON.stringify(options.payload),
	});
}

async function postStartAgentSession(options: {
	backendUrl: string;
	accessToken: string;
	agentId: number;
	payload: Record<string, unknown>;
}): Promise<Response> {
	return fetch(`${options.backendUrl}/orm/api/agents/v1/agents/${options.agentId}/start_new_session/`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${options.accessToken}`,
			"Content-Type": "application/json",
		},
		body: JSON.stringify(options.payload),
	});
}

async function getAgentSessionByEndpoint(options: {
	endpoint: string;
	accessToken: string;
}): Promise<Response> {
	return fetch(options.endpoint, {
		method: "GET",
		headers: {
			Authorization: `Bearer ${options.accessToken}`,
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
			agentId: null,
			agentUniqueId: null,
			userId: null,
		};
	}

	const isProjectCoder = agentName === "mainsequence-project-coder";
	const resolvedProjectId = normalizeIdPart(projectId ?? runtimeEnv.ASTRO_TARGET_PROJECT_ID);
	if (isProjectCoder && !resolvedProjectId) {
		log?.(`Agent registration failed for "${safeAgentName}": missing project id.`);
		return {
			ok: false,
			exitCode: null,
			stdout: "",
			stderr: "Missing required project id for deterministic registration.",
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

	const accessTokenResult = await resolveAccessToken(runtimeEnv, log);
	if (!accessTokenResult.token) {
		log?.(
			`Agent registration failed for "${safeAgentName}": ${
				accessTokenResult.error ?? "missing access token"
			}.`,
		);
		return {
			ok: false,
			exitCode: null,
			stdout: "",
			stderr: accessTokenResult.error ?? "Missing access token for agent get_or_create.",
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
		accessToken: accessTokenResult.token,
		payload,
	});

	if (response.status === 401 || response.status === 403) {
		const refreshedAccessToken = await resolveAccessToken(runtimeEnv, log);
		if (refreshedAccessToken.token) {
			const nextAccessToken = refreshedAccessToken.token;
			response = await postGetOrCreateAgent({
				backendUrl,
				accessToken: nextAccessToken,
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
		const message = parsedBody?.detail || responseText || `Agent get_or_create failed with status ${response.status}.`;
		log?.(
			`Agent registration failed (${response.status}) backend=${backendUrl} agent_unique_id=${agentUniqueId}: ${message}`,
		);
		return {
			ok: false,
			exitCode: response.status,
			stdout: "",
			stderr: String(message),
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
	const accessTokenResult = await resolveAccessToken(runtimeEnv, options.log);

	if (!accessTokenResult.token) {
		return {
			ok: false,
			status: null,
			body: null,
			error: accessTokenResult.error ?? "Missing access token for agent session creation.",
			agentSessionId: null,
		};
	}

	let response = await postStartAgentSession({
		backendUrl,
		accessToken: accessTokenResult.token,
		agentId: options.agentId,
		payload: options.payload,
	});

	if (response.status === 401 || response.status === 403) {
		const refreshedAccessToken = await resolveAccessToken(runtimeEnv, options.log);
		if (refreshedAccessToken.token) {
			response = await postStartAgentSession({
				backendUrl,
				accessToken: refreshedAccessToken.token,
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
		const message =
			parsedBody?.detail || responseText || `Agent session start failed with status ${response.status}.`;
		options.log?.(
			`Agent session start failed (${response.status}) backend=${backendUrl} agentId=${options.agentId}: ${message}`,
		);
		return {
			ok: false,
			status: response.status,
			body: parsedBody,
			error: String(message),
			agentSessionId: null,
		};
	}

	const agentSessionId = parseAgentSessionId(parsedBody);
	if (agentSessionId == null) {
		options.log?.("Agent session start succeeded but no `id` was returned.");
		return {
			ok: false,
			status: response.status,
			body: parsedBody,
			error: "Agent session start did not return an `id`.",
			agentSessionId: null,
		};
	}

	return {
		ok: true,
		status: response.status,
		body: parsedBody,
		error: null,
		agentSessionId,
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

	const accessTokenResult = await resolveAccessToken(runtimeEnv, options.log);
	if (!accessTokenResult.token) {
		logStructuredEvent({
			severity: "ERROR",
			component: "agent-registration",
			event: "backend_session_fetch_missing_access_token",
			message: "Backend agent session fetch failed before the request because no access token was available.",
			data: {
				agentSessionId: normalizedSessionId,
				error: accessTokenResult.error ?? "missing access token",
			},
		});
		return {
			ok: false,
			status: null,
			body: null,
			error: accessTokenResult.error ?? "Missing access token for backend session fetch.",
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
			accessToken: accessTokenResult.token,
		});

		if (response.status === 401 || response.status === 403) {
			const refreshedAccessToken = await resolveAccessToken(runtimeEnv, options.log);
			if (refreshedAccessToken.token) {
				response = await getAgentSessionByEndpoint({
					endpoint,
					accessToken: refreshedAccessToken.token,
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
			const message =
				parsedBody?.detail || responseText || `Backend session fetch failed with status ${response.status}.`;
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
