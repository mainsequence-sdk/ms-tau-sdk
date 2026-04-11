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

export function resolveMainsequenceUserId(options: {
	userId?: unknown;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): string | null {
	const explicitUserId = normalizeIdPart(options.userId);
	if (explicitUserId) return explicitUserId;

	const env = options.env ?? process.env;
	const jwtPayload =
		decodeJwtPayload(env.MAINSEQUENCE_ACCESS_TOKEN) ?? decodeJwtPayload(env.MAINSEQUENCE_REFRESH_TOKEN);
	const jwtUserId =
		normalizeIdPart(jwtPayload?.user_id) ??
		normalizeIdPart(jwtPayload?.userId) ??
		normalizeIdPart(jwtPayload?.id) ??
		normalizeIdPart(jwtPayload?.sub);

	if (jwtUserId) return jwtUserId;

	options.log?.("Could not resolve Main Sequence user id from request context or tokens.");
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

async function refreshAccessToken(env: NodeJS.ProcessEnv): Promise<string | null> {
	const backendUrl = resolveBackendUrl(env);
	const refreshToken = env.MAINSEQUENCE_REFRESH_TOKEN?.trim();
	if (!refreshToken) return null;

	const response = await fetch(`${backendUrl}/auth/jwt-token/token/refresh/`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
		},
		body: JSON.stringify({ refresh: refreshToken }),
	});

	if (!response.ok) return null;

	const payload = await response.json();
	const accessToken = typeof payload?.access === "string" && payload.access.trim() ? payload.access.trim() : null;
	if (accessToken) {
		env.MAINSEQUENCE_ACCESS_TOKEN = accessToken;
	}
	return accessToken;
}

async function resolveAccessToken(env: NodeJS.ProcessEnv, log?: (message: string) => void): Promise<string | null> {
	let accessToken = await refreshAccessToken(env);
	if (!accessToken) {
		accessToken = env.MAINSEQUENCE_ACCESS_TOKEN?.trim() || null;
	}
	if (!accessToken) {
		log?.("Missing access token for backend requests.");
	}
	return accessToken;
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

export async function registerMainsequenceAgent(
	options: RegistrationOptions,
): Promise<RegistrationResult> {
	const { agentName, agentRole, projectId, userId, env, log } = options;
	const runtimeEnv = env ?? process.env;
	const safeAgentName = sanitizeId(agentName);
	const resolvedUserId = resolveMainsequenceUserId({ userId, env: runtimeEnv, log });

	if (!resolvedUserId) {
		log?.(`Skipping agent registration for "${safeAgentName}": missing user id.`);
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
		log?.(`Skipping agent registration for "${safeAgentName}": missing project id.`);
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

	const accessToken = await resolveAccessToken(runtimeEnv, log);
	if (!accessToken) {
		log?.(`Skipping agent registration for "${safeAgentName}": missing access token.`);
		return {
			ok: false,
			exitCode: null,
			stdout: "",
			stderr: "Missing access token for agent get_or_create.",
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
		accessToken,
		payload,
	});

	if (response.status === 401 || response.status === 403) {
		const refreshedAccessToken = await resolveAccessToken(runtimeEnv, log);
		if (refreshedAccessToken) {
			const nextAccessToken = refreshedAccessToken;
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
	const accessToken = await resolveAccessToken(runtimeEnv, options.log);

	if (!accessToken) {
		return {
			ok: false,
			status: null,
			body: null,
			error: "Missing access token for agent session creation.",
			agentSessionId: null,
		};
	}

	let response = await postStartAgentSession({
		backendUrl,
		accessToken,
		agentId: options.agentId,
		payload: options.payload,
	});

	if (response.status === 401 || response.status === 403) {
		const refreshedAccessToken = await resolveAccessToken(runtimeEnv, options.log);
		if (refreshedAccessToken) {
			response = await postStartAgentSession({
				backendUrl,
				accessToken: refreshedAccessToken,
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
