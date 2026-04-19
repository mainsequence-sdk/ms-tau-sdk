import { createServer } from "node:http";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { tmpdir } from "node:os";
import {
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	rmSync,
	statSync,
	writeFileSync,
} from "node:fs";
import { randomUUID } from "node:crypto";
import { attachAgentId, serializeSse, type StreamEvent } from "./protocol.js";
import {
	createConversationStore,
	readConversationHistorySync,
	type ConversationStore,
} from "./conversation-store.js";
import {
	collectAvailableModels,
	collectModelCatalog,
	DEFAULT_OPENAI_MODEL,
	DEFAULT_OPENAI_PROVIDER,
	type RunConfigReasoningEffort,
} from "./available-models.js";
import {
	buildPiModelArgument,
	buildSessionModelEnv,
	normalizeSessionModelBinding,
	resolveSessionModelBinding,
	type SessionModelBinding,
} from "./session-model.js";
import { readSessionInsights } from "./session-insights.js";
import {
	ensureSessionScopedPiAgentDir,
	normalizeSessionConfigOverrides,
	resolveSessionRuntimeLimits,
	validateSessionConfigPatch,
	type SessionConfigOverrides,
} from "./session-config.js";
import { readStorageUsage } from "./storage-usage.js";
import {
	getModelProviderAuthResponse,
	isProviderUsableForExecution,
	signOffModelProvider,
} from "./model-provider-auth.js";
import {
	cancelModelProviderSignInAttempt,
	getModelProviderSignInAttempt,
	startModelProviderSignIn,
	submitModelProviderSignInManualInput,
} from "./model-provider-signin.js";
import { discoverAgents, type AgentConfig } from "../../pi/extensions/tools/specialist-delegate/agents.js";
import {
	buildAgentUniqueId,
	fetchBackendAgentSession,
	registerMainsequenceAgent,
	resolveMainsequenceUserId,
	startBackendAgentSession,
	shouldRegisterAgents,
} from "../../pi/extensions/shared/agent-registration.js";
import { logStructuredEvent } from "../../pi/extensions/shared/structured-logging.js";
import {
	buildMainsequenceStoredAuthEnv,
	bootstrapMainsequenceCliAuth,
	loadEnvFile,
	startMainsequenceRefreshLoop,
} from "../../scripts/mainsequence_runtime_auth.js";
import {
	bootstrapProjectCoderRuntime,
	buildProjectScopedCheckoutEnv,
	buildActivatedProjectEnv,
	formatProjectRuntimeSummary,
	type ProjectRuntimeBootstrapEvent,
	type ProjectRuntimeBootstrapResult,
	type ProjectRuntimeSnapshot,
} from "../../pi/extensions/shared/project-runtime.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const ALLOWED_AGENTS = new Set(["astro-orchestrator", "mainsequence-project-coder"]);
const PI_BUILT_IN_TOOL_NAMES = new Set(["read", "bash", "edit", "write", "grep", "find", "ls"]);

loadEnvFile(repoRoot);
try {
	await bootstrapMainsequenceCliAuth({
		env: process.env,
		log: (message) => console.log(`[astro] ${message}`),
	});
	startMainsequenceRefreshLoop({
		env: process.env,
		log: (message) => console.error(`[astro] ${message}`),
	});
} catch (error) {
	const message = error instanceof Error ? error.message : "Unknown Main Sequence auth bootstrap failure.";
	console.error(`[astro] ${message}`);
	process.exit(1);
}

const host = process.env.ASTRO_STREAM_HOST ?? "0.0.0.0";
const port = Number(process.env.ASTRO_STREAM_PORT ?? "8787");
const logTraffic = process.env.ASTRO_STREAM_LOG_TRAFFIC !== "0";
const logRequestBodies = process.env.ASTRO_STREAM_LOG_REQUEST_BODIES === "1";
const sessionDir =
	process.env.ASTRO_STREAM_SESSION_DIR ?? path.join(repoRoot, ".astro", "stream-sessions");

type RequestContext = {
	res: import("node:http").ServerResponse;
	messageId: string;
	threadId: string;
	sessionKey: string;
	agentId: number | null;
	agentUniqueId: string | null;
	agentSessionId: number | null;
	agentName: string;
	userId: string;
	conversationStore: ConversationStore;
	logState: RequestLogState;
	eventId: number;
	textCounter: number;
	reasoningCounter: number;
	runtimeToolCounter: number;
	toolCallIds: Map<number, { toolCallId: string; toolName: string }>;
	switching: boolean;
	piProcess: ChildProcess | null;
	finished: boolean;
	system: string | undefined;
	uiContext: Record<string, unknown>;
	uiTools: Record<string, unknown>;
	sessionModelBinding: SessionModelBinding | null;
	sessionConfigOverrides: SessionConfigOverrides | null;
	responseProvider: string | null;
	responseModel: string | null;
	piAssistantTextSeen: boolean;
	lastAssistantFinishReason: string | null;
	lastAssistantUsage: { inputTokens?: number; outputTokens?: number } | undefined;
};

type RequestLogState = {
	reasoning: { id: string; text: string } | null;
	text: { id: string; text: string } | null;
	toolCalls: Map<string, { toolName: string; args: string }>;
};

type SessionMetadata = {
	agentId: number | null;
	agentUniqueId: string | null;
	agentSessionId: number | null;
	threadId: string | null;
	startedAt: string | null;
	agentName: string | null;
	projectId: string | null;
	cwd: string | null;
	repoRoot: string | null;
	pendingOnboarding: boolean;
	pendingRuntimeBootstrap: boolean;
	switchSummary: string | null;
	projectRuntime: ProjectRuntimeSnapshot | null;
	sessionModelBinding: SessionModelBinding | null;
	sessionConfigOverrides: SessionConfigOverrides | null;
};

type DiffFileStatus =
	| "added"
	| "copied"
	| "deleted"
	| "modified"
	| "renamed"
	| "typechange"
	| "unmerged"
	| "unknown"
	| "untracked";

type DiffFileSummary = {
	path: string;
	originalPath: string | null;
	status: DiffFileStatus;
	indexStatus: string | null;
	worktreeStatus: string | null;
	staged: boolean;
	unstaged: boolean;
	untracked: boolean;
};

type SessionAvailableTool = {
	url: string;
};

type SessionAvailableTools = Record<string, SessionAvailableTool>;

type ThreadSessionBinding = {
	threadId: string;
	runtimeSessionId: string;
	updatedAt: string | null;
};

type HydratedBackendOrchestratorSession = {
	agentId: number;
	agentUniqueId: string;
	metadata: SessionMetadata;
};

type SessionSwitchRequest = {
	kind: "project_session_switch";
	agentName: string;
	projectId: string;
	cwd: string;
	initialTask: string | null;
	summary: string | null;
};

function isProjectRuntimeBootstrapFailure(
	result: ProjectRuntimeBootstrapResult,
): result is Extract<ProjectRuntimeBootstrapResult, { ok: false }> {
	return result.ok === false;
}

function normalizeCorsOrigin(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	if (!trimmed || trimmed === "null") return null;
	try {
		return new URL(trimmed).origin;
	} catch {
		return null;
	}
}

function resolveTrustedCorsOrigins(env: NodeJS.ProcessEnv = process.env): Set<string> {
	const configured = new Set<string>();
	const rawTrustedOrigins = env.ASTRO_STREAM_TRUSTED_ORIGINS ?? "";

	for (const entry of rawTrustedOrigins.split(",")) {
		const normalized = normalizeCorsOrigin(entry);
		if (normalized) configured.add(normalized);
	}

	const deprecatedOrigin = normalizeCorsOrigin(env.ASTRO_STREAM_CORS_ORIGIN);
	if (deprecatedOrigin) configured.add(deprecatedOrigin);

	return configured;
}

function normalizePossiblyEncodedChatUrl(rawUrl: string | undefined): string {
	const input = rawUrl ?? "/";
	const encodedQuestionMarkIndex = input.search(/%3[fF]/);
	if (encodedQuestionMarkIndex === -1) return input;

	const pathPart = input.slice(0, encodedQuestionMarkIndex);
	if (!pathPart.startsWith("/api/chat/")) return input;

	const encodedQuery = input.slice(encodedQuestionMarkIndex + 3);
	let decodedQuery = encodedQuery;
	try {
		decodedQuery = decodeURIComponent(encodedQuery);
	} catch {
		decodedQuery = encodedQuery;
	}

	return `${pathPart}?${decodedQuery}`;
}

function appendVaryHeader(res: import("node:http").ServerResponse, field: string) {
	const existing = res.getHeader("Vary");
	if (typeof existing === "string" && existing.trim()) {
		const values = existing
			.split(",")
			.map((value) => value.trim())
			.filter(Boolean);
		if (!values.includes(field)) {
			values.push(field);
			res.setHeader("Vary", values.join(", "));
		}
		return;
	}
	if (Array.isArray(existing) && existing.length > 0) {
		const values = existing
			.flatMap((value) => String(value).split(","))
			.map((value) => value.trim())
			.filter(Boolean);
		if (!values.includes(field)) values.push(field);
		res.setHeader("Vary", values.join(", "));
		return;
	}
	res.setHeader("Vary", field);
}

const trustedCorsOrigins = resolveTrustedCorsOrigins(process.env);

function applyCorsHeaders(
	req: import("node:http").IncomingMessage,
	res: import("node:http").ServerResponse,
	options?: { preflight?: boolean },
): boolean {
	const requestOrigin = normalizeCorsOrigin(req.headers.origin);
	if (!requestOrigin) return true;

	appendVaryHeader(res, "Origin");
	if (!trustedCorsOrigins.has(requestOrigin)) {
		return false;
	}

	res.setHeader("Access-Control-Allow-Origin", requestOrigin);
	res.setHeader("Access-Control-Allow-Credentials", "true");

	if (options?.preflight) {
		appendVaryHeader(res, "Access-Control-Request-Headers");
		res.setHeader("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS");
		const requestedHeaders = normalizeLogString(req.headers["access-control-request-headers"]);
		res.setHeader("Access-Control-Allow-Headers", requestedHeaders ?? "Content-Type, Authorization, Last-Event-ID");
	}

	return true;
}

function writeCorsOriginNotAllowed(
	req: import("node:http").IncomingMessage,
	res: import("node:http").ServerResponse,
	url: URL,
) {
	const requestOrigin = normalizeCorsOrigin(req.headers.origin);
	const allowedOrigins = Array.from(trustedCorsOrigins).sort();
	const path = getRequestPathForLog(url);

	console.error(
		`[astro-cors] Rejected origin=${requestOrigin ?? "unknown"} method=${req.method ?? "UNKNOWN"} path=${path} trusted_origins=${allowedOrigins.length ? allowedOrigins.join(",") : "(none configured)"}`,
	);

	json(res, 403, {
		error: "cors_origin_not_allowed",
		message: "The request origin is not allowed by ASTRO_STREAM_TRUSTED_ORIGINS.",
		origin: requestOrigin,
		trustedOrigins: allowedOrigins,
	});
}

function json(res: import("node:http").ServerResponse, statusCode: number, body: unknown) {
	res.writeHead(statusCode, {
		"Content-Type": "application/json",
	});
	res.end(JSON.stringify(body));
}

function getRequestPathForLog(url: URL): string {
	const search = url.search || "";
	return `${url.pathname}${search}`;
}

function getRequestRemoteAddress(req: import("node:http").IncomingMessage): string | null {
	const forwardedFor = req.headers["x-forwarded-for"];
	if (typeof forwardedFor === "string") {
		const first = forwardedFor
			.split(",")
			.map((value) => value.trim())
			.find(Boolean);
		if (first) return first;
	}
	return req.socket.remoteAddress?.trim() || null;
}

function formatHttpAccessLogTimestamp(date: Date): string {
	const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
	const pad2 = (value: number) => String(value).padStart(2, "0");
	const offsetMinutes = -date.getTimezoneOffset();
	const sign = offsetMinutes >= 0 ? "+" : "-";
	const absoluteOffsetMinutes = Math.abs(offsetMinutes);
	const offsetHours = Math.floor(absoluteOffsetMinutes / 60);
	const offsetRemainderMinutes = absoluteOffsetMinutes % 60;
	return `${pad2(date.getDate())}/${months[date.getMonth()]}/${date.getFullYear()}:${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())} ${sign}${pad2(offsetHours)}${pad2(offsetRemainderMinutes)}`;
}

function formatHttpAccessLogField(value: string | null): string {
	if (!value) return "-";
	return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function getResponseSizeForLog(res: import("node:http").ServerResponse): string {
	const contentLength = res.getHeader("content-length");
	if (typeof contentLength === "number" && Number.isFinite(contentLength)) return String(contentLength);
	if (typeof contentLength === "string" && contentLength.trim()) return contentLength.trim();
	return "-";
}

function registerHttpAccessLog(
	req: import("node:http").IncomingMessage,
	res: import("node:http").ServerResponse,
	url: URL,
) {
	if (!logTraffic) return;

	const startedAt = process.hrtime.bigint();
	const remoteAddress = getRequestRemoteAddress(req) ?? "-";
	let finalized = false;

	const finalize = (state: "finish" | "close") => {
		if (finalized) return;
		finalized = true;
		const durationMs = Number(process.hrtime.bigint() - startedAt) / 1_000_000;
		const method = req.method ?? "UNKNOWN";
		const path = getRequestPathForLog(url);
		const protocol = req.httpVersion ? `HTTP/${req.httpVersion}` : "HTTP/1.1";
		const statusCode = res.statusCode || 0;
		const responseSize = getResponseSizeForLog(res);
		const referer = formatHttpAccessLogField(
			normalizeLogString(req.headers.referer) ?? normalizeLogString(req.headers.referrer),
		);
		const userAgent = formatHttpAccessLogField(normalizeLogString(req.headers["user-agent"]));
		const suffix = state === "close" && !res.writableEnded ? " aborted=true" : "";
		console.log(
			`[astro-http] ${remoteAddress} - - [${formatHttpAccessLogTimestamp(new Date())}] "${method} ${path} ${protocol}" ${statusCode} ${responseSize} "${referer}" "${userAgent}" rt=${durationMs.toFixed(1)}ms${suffix}`,
		);
	};

	res.once("finish", () => finalize("finish"));
	res.once("close", () => finalize("close"));
}

function notFound(res: import("node:http").ServerResponse) {
	json(res, 404, { error: "not_found" });
}

function badRequest(res: import("node:http").ServerResponse, message: string) {
	json(res, 400, { error: "bad_request", message });
}

function matchModelProviderAuthActionPath(pathname: string):
	| { provider: string; action: "signin" | "signoff" }
	| null {
	const matched = pathname.match(/^\/api\/model-providers\/([^/]+)\/(signin|signoff)$/);
	if (!matched) return null;
	const provider = normalizeLogString(decodeURIComponent(matched[1]))?.trim();
	const action = matched[2] === "signin" ? "signin" : "signoff";
	if (!provider) return null;
	return { provider, action };
}

function matchModelProviderSignInAttemptPath(pathname: string):
	| { provider: string; attemptId: string; action: "status" | "manual" | "cancel" }
	| null {
	const matched = pathname.match(
		/^\/api\/model-providers\/([^/]+)\/signin\/([^/]+)(?:\/(manual|cancel))?$/,
	);
	if (!matched) return null;
	const provider = normalizeLogString(decodeURIComponent(matched[1]))?.trim();
	const attemptId = normalizeLogString(decodeURIComponent(matched[2]))?.trim();
	const action = matched[3] === "manual" ? "manual" : matched[3] === "cancel" ? "cancel" : "status";
	if (!provider || !attemptId) return null;
	return { provider, attemptId, action };
}

function buildRuntimeConfigSnapshot(sessionModelBinding: SessionModelBinding | null): {
	temperature: number;
	top_p: number;
	max_output_tokens: number;
	reasoning_effort: RunConfigReasoningEffort;
} {
	return {
		temperature: 0.35,
		top_p: 0.9,
		max_output_tokens: 4000,
		reasoning_effort: sessionModelBinding?.piThinkingLevel ?? "medium",
	};
}

function resolveBackendLlmProvider(sessionModelBinding: SessionModelBinding | null): string {
	return sessionModelBinding?.provider ?? DEFAULT_OPENAI_PROVIDER;
}

function resolveBackendLlmModel(sessionModelBinding: SessionModelBinding | null): string {
	return sessionModelBinding?.model ?? DEFAULT_OPENAI_MODEL;
}

async function ensureRequestCliAuth(
	res: import("node:http").ServerResponse,
): Promise<{ ok: true } | { ok: false }> {
	try {
		await bootstrapMainsequenceCliAuth({
			env: process.env,
			log: (message) => console.log(`[astro] ${message}`),
		});
		return { ok: true };
	} catch (error) {
		const message =
			error instanceof Error ? error.message : "Unknown Main Sequence CLI auth bootstrap failure.";
		json(res, 503, {
			error: "runtime_auth_unavailable",
			message: `Deterministic Main Sequence CLI login failed before the session started: ${message}`,
		});
		return { ok: false };
	}
}

function logAgentResolutionFailure(details: {
	threadId: string;
	newChat: boolean;
	error: string | null;
	context: Record<string, unknown>;
}) {
	console.log(
		`[astro-stream] agent registration failed threadId=${details.threadId} newChat=${details.newChat} userId=${String(
			details.context.userId ?? "",
		)} error=${details.error ?? "unknown"}`,
	);
}

function parseJson(req: import("node:http").IncomingMessage): Promise<any> {
	return new Promise((resolve, reject) => {
		let data = "";
		req.on("data", (chunk) => {
			data += chunk;
		});
		req.on("end", () => {
			if (!data.trim()) {
				resolve({});
				return;
			}
			try {
				resolve(JSON.parse(data));
			} catch (error) {
				reject(error);
			}
		});
	});
}

function sanitizeSessionKey(value: string): string {
	return value.trim().replace(/[^a-zA-Z0-9._-]+/g, "_");
}

function normalizeAgentName(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function normalizeRuntimeSessionId(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? sanitizeSessionKey(trimmed) : null;
}

function shouldUseMockResponse(latestUserMessage: string): boolean {
	return /\bMOCK\b/i.test(latestUserMessage);
}

function resolveUserId(value: unknown): string | null {
	return resolveMainsequenceUserId({ userId: value, env: process.env });
}

function normalizeProjectId(value: unknown): string | null {
	if (typeof value === "number" && Number.isFinite(value)) {
		return String(value);
	}
	if (typeof value === "string" && value.trim()) {
		return value.trim();
	}
	return null;
}

function normalizeProjectCwd(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? path.resolve(trimmed) : null;
}

function normalizeRepoRoot(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? path.resolve(trimmed) : null;
}

function normalizeNumericId(value: unknown): number | null {
	if (typeof value === "number" && Number.isFinite(value)) {
		return Math.trunc(value);
	}
	if (typeof value === "string" && /^\d+$/.test(value.trim())) {
		const parsed = Number.parseInt(value.trim(), 10);
		return Number.isFinite(parsed) ? parsed : null;
	}
	return null;
}

function extractObjectPropertyRecord(
	record: Record<string, unknown>,
	...keys: string[]
): Record<string, unknown> | null {
	for (const key of keys) {
		const value = record[key];
		if (isPlainObject(value)) return value;
	}
	return null;
}

function extractStringProperty(record: Record<string, unknown>, ...keys: string[]): string | null {
	for (const key of keys) {
		const value = record[key];
		if (typeof value === "string" && value.trim()) {
			return value.trim();
		}
	}
	return null;
}

function extractNumericProperty(record: Record<string, unknown>, ...keys: string[]): number | null {
	for (const key of keys) {
		const value = normalizeNumericId(record[key]);
		if (value != null) return value;
	}
	return null;
}

function extractBackendSessionAgentId(payload: Record<string, unknown>): number | null {
	return (
		extractNumericProperty(payload, "agent", "agent_id", "agentId") ??
		extractNumericProperty(payload, "agent_id", "agentId") ??
		(() => {
			const agentRecord = extractObjectPropertyRecord(payload, "agent");
			if (!agentRecord) return null;
			return extractNumericProperty(agentRecord, "id", "agent_id", "agentId");
		})()
	);
}

function extractBackendSessionWorkflowKey(
	payload: Record<string, unknown>,
	sessionMetadata: Record<string, unknown> | null,
): string | null {
	return (
		extractStringProperty(
			sessionMetadata ?? {},
			"workflow_key",
			"workflowKey",
			"agent_name",
			"agentName",
		) ??
		extractStringProperty(
			payload,
			"workflow_key",
			"workflowKey",
			"agent_name",
			"agentName",
		) ??
		(() => {
			const agentRecord = extractObjectPropertyRecord(payload, "agent");
			if (!agentRecord) return null;
			return extractStringProperty(agentRecord, "name", "agent_name", "agentName");
		})()
	);
}

function extractBackendSessionThreadId(
	payload: Record<string, unknown>,
	sessionMetadata: Record<string, unknown> | null,
	requestedThreadId: string | null,
): string | null {
	return (
		extractStringProperty(payload, "thread_id", "threadId") ??
		extractStringProperty(sessionMetadata ?? {}, "thread_id", "threadId") ??
		requestedThreadId
	);
}

async function attachHydratedBackendSession(options: {
	runtimeSessionId: string;
	userId: string;
	requestedThreadId: string | null;
	log?: (message: string) => void;
}): Promise<
	| { ok: true; hydrated: HydratedBackendOrchestratorSession }
	| { ok: false; error: string; message: string; statusCode: number }
> {
	const normalizedAgentSessionId = normalizeNumericId(options.runtimeSessionId);
	logStructuredEvent({
		component: "astro-stream",
		event: "backend_session_hydration_attempt",
		message: "Attempting backend session hydration for the orchestrator.",
		data: {
			runtimeSessionId: options.runtimeSessionId,
			normalizedAgentSessionId,
			requestedThreadId: options.requestedThreadId,
			userId: options.userId,
		},
	});
	if (normalizedAgentSessionId == null) {
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "backend_session_hydration_invalid_id",
			message: "Backend session hydration was aborted because runtime_session_id was not numeric.",
			data: {
				runtimeSessionId: options.runtimeSessionId,
			},
		});
		return {
			ok: false,
			error: "session_not_found",
			message: "No local session found for the provided runtime_session_id.",
			statusCode: 409,
		};
	}

	const fetched = await fetchBackendAgentSession({
		agentSessionId: normalizedAgentSessionId,
		env: process.env,
		log: options.log,
	});

	if (!fetched.ok) {
		if (fetched.notFound) {
			logStructuredEvent({
				severity: "WARNING",
				component: "astro-stream",
				event: "backend_session_hydration_not_found",
				message: "Backend session hydration failed because the backend session does not exist.",
				data: {
					agentSessionId: normalizedAgentSessionId,
				},
			});
			return {
				ok: false,
				error: "session_not_found",
				message: "No local session found for the provided runtime_session_id.",
				statusCode: 409,
			};
		}
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-stream",
			event: "backend_session_hydration_fetch_failed",
			message: "Backend session hydration failed during backend session fetch.",
			data: {
				agentSessionId: normalizedAgentSessionId,
				error: fetched.error ?? "unknown fetch error",
				status: fetched.status,
				endpoint: fetched.endpoint,
			},
		});
		return {
			ok: false,
			error: "session_hydration_failed",
			message: fetched.error ?? "Failed to hydrate the backend-owned orchestrator session.",
			statusCode: fetched.status && fetched.status >= 500 ? 502 : 409,
		};
	}

	if (!isPlainObject(fetched.body)) {
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-stream",
			event: "backend_session_hydration_invalid_payload",
			message: "Backend session hydration failed because the backend response body was not an object.",
			data: {
				agentSessionId: normalizedAgentSessionId,
			},
		});
		return {
			ok: false,
			error: "session_hydration_failed",
			message: "The backend session response was not an object.",
			statusCode: 409,
		};
	}

	const sessionPayload = fetched.body;
	const sessionMetadata = extractObjectPropertyRecord(sessionPayload, "session_metadata", "sessionMetadata");
	const workflowKey = extractBackendSessionWorkflowKey(sessionPayload, sessionMetadata);
	if (workflowKey !== "astro-orchestrator") {
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "backend_session_hydration_wrong_workflow",
			message: "Backend session hydration was rejected because the session is not an orchestrator session.",
			data: {
				agentSessionId: normalizedAgentSessionId,
				workflowKey,
				agentName: extractStringProperty(sessionPayload, "agent_name", "agentName"),
				sessionMetadataWorkflowKey: extractStringProperty(
					sessionMetadata ?? {},
					"workflow_key",
					"workflowKey",
					"agent_name",
					"agentName",
				),
			},
		});
		return {
			ok: false,
			error: "session_hydration_failed",
			message: "The backend session is not an astro-orchestrator session.",
			statusCode: 409,
		};
	}

	const agentId = extractBackendSessionAgentId(sessionPayload);
	if (agentId == null) {
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-stream",
			event: "backend_session_hydration_missing_agent_id",
			message: "Backend session hydration failed because the backend payload had no usable agent id.",
			data: {
				agentSessionId: normalizedAgentSessionId,
			},
		});
		return {
			ok: false,
			error: "session_hydration_failed",
			message: "The backend session did not include a valid agent id.",
			statusCode: 409,
		};
	}

	const threadId = extractBackendSessionThreadId(sessionPayload, sessionMetadata, options.requestedThreadId);
	if (!threadId) {
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-stream",
			event: "backend_session_hydration_missing_thread_id",
			message: "Backend session hydration failed because no thread id was available from the backend payload or request.",
			data: {
				agentSessionId: normalizedAgentSessionId,
				requestedThreadId: options.requestedThreadId,
			},
		});
		return {
			ok: false,
			error: "session_hydration_failed",
			message: "The backend session did not include a usable thread id.",
			statusCode: 409,
		};
	}

	const createdByUser =
		extractStringProperty(sessionPayload, "created_by_user", "createdByUser") ??
		(() => {
			const userId = extractNumericProperty(sessionPayload, "created_by_user", "createdByUser");
			return userId != null ? String(userId) : null;
		})() ??
		extractStringProperty(sessionMetadata ?? {}, "created_by_user", "createdByUser") ??
		(() => {
			const userId = extractNumericProperty(sessionMetadata ?? {}, "created_by_user", "createdByUser");
			return userId != null ? String(userId) : null;
		})();
	if (createdByUser && createdByUser !== options.userId) {
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "backend_session_hydration_wrong_user",
			message: "Backend session hydration was rejected because the backend session belongs to a different user.",
			data: {
				agentSessionId: normalizedAgentSessionId,
				createdByUser,
				requestUserId: options.userId,
			},
		});
		return {
			ok: false,
			error: "session_hydration_failed",
			message: "The backend session belongs to a different user.",
			statusCode: 409,
		};
	}

	const sessionModelBinding = normalizeSessionModelBinding(
		sessionMetadata?.session_model_binding ?? sessionMetadata?.sessionModelBinding,
	);
	const startedAt =
		extractStringProperty(sessionPayload, "started_at", "startedAt") ??
		extractStringProperty(sessionMetadata ?? {}, "started_at", "startedAt");
	const agentUniqueId = buildAgentUniqueId({
		agentName: "astro-orchestrator",
		userId: options.userId,
	});
	logStructuredEvent({
		component: "astro-stream",
		event: "backend_session_hydration_succeeded",
		message: "Backend orchestrator session hydration succeeded.",
		data: {
			agentSessionId: normalizedAgentSessionId,
			agentId,
			threadId,
			agentUniqueId,
			startedAt,
			hasSessionModelBinding: Boolean(sessionModelBinding),
		},
	});

	return {
		ok: true,
		hydrated: {
			agentId,
			agentUniqueId,
			metadata: {
				agentId,
				agentUniqueId,
				agentSessionId: fetched.agentSessionId ?? normalizedAgentSessionId,
				threadId,
				startedAt,
				agentName: "astro-orchestrator",
				projectId: null,
				cwd: null,
				repoRoot: null,
				pendingOnboarding: false,
				pendingRuntimeBootstrap: false,
				switchSummary: null,
				projectRuntime: null,
				sessionModelBinding,
				sessionConfigOverrides: null,
			},
		},
	};
}

function runGitCommand(
	cwd: string,
	args: string[],
	allowExitCodes: number[] = [0],
): {
	ok: boolean;
	stdout: string;
	stderr: string;
	exitCode: number | null;
} {
	const result = spawnSync("git", args, {
		cwd,
		encoding: "utf8",
		maxBuffer: 32 * 1024 * 1024,
		env: {
			...process.env,
			GIT_PAGER: "cat",
		},
	});

	const stdout = typeof result.stdout === "string" ? result.stdout : "";
	const stderr = typeof result.stderr === "string" ? result.stderr : "";
	const exitCode = typeof result.status === "number" ? result.status : null;
	const commandError = result.error?.message?.trim();
	const mergedStderr = commandError
		? [stderr.trim(), commandError].filter(Boolean).join("\n")
		: stderr.trim();

	return {
		ok: exitCode !== null && allowExitCodes.includes(exitCode),
		stdout,
		stderr: mergedStderr,
		exitCode,
	};
}

function resolveGitRepoRoot(startCwd: string): string | null {
	const result = runGitCommand(startCwd, ["rev-parse", "--show-toplevel"]);
	if (!result.ok) return null;
	return normalizeRepoRoot(result.stdout);
}

function gitHeadExists(repoRootPath: string): boolean {
	return runGitCommand(repoRootPath, ["rev-parse", "--verify", "HEAD"]).ok;
}

function resolveDiffFileStatus(indexStatus: string, worktreeStatus: string): DiffFileStatus {
	if (indexStatus === "?" && worktreeStatus === "?") return "untracked";
	if (indexStatus === "U" || worktreeStatus === "U") return "unmerged";
	if (indexStatus === "R" || worktreeStatus === "R") return "renamed";
	if (indexStatus === "C" || worktreeStatus === "C") return "copied";
	if (indexStatus === "A" || worktreeStatus === "A") return "added";
	if (indexStatus === "D" || worktreeStatus === "D") return "deleted";
	if (indexStatus === "T" || worktreeStatus === "T") return "typechange";
	if (indexStatus === "M" || worktreeStatus === "M") return "modified";
	return "unknown";
}

function parseStatusEntryPath(rawPath: string): string {
	return rawPath.replaceAll("\\", "/");
}

function parseGitStatusPorcelain(stdout: string): DiffFileSummary[] {
	const records = stdout.split("\0");
	if (records.at(-1) === "") records.pop();

	const files: DiffFileSummary[] = [];
	for (let index = 0; index < records.length; index += 1) {
		const entry = records[index];
		if (!entry) continue;

		const statusCode = entry.slice(0, 2);
		const currentPath = entry.length > 3 ? parseStatusEntryPath(entry.slice(3)) : "";
		if (!currentPath) continue;

		let originalPath: string | null = null;
		if ((statusCode.includes("R") || statusCode.includes("C")) && index + 1 < records.length) {
			originalPath = parseStatusEntryPath(records[index + 1]);
			index += 1;
		}

		const indexStatus = statusCode[0] ?? " ";
		const worktreeStatus = statusCode[1] ?? " ";
		const untracked = statusCode === "??";
		files.push({
			path: currentPath,
			originalPath,
			status: resolveDiffFileStatus(indexStatus, worktreeStatus),
			indexStatus: untracked ? null : indexStatus,
			worktreeStatus: untracked ? null : worktreeStatus,
			staged: !untracked && indexStatus !== " ",
			unstaged: !untracked && worktreeStatus !== " ",
			untracked,
		});
	}

	return files;
}

function joinGitPatches(parts: string[]): string {
	let combined = "";
	for (const part of parts) {
		if (!part.trim()) continue;
		if (combined && !combined.endsWith("\n")) combined += "\n";
		combined += part;
		if (!combined.endsWith("\n")) combined += "\n";
	}
	return combined;
}

function buildUntrackedPatch(repoRootPath: string, relativePath: string): { ok: boolean; patch: string; error?: string } {
	const result = runGitCommand(
		repoRootPath,
		["diff", "--no-index", "--binary", "--no-ext-diff", "--", "/dev/null", relativePath],
		[0, 1],
	);
	if (!result.ok) {
		return {
			ok: false,
			patch: "",
			error: result.stderr || `git diff --no-index failed with exit code ${result.exitCode ?? "unknown"}.`,
		};
	}
	return { ok: true, patch: result.stdout };
}

function buildRepoDiffSnapshot(repoRootPath: string): {
	ok: boolean;
	base: "HEAD" | "staged_and_worktree";
	patch: string;
	files: DiffFileSummary[];
	error?: string;
} {
	const statusResult = runGitCommand(repoRootPath, ["status", "--porcelain=v1", "--untracked-files=all", "-z"]);
	if (!statusResult.ok) {
		return {
			ok: false,
			base: "HEAD",
			patch: "",
			files: [],
			error: statusResult.stderr || "git status failed while building the repo diff snapshot.",
		};
	}

	const files = parseGitStatusPorcelain(statusResult.stdout);
	const hasHead = gitHeadExists(repoRootPath);

	let trackedPatch = "";
	let base: "HEAD" | "staged_and_worktree" = hasHead ? "HEAD" : "staged_and_worktree";

	if (hasHead) {
		const trackedResult = runGitCommand(
			repoRootPath,
			["diff", "--binary", "--find-renames", "--no-ext-diff", "HEAD", "--"],
			[0, 1],
		);
		if (!trackedResult.ok) {
			return {
				ok: false,
				base,
				patch: "",
				files,
				error:
					trackedResult.stderr ||
					"git diff HEAD failed while building the repo diff snapshot.",
			};
		}
		trackedPatch = trackedResult.stdout;
	} else {
		const stagedResult = runGitCommand(
			repoRootPath,
			["diff", "--cached", "--binary", "--find-renames", "--no-ext-diff", "--"],
			[0, 1],
		);
		if (!stagedResult.ok) {
			return {
				ok: false,
				base,
				patch: "",
				files,
				error:
					stagedResult.stderr ||
					"git diff --cached failed while building the repo diff snapshot.",
			};
		}

		const unstagedResult = runGitCommand(
			repoRootPath,
			["diff", "--binary", "--find-renames", "--no-ext-diff", "--"],
			[0, 1],
		);
		if (!unstagedResult.ok) {
			return {
				ok: false,
				base,
				patch: "",
				files,
				error:
					unstagedResult.stderr ||
					"git diff failed while building the repo diff snapshot.",
			};
		}

		trackedPatch = joinGitPatches([stagedResult.stdout, unstagedResult.stdout]);
	}

	const untrackedPatches: string[] = [];
	for (const file of files) {
		if (!file.untracked) continue;
		const patchResult = buildUntrackedPatch(repoRootPath, file.path);
		if (!patchResult.ok) {
			return {
				ok: false,
				base,
				patch: "",
				files,
				error:
					patchResult.error ||
					`Failed to build an untracked-file patch for ${file.path}.`,
			};
		}
		untrackedPatches.push(patchResult.patch);
	}

	return {
		ok: true,
		base,
		patch: joinGitPatches([trackedPatch, ...untrackedPatches]),
		files,
	};
}

function isExistingDirectory(candidate: string): boolean {
	try {
		return statSync(candidate).isDirectory();
	} catch {
		return false;
	}
}

function loadSpecialistAgent(agentName: string): AgentConfig | null {
	if (agentName === "astro-orchestrator") return null;
	const discovery = discoverAgents(repoRoot, "project");
	return discovery.agents.find((candidate) => candidate.name === agentName) ?? null;
}

function writePromptToTempFile(agentName: string, prompt: string): string {
	const tempDir = mkdtempSync(path.join(tmpdir(), `astro-stream-${agentName.replace(/[^\w.-]+/g, "_")}-`));
	const promptPath = path.join(tempDir, "append-system-prompt.md");
	writeFileSync(promptPath, prompt, { encoding: "utf8", mode: 0o600 });
	return promptPath;
}

function cleanupPromptFile(promptPath: string | null) {
	if (!promptPath) return;
	try {
		rmSync(path.dirname(promptPath), { recursive: true, force: true });
	} catch {
		// ignore prompt cleanup failures
	}
}

function parseSessionSwitchRequest(result: any): SessionSwitchRequest | null {
	const candidate = result?.details?.sessionSwitch;
	if (!candidate || typeof candidate !== "object") return null;
	if (candidate.kind !== "project_session_switch") return null;
	if (candidate.agentName !== "mainsequence-project-coder") return null;
	if (typeof candidate.projectId !== "string" || !candidate.projectId.trim()) return null;
	if (typeof candidate.cwd !== "string" || !candidate.cwd.trim()) return null;

	return {
		kind: "project_session_switch",
		agentName: "mainsequence-project-coder",
		projectId: candidate.projectId.trim(),
		cwd: path.resolve(candidate.cwd),
		initialTask:
			typeof candidate.initialTask === "string" && candidate.initialTask.trim()
				? candidate.initialTask.trim()
				: null,
		summary:
			typeof candidate.summary === "string" && candidate.summary.trim()
				? candidate.summary.trim()
				: null,
	};
}

function toSessionTimestamp(input: string): string {
	const parsed = Date.parse(input);
	return Number.isFinite(parsed) ? String(parsed) : String(Date.now());
}

function buildPendingRuntimeSessionId(agentUniqueId: string, startedAt: string): string {
	return sanitizeSessionKey(`${agentUniqueId}__pending_${toSessionTimestamp(startedAt)}`);
}

function buildBackendRuntimeSessionId(agentSessionId: number): string {
	return sanitizeSessionKey(String(agentSessionId));
}

function sessionExists(sessionKey: string): boolean {
	return existsSync(getSessionPath(sessionKey)) || existsSync(getSessionMetadataPath(sessionKey));
}

function getSessionPath(sessionKey: string): string {
	return path.join(sessionDir, `${sessionKey}.jsonl`);
}

function getSessionMetadataPath(sessionKey: string): string {
	return path.join(sessionDir, `${sessionKey}.meta.json`);
}

function getThreadBindingPath(threadId: string): string {
	return path.join(sessionDir, `${sanitizeSessionKey(threadId)}.thread.json`);
}

function readSessionMetadata(sessionKey: string): SessionMetadata | null {
	const metadataPath = getSessionMetadataPath(sessionKey);
	if (!existsSync(metadataPath)) return null;

	try {
		const parsed = JSON.parse(readFileSync(metadataPath, "utf8"));
		if (!parsed || typeof parsed !== "object") return null;
		const rawAgentId = (parsed as { agentId?: unknown }).agentId;
		const rawAgentUniqueId = (parsed as { agentUniqueId?: unknown }).agentUniqueId;
		const rawAgentSessionId = (parsed as { agentSessionId?: unknown }).agentSessionId;
		const rawThreadId = (parsed as { threadId?: unknown }).threadId;
		const rawStartedAt = (parsed as { startedAt?: unknown }).startedAt;
		const rawAgentName = (parsed as { agentName?: unknown }).agentName;
		const rawProjectId = (parsed as { projectId?: unknown }).projectId;
		const rawCwd = (parsed as { cwd?: unknown }).cwd;
		const rawRepoRoot = (parsed as { repoRoot?: unknown }).repoRoot;
		const rawPendingOnboarding = (parsed as { pendingOnboarding?: unknown }).pendingOnboarding;
		const rawPendingRuntimeBootstrap = (parsed as { pendingRuntimeBootstrap?: unknown }).pendingRuntimeBootstrap;
		const rawSwitchSummary = (parsed as { switchSummary?: unknown }).switchSummary;
		const rawProjectRuntime = (parsed as { projectRuntime?: unknown }).projectRuntime;
		const rawSessionModelBinding = (parsed as { sessionModelBinding?: unknown }).sessionModelBinding;
		const rawSessionConfigOverrides = (parsed as { sessionConfigOverrides?: unknown }).sessionConfigOverrides;
		const normalizedAgentId =
			typeof rawAgentId === "number" && Number.isFinite(rawAgentId)
				? rawAgentId
				: typeof rawAgentId === "string" && rawAgentId.trim()
					? Number.parseInt(rawAgentId, 10)
					: null;
		const normalizedAgentSessionId =
			typeof rawAgentSessionId === "number" && Number.isFinite(rawAgentSessionId)
				? rawAgentSessionId
				: typeof rawAgentSessionId === "string" && rawAgentSessionId.trim()
					? Number.parseInt(rawAgentSessionId, 10)
					: null;
		const normalizedAgentUniqueId =
			typeof rawAgentUniqueId === "string" && rawAgentUniqueId.trim()
				? rawAgentUniqueId.trim()
				: null;
		const normalizedThreadId =
			typeof rawThreadId === "string" && rawThreadId.trim() ? rawThreadId.trim() : null;
		const normalizedStartedAt =
			typeof rawStartedAt === "string" && rawStartedAt.trim() ? rawStartedAt.trim() : null;
		const normalizedAgentName =
			typeof rawAgentName === "string" && rawAgentName.trim() ? rawAgentName.trim() : null;
		const normalizedProjectId = normalizeProjectId(rawProjectId);
		const normalizedCwd = normalizeProjectCwd(rawCwd);
		const normalizedRepoRoot = normalizeRepoRoot(rawRepoRoot);
		const normalizedPendingOnboarding = rawPendingOnboarding === true;
		const normalizedPendingRuntimeBootstrap = rawPendingRuntimeBootstrap === true;
		const normalizedSwitchSummary =
			typeof rawSwitchSummary === "string" && rawSwitchSummary.trim() ? rawSwitchSummary.trim() : null;
		const normalizedProjectRuntime =
			rawProjectRuntime && typeof rawProjectRuntime === "object"
				? (rawProjectRuntime as ProjectRuntimeSnapshot)
				: null;
		const normalizedSessionModelBinding = normalizeSessionModelBinding(rawSessionModelBinding);
		const normalizedSessionConfigOverrides = normalizeSessionConfigOverrides(rawSessionConfigOverrides);
		return {
			agentId: Number.isFinite(normalizedAgentId as number) ? (normalizedAgentId as number) : null,
			agentUniqueId: normalizedAgentUniqueId,
			agentSessionId: Number.isFinite(normalizedAgentSessionId as number)
				? (normalizedAgentSessionId as number)
				: null,
			threadId: normalizedThreadId,
			startedAt: normalizedStartedAt,
			agentName: normalizedAgentName,
			projectId: normalizedProjectId,
			cwd: normalizedCwd,
			repoRoot: normalizedRepoRoot,
			pendingOnboarding: normalizedPendingOnboarding,
			pendingRuntimeBootstrap: normalizedPendingRuntimeBootstrap,
			switchSummary: normalizedSwitchSummary,
			projectRuntime: normalizedProjectRuntime,
			sessionModelBinding: normalizedSessionModelBinding,
			sessionConfigOverrides: normalizedSessionConfigOverrides,
		};
	} catch {
		return null;
	}
}

function writeSessionMetadata(sessionKey: string, metadata: SessionMetadata) {
	mkdirSync(sessionDir, { recursive: true });
	writeFileSync(getSessionMetadataPath(sessionKey), JSON.stringify(metadata, null, 2));
}

function resolveSessionRepoRoot(sessionKey: string, metadata: SessionMetadata): string | null {
	if (metadata.repoRoot) return metadata.repoRoot;
	if (!metadata.cwd) return null;

	const resolvedRepoRoot = resolveGitRepoRoot(metadata.cwd);
	if (!resolvedRepoRoot) return null;

	writeSessionMetadata(sessionKey, {
		...metadata,
		repoRoot: resolvedRepoRoot,
	});
	return resolvedRepoRoot;
}

function buildSessionToolUrl(pathname: string, sessionKey: string): string {
	return `${pathname}?sessionId=${encodeURIComponent(sessionKey)}`;
}

function buildAvailableSessionTools(sessionKey: string, metadata: SessionMetadata): SessionAvailableTools {
	const tools: SessionAvailableTools = {};

	if (metadata.agentName === "mainsequence-project-coder") {
		const resolvedRepoRoot = resolveSessionRepoRoot(sessionKey, metadata);
		if (resolvedRepoRoot && isExistingDirectory(resolvedRepoRoot)) {
			tools.repo_diff = {
				url: buildSessionToolUrl("/api/chat/diff", sessionKey),
			};
		}
	}

	return tools;
}

function writeMockStreamResponse(
	req: import("node:http").IncomingMessage,
	res: import("node:http").ServerResponse,
	input: { threadId: string; latestUserMessage: string },
) {
	applyCorsHeaders(req, res);
	res.writeHead(200, {
		"Content-Type": "text/event-stream",
		"Cache-Control": "no-cache",
		Connection: "keep-alive",
		"X-Thread-Id": input.threadId,
		"X-Stream-Protocol": "ui-message-stream",
		Protocol: "ui-message-stream",
	});

	res.write("retry: 1000\n\n");

	let eventId = 0;
	const writeMockChunk = (chunk: ReturnType<typeof attachAgentId>) => {
		eventId += 1;
		res.write(serializeSse(eventId, chunk));
		if (logTraffic) {
			console.log(`[astro-stream] MOCK thread=${input.threadId}: ${JSON.stringify(chunk)}`);
		}
	};

	const messageId = `mock_${Date.now().toString(36)}_${Math.floor(Math.random() * 1e6).toString(36)}`;
	const mockText =
		`Mock response active. Agent execution, session creation, and conversation persistence were skipped for frontend testing. ` +
		`Received message: ${input.latestUserMessage}`;

	writeMockChunk(
		attachAgentId(
			{
				type: "start",
				messageId,
				threadId: input.threadId,
			},
			null,
		),
	);
	writeMockChunk(attachAgentId({ type: "text-start", id: "t1" }, null));
	writeMockChunk(attachAgentId({ type: "text-delta", textDelta: mockText }, null));
	writeMockChunk(attachAgentId({ type: "text-end" }, null));
	writeMockChunk(attachAgentId({ type: "finish", finishReason: "stop" }, null));
	res.write("data: [DONE]\n\n");
	if (logTraffic) {
		console.log(`[astro-stream] MOCK thread=${input.threadId}: [DONE]`);
	}
	res.end();
}

function readThreadBinding(threadId: string): ThreadSessionBinding | null {
	const bindingPath = getThreadBindingPath(threadId);
	if (!existsSync(bindingPath)) return null;

	try {
		const parsed = JSON.parse(readFileSync(bindingPath, "utf8"));
		if (!parsed || typeof parsed !== "object") return null;
		const runtimeSessionId =
			typeof (parsed as { runtimeSessionId?: unknown }).runtimeSessionId === "string" &&
			(parsed as { runtimeSessionId?: string }).runtimeSessionId?.trim()
				? (parsed as { runtimeSessionId: string }).runtimeSessionId.trim()
				: null;
		const normalizedThreadId =
			typeof (parsed as { threadId?: unknown }).threadId === "string" &&
			(parsed as { threadId?: string }).threadId?.trim()
				? (parsed as { threadId: string }).threadId.trim()
				: null;
		const updatedAt =
			typeof (parsed as { updatedAt?: unknown }).updatedAt === "string" &&
			(parsed as { updatedAt?: string }).updatedAt?.trim()
				? (parsed as { updatedAt: string }).updatedAt.trim()
				: null;
		if (!runtimeSessionId || !normalizedThreadId) return null;
		return {
			threadId: normalizedThreadId,
			runtimeSessionId,
			updatedAt: updatedAt ?? null,
		};
	} catch {
		return null;
	}
}

function writeThreadBinding(binding: ThreadSessionBinding) {
	mkdirSync(sessionDir, { recursive: true });
	writeFileSync(getThreadBindingPath(binding.threadId), JSON.stringify(binding, null, 2));
}

async function createBackendRuntimeSession(options: {
	agentName: string;
	userId: string;
	threadId: string;
	projectId: string | null;
	cwd: string | null;
	externalSessionId?: string;
	sessionMetadata?: Record<string, unknown>;
	pendingOnboarding?: boolean;
	pendingRuntimeBootstrap?: boolean;
	switchSummary?: string | null;
	projectRuntime?: ProjectRuntimeSnapshot | null;
	sessionModelBinding?: SessionModelBinding | null;
	sessionConfigOverrides?: SessionConfigOverrides | null;
}): Promise<
	| {
			ok: true;
			agentId: number;
			agentUniqueId: string;
			agentSessionId: number;
			sessionKey: string;
			startedAt: string;
	  }
	| {
			ok: false;
			error: string;
	  }
> {
	const registration = await registerMainsequenceAgent({
		agentName: options.agentName,
		agentRole: options.agentName === "astro-orchestrator" ? "orchestrator" : "specialist",
		cwd: options.cwd ?? repoRoot,
		projectId: options.projectId,
		userId: options.userId,
		log: (message) => {
			console.log(`[astro-stream] ${message}`);
		},
	});

	if (!registration.agentId) {
		return {
			ok: false,
			error: registration.stderr || "Failed to resolve backend agent id.",
		};
	}

	const agentId = registration.agentId;
	const agentUniqueId =
		registration.agentUniqueId ??
		buildAgentUniqueId({
			agentName: options.agentName,
			userId: options.userId,
			projectId: options.projectId,
		});

	const startedAt = new Date().toISOString();
	const frozenRepoRoot =
		options.agentName === "mainsequence-project-coder" && options.cwd
			? resolveGitRepoRoot(options.cwd)
			: null;
	const pendingRuntimeSessionId = buildPendingRuntimeSessionId(agentUniqueId, startedAt);
	const runtimeConfig = buildRuntimeConfigSnapshot(options.sessionModelBinding ?? null);

	const payload: Record<string, unknown> = {
		status: "running",
		started_at: startedAt,
		ended_at: null,
		created_by_user: options.userId,
		llm_provider: resolveBackendLlmProvider(options.sessionModelBinding ?? null),
		llm_model: resolveBackendLlmModel(options.sessionModelBinding ?? null),
		engine_name: options.agentName === "astro-orchestrator" ? "astro_router_v1" : "astro_project_session_v1",
		runtime_config_snapshot: runtimeConfig,
		error_detail: "",
		external_session_id: options.externalSessionId ?? "",
		runtime_session_id: pendingRuntimeSessionId,
		thread_id: options.threadId,
		usage_summary: {
			input_tokens: 0,
			output_tokens: 0,
			total_tokens: 0,
			estimated_cost_usd: 0,
		},
		session_metadata: {
			source: "frontend",
			workflow_key: options.agentName,
			...(options.projectId ? { project_id: options.projectId } : {}),
			...(options.cwd ? { project_cwd: options.cwd } : {}),
			...(frozenRepoRoot ? { project_repo_root: frozenRepoRoot } : {}),
			...(options.projectRuntime ? { project_runtime_snapshot: options.projectRuntime } : {}),
			...(options.sessionModelBinding ? { session_model_binding: options.sessionModelBinding } : {}),
			...(options.sessionMetadata ?? {}),
		},
	};

	const sessionStart = await startBackendAgentSession({
		agentId,
		payload,
		env: process.env,
		log: (message) => {
			console.log(`[astro-stream] ${message}`);
		},
	});

	if (!sessionStart.ok || !sessionStart.agentSessionId) {
		return {
			ok: false,
			error: sessionStart.error || "Failed to start backend agent session.",
		};
	}

	const agentSessionId = sessionStart.agentSessionId;
	const sessionKey = buildBackendRuntimeSessionId(agentSessionId);
	writeSessionMetadata(sessionKey, {
		agentId,
		agentUniqueId,
		agentSessionId,
		threadId: options.threadId,
		startedAt,
		agentName: options.agentName,
		projectId: options.projectId,
		cwd: options.cwd,
		repoRoot: frozenRepoRoot,
		pendingOnboarding: options.pendingOnboarding === true,
		pendingRuntimeBootstrap: options.pendingRuntimeBootstrap === true,
		switchSummary: options.switchSummary ?? null,
		projectRuntime: options.projectRuntime ?? null,
		sessionModelBinding: options.sessionModelBinding ?? null,
		sessionConfigOverrides: options.sessionConfigOverrides ?? null,
	});
	writeThreadBinding({
		threadId: options.threadId,
		runtimeSessionId: sessionKey,
		updatedAt: startedAt,
	});

	createConversationStore({
		sessionDir,
		sessionKey,
		threadId: options.threadId,
		agentName: options.agentName,
		agentId,
		agentSessionId,
		startedAt,
	});

	return {
		ok: true,
		agentId,
		agentUniqueId,
		agentSessionId,
		sessionKey,
		startedAt,
	};
}

function compactLogValue(value: string, maxLength = 240): string {
	const compact = value.replace(/\s+/g, " ").trim();
	if (!compact) return "";
	if (compact.length <= maxLength) return compact;
	return `${compact.slice(0, maxLength - 3).trimEnd()}...`;
}

function stringifyLogValue(value: unknown): string {
	if (typeof value === "string") return value.trim();
	try {
		return JSON.stringify(value);
	} catch {
		return String(value);
	}
}

function normalizeLogString(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function updateResponseModelFromMessage(ctx: RequestContext, message: unknown) {
	if (!message || typeof message !== "object") return;
	const provider = normalizeLogString((message as { provider?: unknown }).provider);
	const model = normalizeLogString((message as { model?: unknown }).model);
	if (provider) ctx.responseProvider = provider;
	if (model) ctx.responseModel = model;
}

function mapUsageSummary(usage: unknown): { inputTokens?: number; outputTokens?: number } | undefined {
	if (!usage || typeof usage !== "object") return undefined;

	const inputTokens = (() => {
		const input = (usage as { input?: unknown }).input;
		if (typeof input === "number") return input;
		const inputTokensValue = (usage as { inputTokens?: unknown }).inputTokens;
		return typeof inputTokensValue === "number" ? inputTokensValue : undefined;
	})();
	const outputTokens = (() => {
		const output = (usage as { output?: unknown }).output;
		if (typeof output === "number") return output;
		const outputTokensValue = (usage as { outputTokens?: unknown }).outputTokens;
		return typeof outputTokensValue === "number" ? outputTokensValue : undefined;
	})();

	if (inputTokens == null && outputTokens == null) return undefined;
	return {
		...(inputTokens != null ? { inputTokens } : {}),
		...(outputTokens != null ? { outputTokens } : {}),
	};
}

function formatLoggedModel(ctx: Pick<RequestContext, "responseProvider" | "responseModel" | "sessionModelBinding">): string | null {
	if (ctx.responseProvider && ctx.responseModel) return `${ctx.responseProvider}/${ctx.responseModel}`;
	if (ctx.responseModel) return ctx.responseModel;
	if (ctx.sessionModelBinding) return `${ctx.sessionModelBinding.provider}/${ctx.sessionModelBinding.model}`;
	return null;
}

function getOutgoingLogPrefix(ctx: RequestContext): string {
	const model = formatLoggedModel(ctx);
	return `[astro-stream] OUT agent=${ctx.agentName} session=${ctx.sessionKey} thread=${ctx.threadId}${model ? ` model=${model}` : ""}`;
}

function resolveToolCallLogEntry(
	ctx: RequestContext,
	toolCallId?: string,
): { toolCallId: string; toolName: string; args: string } | null {
	if (toolCallId) {
		const entry = ctx.logState.toolCalls.get(toolCallId);
		if (entry) {
			return { toolCallId, toolName: entry.toolName, args: entry.args };
		}
	}
	if (ctx.logState.toolCalls.size !== 1) return null;
	const [resolvedToolCallId, entry] = ctx.logState.toolCalls.entries().next().value as [
		string,
		{ toolName: string; args: string },
	];
	return { toolCallId: resolvedToolCallId, toolName: entry.toolName, args: entry.args };
}

function flushPendingReadableLogs(ctx: RequestContext, label = "partial") {
	const prefix = getOutgoingLogPrefix(ctx);

	if (ctx.logState.reasoning) {
		const preview = compactLogValue(ctx.logState.reasoning.text, 180);
		if (preview) {
			console.log(`${prefix}: reasoning-${label} preview=${JSON.stringify(preview)}`);
		}
		ctx.logState.reasoning = null;
	}

	if (ctx.logState.text) {
		const preview = compactLogValue(ctx.logState.text.text, 320);
		if (preview) {
			console.log(`${prefix}: assistant-text-${label} text=${JSON.stringify(preview)}`);
		}
		ctx.logState.text = null;
	}

	for (const [toolCallId, entry] of ctx.logState.toolCalls.entries()) {
		const preview = compactLogValue(entry.args, 320);
		const suffix = preview ? ` args=${JSON.stringify(preview)}` : "";
		console.log(`${prefix}: tool-call-${label} tool=${entry.toolName} toolCallId=${toolCallId}${suffix}`);
	}
	ctx.logState.toolCalls.clear();
}

function logReadableChunk(ctx: RequestContext, chunk: ReturnType<typeof attachAgentId>) {
	if (!logTraffic) return;

	const prefix = getOutgoingLogPrefix(ctx);

	switch (chunk.type) {
		case "new_session":
			console.log(
				`${prefix}: new_session agent_session_id=${chunk.new_session.agent_session_id} session_key=${chunk.new_session.session_key}`,
			);
			return;
		case "session_switch":
			console.log(
				`${prefix}: session_switch to=${chunk.session_switch.to_agent_name} project_id=${chunk.session_switch.project_id} session_key=${chunk.session_switch.session_key}`,
			);
			return;
		case "start":
			console.log(`${prefix}: start messageId=${chunk.messageId}`);
			return;
		case "reasoning-start":
			ctx.logState.reasoning = { id: chunk.id, text: "" };
			return;
		case "reasoning-delta":
			if (!ctx.logState.reasoning) {
				ctx.logState.reasoning = { id: "unknown", text: "" };
			}
			ctx.logState.reasoning.text += chunk.delta;
			return;
		case "reasoning-end": {
			const preview = compactLogValue(ctx.logState.reasoning?.text ?? "", 180);
			if (preview) {
				console.log(`${prefix}: reasoning preview=${JSON.stringify(preview)}`);
			}
			ctx.logState.reasoning = null;
			return;
		}
		case "text-start":
			ctx.logState.text = { id: chunk.id, text: "" };
			return;
		case "text-delta":
			if (!ctx.logState.text) {
				ctx.logState.text = { id: "unknown", text: "" };
			}
			ctx.logState.text.text += chunk.textDelta;
			return;
		case "text-end": {
			const preview = compactLogValue(ctx.logState.text?.text ?? "", 320);
			if (preview) {
				console.log(`${prefix}: assistant-text text=${JSON.stringify(preview)}`);
			}
			ctx.logState.text = null;
			return;
		}
		case "tool-call-start":
			ctx.logState.toolCalls.set(chunk.toolCallId, { toolName: chunk.toolName, args: "" });
			return;
		case "tool-call-delta": {
			const entry = resolveToolCallLogEntry(ctx, chunk.toolCallId);
			if (!entry) return;
			ctx.logState.toolCalls.set(entry.toolCallId, {
				toolName: entry.toolName,
				args: entry.args + chunk.argsText,
			});
			return;
		}
		case "tool-call-end": {
			const entry = resolveToolCallLogEntry(ctx, chunk.toolCallId);
			if (!entry) {
				console.log(`${prefix}: tool-call tool=unknown`);
				return;
			}
			const preview = compactLogValue(entry.args, 320);
			const suffix = preview ? ` args=${JSON.stringify(preview)}` : "";
			console.log(`${prefix}: tool-call tool=${entry.toolName} toolCallId=${entry.toolCallId}${suffix}`);
			ctx.logState.toolCalls.delete(entry.toolCallId);
			return;
		}
		case "tool-result": {
			const preview = compactLogValue(stringifyLogValue(chunk.result), 320);
			console.log(
				`${prefix}: tool-result toolCallId=${chunk.toolCallId}${preview ? ` result=${JSON.stringify(preview)}` : ""}`,
			);
			return;
		}
		case "finish": {
			flushPendingReadableLogs(ctx);
			const usageSuffix = chunk.usage
				? ` usage=${JSON.stringify({
						inputTokens: (chunk.usage as { inputTokens?: number }).inputTokens,
						outputTokens: (chunk.usage as { outputTokens?: number }).outputTokens,
				  })}`
				: "";
			console.log(`${prefix}: finish reason=${chunk.finishReason}${usageSuffix}`);
			return;
		}
		case "error":
			flushPendingReadableLogs(ctx);
			console.log(`${prefix}: error ${JSON.stringify(compactLogValue(chunk.error, 320))}`);
			return;
	}
}

function abortStreamOnPersistenceFailure(ctx: RequestContext, error: unknown) {
	const message = error instanceof Error ? error.message : String(error);
	console.error(
		`[astro-stream] conversation persistence failed agent=${ctx.agentName} session=${ctx.sessionKey} thread=${ctx.threadId}: ${message}`,
	);
	ctx.finished = true;
	ctx.res.destroy(error instanceof Error ? error : new Error(message));
}

function writeChunkWithAgentId(ctx: RequestContext, chunk: StreamEvent, agentId: number | null) {
	if (ctx.finished) return;
	ctx.eventId += 1;
	const enrichedChunk = attachAgentId(chunk, agentId);
	try {
		ctx.conversationStore.recordStreamChunkSync(enrichedChunk);
	} catch (error) {
		abortStreamOnPersistenceFailure(ctx, error);
		return;
	}
	const payload = serializeSse(ctx.eventId, enrichedChunk);
	ctx.res.write(payload);
	logReadableChunk(ctx, enrichedChunk);
}

function writeChunk(ctx: RequestContext, chunk: StreamEvent) {
	writeChunkWithAgentId(ctx, chunk, ctx.agentId);
}

function emitAssistantText(ctx: RequestContext, text: string, agentId: number | null = ctx.agentId) {
	const trimmed = text.trim();
	if (!trimmed) return;
	ctx.textCounter += 1;
	const id = `t${ctx.textCounter}`;
	writeChunkWithAgentId(ctx, { type: "text-start", id }, agentId);
	writeChunkWithAgentId(ctx, { type: "text-delta", textDelta: trimmed }, agentId);
	writeChunkWithAgentId(ctx, { type: "text-end" }, agentId);
}

function writeDone(ctx: RequestContext) {
	if (ctx.finished) return;
	try {
		ctx.conversationStore.recordStreamDoneSync();
	} catch (error) {
		abortStreamOnPersistenceFailure(ctx, error);
		return;
	}
	ctx.res.write("data: [DONE]\n\n");
	if (logTraffic) {
		console.log(`${getOutgoingLogPrefix(ctx)}: [DONE]`);
	}
	ctx.finished = true;
	ctx.res.end();
}

function extractText(content: any): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.map((part) => {
			if (part?.type === "text" && typeof part.text === "string") return part.text;
			if (part?.type === "tool-call") {
				return `[tool-call] ${part.toolName ?? part.name ?? "unknown"} ${JSON.stringify(
					part.args ?? part.arguments ?? {},
				)}`;
			}
			if (part?.type === "tool-result") {
				return `[tool-result] ${part.toolName ?? "unknown"} ${JSON.stringify(
					part.result ?? part.content ?? {},
				)}`;
			}
			return "";
		})
		.filter(Boolean)
		.join("");
}

function extractTextParts(content: any): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.map((part) => (part?.type === "text" && typeof part.text === "string" ? part.text : ""))
		.filter(Boolean)
		.join("");
}

function buildProjectSwitchAnnouncement(request: SessionSwitchRequest): string {
	const summary = request.summary?.trim() ?? "";
	const quotedProjectName = summary.match(/["']([^"']+)["']/)?.[1]?.trim() ?? null;
	if (quotedProjectName) {
		return `${quotedProjectName} is checked out locally and ready. I’m switching you now to the project coding agent so we can keep working inside that project.`;
	}
	if (summary) {
		return `${summary} I’m switching you now to the project coding agent so we can continue inside this checked-out project.`;
	}
	return `Project ${request.projectId} is checked out locally and ready. I’m switching you now to the project coding agent so we can continue working inside that project.`;
}

function buildProjectRuntimeBootstrapAnnouncement(summary: string | null): string {
	const quotedProjectName = summary?.match(/["']([^"']+)["']/)?.[1]?.trim() ?? null;
	if (quotedProjectName) {
		return `Verifying the project environment for ${quotedProjectName}. I’m checking SDK status, preparing the local virtual environment, syncing dependencies, and activating the project runtime before we continue.`;
	}
	return "Verifying the project environment. I’m checking SDK status, preparing the local virtual environment, syncing dependencies, and activating the project runtime before we continue.";
}

function buildProjectRuntimeReadyAnnouncement(summary: string | null): string {
	const quotedProjectName = summary?.match(/["']([^"']+)["']/)?.[1]?.trim() ?? null;
	if (quotedProjectName) {
		return `${quotedProjectName} is ready. The project environment is prepared and I’m now in the project coding session.`;
	}
	return "The project environment is prepared and I’m now in the project coding session.";
}

function buildCoderOnboardingInstruction(
	summary: string | null,
	projectRuntime: ProjectRuntimeSnapshot | null,
): string {
	const summaryPrefix = summary?.trim() ? `${summary.trim()} ` : "";
	const runtimeInstruction = projectRuntime
		? "The runtime already completed the deterministic project bootstrap for this session. Use the provided project runtime bootstrap summary as your source of truth for SDK and active virtualenv state, and do not rerun `mainsequence project sdk-status --path . --json`, `mainsequence project build_local_venv --path .`, or `uv sync` unless you are intentionally refreshing the environment after a user-approved change."
		: "Runtime bootstrap metadata is missing, so you must establish the SDK/runtime state yourself before broader onboarding.";
	return `${summaryPrefix}This is the first turn after switching into a checked-out project session without a concrete implementation task. ${runtimeInstruction} Then perform the rest of the no-task onboarding flow from your specialist prompt: handle missing or outdated \`AGENTS.md\` / agent skills exactly as instructed, ask the user about upgrading \`mainsequence\` only if the project SDK version or active environment version differs from the latest available version, and then answer the latest user message in that project context.`;
}

function isPlainObject(value: any): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stringifyInline(value: unknown): string {
	if (typeof value === "string") return value.trim();
	try {
		return JSON.stringify(value);
	} catch {
		return String(value);
	}
}

function addPromptField(lines: string[], label: string, value: unknown) {
	if (value == null) return;

	if (typeof value === "string") {
		const trimmed = value.trim();
		if (!trimmed) return;
		lines.push(`- ${label}: ${trimmed}`);
		return;
	}

	if (Array.isArray(value) && value.length === 0) return;
	if (isPlainObject(value) && Object.keys(value).length === 0) return;

	lines.push(`- ${label}: ${stringifyInline(value)}`);
}

function extractLatestUserMessage(messages: any[]): string {
	const msg = messages[messages.length - 1];
	if (!msg || typeof msg !== "object") return "";
	if (msg.role !== "user") return "";

	const content = extractText(msg.content);
	return content.trim();
}

function buildPrompt(
	system: string | undefined,
	latestUserMessage: string,
	context: Record<string, unknown>,
	tools: Record<string, unknown>,
	options: {
		onboardingInstruction?: string | null;
		switchSummary?: string | null;
		projectRuntimeSummary?: string | null;
	} = {},
): string {
	const lines: string[] = [];
	if (system?.trim()) lines.push(`System: ${system.trim()}`);

	if (Object.keys(context).length > 0) {
		lines.push("UI context:");
		addPromptField(lines, "appId", context.appId);
		addPromptField(lines, "appTitle", context.appTitle);
		addPromptField(lines, "currentPath", context.currentPath);
		addPromptField(lines, "surfaceId", context.surfaceId);
		addPromptField(lines, "surfaceTitle", context.surfaceTitle);
		addPromptField(lines, "surfaceActions", context.surfaceActions);
		addPromptField(lines, "surfaceContextSource", context.surfaceContextSource);
		addPromptField(lines, "surfaceDetails", context.surfaceDetails);
		addPromptField(lines, "surfaceSummary", context.surfaceSummary);
		addPromptField(lines, "userId", context.userId);
	}

	if (Object.keys(tools).length > 0) {
		lines.push("UI tools:");
		lines.push(stringifyInline(tools));
	}

	if (options.switchSummary?.trim()) {
		lines.push("Active project context:");
		lines.push(options.switchSummary.trim());
	}

	if (options.onboardingInstruction?.trim()) {
		lines.push("Session bootstrap instruction:");
		lines.push(options.onboardingInstruction.trim());
	}

	if (options.projectRuntimeSummary?.trim()) {
		lines.push("Project runtime bootstrap:");
		lines.push(options.projectRuntimeSummary.trim());
	}

	lines.push("Latest user message:");
	lines.push(latestUserMessage);
	lines.push("Use the active session for prior conversation context when the same backend agent session is reused.");
	return lines.join("\n");
}

function formatProjectRuntimeBootstrapError(result: ProjectRuntimeBootstrapResult): string {
	if (!isProjectRuntimeBootstrapFailure(result)) {
		return "Deterministic project runtime bootstrap did not return an error payload.";
	}
	const detail = result.stderr.trim() || result.stdout.trim() || result.error;
	return `Deterministic project runtime bootstrap failed during ${result.step}: ${detail}`;
}

function getProjectRuntimeToolName(step: ProjectRuntimeBootstrapEvent["step"]): string {
	switch (step) {
		case "sdk_status":
			return "runtime_mainsequence_project_sdk_status";
		case "build_local_venv":
			return "runtime_mainsequence_project_build_local_venv";
		case "resolve_venv":
			return "runtime_resolve_project_venv";
		case "activate_venv":
			return "runtime_activate_project_venv";
		case "uv_sync":
			return "runtime_uv_sync";
		case "read_venv_mainsequence":
			return "runtime_read_venv_mainsequence_version";
	}
}

function compactToolResultText(text: string, maxLength = 600): string {
	const trimmed = text.replace(/\s+/g, " ").trim();
	if (!trimmed) return "(no output)";
	if (trimmed.length <= maxLength) return trimmed;
	return `${trimmed.slice(0, maxLength - 3).trimEnd()}...`;
}

function buildProjectRuntimeToolArgs(event: ProjectRuntimeBootstrapEvent): string {
	return JSON.stringify({
		step: event.step,
		command: event.command,
		cwd: event.cwd,
		...(event.details ?? {}),
	});
}

function emitProjectRuntimeBootstrapEvent(
	ctx: RequestContext,
	event: ProjectRuntimeBootstrapEvent,
	toolCallIds: Map<ProjectRuntimeBootstrapEvent["step"], string>,
) {
	if (event.phase === "start") {
		ctx.runtimeToolCounter += 1;
		const toolCallId = `runtime_${event.step}_${ctx.runtimeToolCounter}`;
		toolCallIds.set(event.step, toolCallId);
		writeChunk(ctx, {
			type: "tool-call-start",
			id: toolCallId,
			toolCallId,
			toolName: getProjectRuntimeToolName(event.step),
		});
		writeChunk(ctx, {
			type: "tool-call-delta",
			toolCallId,
			argsText: buildProjectRuntimeToolArgs(event),
		});
		writeChunk(ctx, {
			type: "tool-call-end",
			toolCallId,
		});
		return;
	}

	const toolCallId = toolCallIds.get(event.step) ?? `runtime_${event.step}_unknown`;
	const resultText =
		event.phase === "success"
			? event.summary
			: `${event.summary} ${compactToolResultText(event.stderr || event.stdout || event.error, 400)}`;
	writeChunk(ctx, {
		type: "tool-result",
		toolCallId,
		result: {
			content: [
				{
					type: "text",
					text: compactToolResultText(resultText),
				},
			],
		},
	});
}

function runPendingProjectRuntimeBootstrap(
	ctx: RequestContext,
	options: {
		cwd: string;
		repoRoot: string | null;
		sessionKey: string;
		agentId: number;
		agentUniqueId: string;
		agentSessionId: number | null;
		threadId: string;
		startedAt: string | null;
		agentName: string;
		projectId: string | null;
		pendingOnboarding: boolean;
		switchSummary: string | null;
		sessionModelBinding: SessionModelBinding | null;
		sessionConfigOverrides: SessionConfigOverrides | null;
	},
): ProjectRuntimeBootstrapResult {
	const runtimeEnv = buildProjectScopedCheckoutEnv(process.env, {
		projectId: options.projectId,
		cwd: options.cwd,
	});
	const toolCallIds = new Map<ProjectRuntimeBootstrapEvent["step"], string>();
	const result = bootstrapProjectCoderRuntime({
		cwd: options.cwd,
		env: runtimeEnv,
		log: (message) => {
			console.log(`[astro-stream] ${message}`);
		},
		onEvent: (event) => {
			emitProjectRuntimeBootstrapEvent(ctx, event, toolCallIds);
		},
	});

	if (result.ok) {
		writeSessionMetadata(options.sessionKey, {
			agentId: options.agentId,
			agentUniqueId: options.agentUniqueId,
			agentSessionId: options.agentSessionId,
			threadId: options.threadId,
			startedAt: options.startedAt,
			agentName: options.agentName,
			projectId: options.projectId,
			cwd: options.cwd,
			repoRoot: options.repoRoot,
			pendingOnboarding: options.pendingOnboarding,
			pendingRuntimeBootstrap: false,
			switchSummary: options.switchSummary,
			projectRuntime: result.snapshot,
			sessionModelBinding: options.sessionModelBinding,
			sessionConfigOverrides: options.sessionConfigOverrides,
		});
	}

	return result;
}

function markConversationStoreCompleted(ctx: RequestContext, finishReason = "session_switch") {
	try {
		ctx.conversationStore.recordStreamChunkSync(
			attachAgentId(
				{
					type: "finish",
					finishReason,
				},
				ctx.agentId,
			),
		);
		ctx.conversationStore.recordStreamDoneSync();
	} catch {
		// ignore local history finalization failures during session handoff
	}
}

function createContinuationContextFromSwitch(
	parentCtx: RequestContext,
	options: {
		sessionKey: string;
		agentId: number;
		agentUniqueId: string;
		agentSessionId: number;
		agentName: string;
		startedAt: string;
		initialTask: string | null;
	},
): RequestContext | null {
	try {
		const conversationStore = createConversationStore({
			sessionDir,
			sessionKey: options.sessionKey,
			threadId: parentCtx.threadId,
			agentName: options.agentName,
			agentId: options.agentId,
			agentSessionId: options.agentSessionId,
			startedAt: options.startedAt,
		});
		if (options.initialTask) {
			conversationStore.recordUserMessageSync({ text: options.initialTask });
		}
		return {
			...parentCtx,
			sessionKey: options.sessionKey,
			agentId: options.agentId,
			agentUniqueId: options.agentUniqueId,
			agentSessionId: options.agentSessionId,
			agentName: options.agentName,
			conversationStore,
			logState: {
				reasoning: null,
				text: null,
				toolCalls: new Map(),
			},
			toolCallIds: new Map(),
			switching: false,
			piProcess: null,
			finished: false,
			responseProvider: null,
			responseModel: null,
			piAssistantTextSeen: false,
			lastAssistantFinishReason: null,
			lastAssistantUsage: undefined,
		};
	} catch (error) {
		console.error(
			`[astro-stream] failed to initialize switched project session history for session=${options.sessionKey}: ${
				error instanceof Error ? error.message : String(error)
			}`,
		);
		return null;
	}
}

function stopActivePiProcess(ctx: RequestContext) {
	if (!ctx.piProcess) return;
	try {
		ctx.piProcess.kill("SIGTERM");
	} catch {
		// ignore process termination failures
	}
	ctx.piProcess = null;
}

async function handleProjectSessionSwitch(ctx: RequestContext, request: SessionSwitchRequest) {
	if (ctx.finished || ctx.switching) return;
	ctx.switching = true;

	if (!isExistingDirectory(request.cwd)) {
		writeChunk(ctx, {
			type: "error",
			error: `switch_project_session requires an existing checked-out project directory. Invalid cwd: ${request.cwd}`,
		});
		writeDone(ctx);
		stopActivePiProcess(ctx);
		return;
	}

	const created = await createBackendRuntimeSession({
		agentName: request.agentName,
		userId: ctx.userId,
		threadId: ctx.threadId,
		projectId: request.projectId,
		cwd: request.cwd,
		externalSessionId: ctx.sessionKey,
		sessionMetadata: {
			switched_from_agent: ctx.agentName,
			switched_from_session_key: ctx.sessionKey,
		},
		pendingOnboarding: !request.initialTask,
		pendingRuntimeBootstrap: true,
		switchSummary: request.summary ?? null,
		sessionModelBinding: ctx.sessionModelBinding,
		sessionConfigOverrides: ctx.sessionConfigOverrides,
	});

	if (!created.ok) {
		const errorMessage = "error" in created ? created.error : "Failed to create project session.";
		writeChunk(ctx, {
			type: "error",
			error: errorMessage,
		});
		writeDone(ctx);
		stopActivePiProcess(ctx);
		return;
	}

	emitAssistantText(ctx, buildProjectSwitchAnnouncement(request), ctx.agentId);
	writeChunkWithAgentId(
		ctx,
		{
			type: "session_switch",
			session_switch: {
				from_agent_name: ctx.agentName,
				to_agent_name: request.agentName,
				project_id: request.projectId,
				cwd: request.cwd,
				thread_id: ctx.threadId,
				agent_id: created.agentId,
				agent_unique_id: created.agentUniqueId,
				agent_session_id: created.agentSessionId,
				session_key: created.sessionKey,
				runtime_session_id: created.sessionKey,
				initial_task: request.initialTask,
				summary: request.summary,
			},
		},
		created.agentId,
	);
	stopActivePiProcess(ctx);
	markConversationStoreCompleted(ctx);

	const coderCtx = createContinuationContextFromSwitch(ctx, {
		sessionKey: created.sessionKey,
		agentId: created.agentId,
		agentUniqueId: created.agentUniqueId,
		agentSessionId: created.agentSessionId,
		agentName: request.agentName,
		startedAt: created.startedAt,
		initialTask: request.initialTask,
	});

	if (!coderCtx) {
		writeChunk(ctx, {
			type: "error",
			error: "Failed to initialize the project coding session after switching.",
		});
		writeDone(ctx);
		return;
	}

	writeChunkWithAgentId(
		coderCtx,
		{
			type: "new_session",
			new_session: {
				agent_session_id: created.agentSessionId,
				session_key: created.sessionKey,
				agent_unique_id: created.agentUniqueId,
				thread_id: ctx.threadId,
				agent_id: created.agentId,
			},
		},
		created.agentId,
	);

	emitAssistantText(coderCtx, buildProjectRuntimeBootstrapAnnouncement(request.summary), created.agentId);
	const runtimeBootstrapResult = runPendingProjectRuntimeBootstrap(coderCtx, {
		cwd: request.cwd,
		repoRoot: resolveGitRepoRoot(request.cwd),
		sessionKey: created.sessionKey,
		agentId: created.agentId,
		agentUniqueId: created.agentUniqueId,
		agentSessionId: created.agentSessionId,
		threadId: ctx.threadId,
		startedAt: created.startedAt,
		agentName: request.agentName,
			projectId: request.projectId,
			pendingOnboarding: !request.initialTask,
			switchSummary: request.summary ?? null,
			sessionModelBinding: coderCtx.sessionModelBinding,
			sessionConfigOverrides: coderCtx.sessionConfigOverrides,
		});
	if (!runtimeBootstrapResult.ok) {
		writeChunk(coderCtx, {
			type: "error",
			error: formatProjectRuntimeBootstrapError(runtimeBootstrapResult),
		});
		writeDone(coderCtx);
		return;
	}

	if (!request.initialTask) {
		emitAssistantText(coderCtx, buildProjectRuntimeReadyAnnouncement(request.summary), created.agentId);
		writeChunk(coderCtx, { type: "finish", finishReason: "stop" });
		writeDone(coderCtx);
		return;
	}

	const agentConfig = loadSpecialistAgent(request.agentName);
	if (!agentConfig) {
		writeChunk(coderCtx, {
			type: "error",
			error: "Could not load the mainsequence-project-coder specialist prompt after switching sessions.",
		});
		writeDone(coderCtx);
		return;
	}

	const projectRuntime = runtimeBootstrapResult.snapshot;
	const prompt = buildPrompt(
		ctx.system,
		request.initialTask,
		ctx.uiContext,
		ctx.uiTools,
		{
			switchSummary: request.summary ?? null,
			projectRuntimeSummary: formatProjectRuntimeSummary(projectRuntime),
		},
	);
	runPiPrompt(prompt, coderCtx, {
		cwd: request.cwd,
		projectId: request.projectId,
		agentConfig,
		envOverrides: buildActivatedProjectEnv(process.env, projectRuntime, {
			projectId: request.projectId,
			cwd: request.cwd,
		}),
	});
}

function handleAssistantDelta(ctx: RequestContext, evt: any) {
	updateResponseModelFromMessage(ctx, evt?.message ?? evt?.partial);
	switch (evt.type) {
		case "thinking_start": {
			ctx.reasoningCounter += 1;
			const id = `r${ctx.reasoningCounter}`;
			writeChunk(ctx, { type: "reasoning-start", id });
			return;
		}
		case "thinking_delta":
			if (typeof evt.delta === "string") {
				writeChunk(ctx, { type: "reasoning-delta", delta: evt.delta });
			}
			return;
		case "thinking_end":
			writeChunk(ctx, { type: "reasoning-end" });
			return;
		case "text_start": {
			ctx.piAssistantTextSeen = true;
			ctx.textCounter += 1;
			const id = `t${ctx.textCounter}`;
			writeChunk(ctx, { type: "text-start", id });
			return;
		}
		case "text_delta":
			if (typeof evt.delta === "string") {
				ctx.piAssistantTextSeen = true;
				writeChunk(ctx, { type: "text-delta", textDelta: evt.delta });
			}
			return;
		case "text_end":
			writeChunk(ctx, { type: "text-end" });
			return;
		case "toolcall_start": {
			const idx = Number(evt.contentIndex ?? 0);
			const toolCall = evt.toolCall ?? evt.partial?.content?.[idx];
			const toolCallId = toolCall?.id ?? `call_${idx}`;
			const toolName = toolCall?.name ?? toolCall?.toolName ?? "unknown";
			ctx.toolCallIds.set(idx, { toolCallId, toolName });
			writeChunk(ctx, { type: "tool-call-start", id: toolCallId, toolCallId, toolName });
			return;
		}
		case "toolcall_delta": {
			if (typeof evt.delta === "string") {
				const idx = Number(evt.contentIndex ?? 0);
				const toolCallId = ctx.toolCallIds.get(idx)?.toolCallId;
				writeChunk(ctx, { type: "tool-call-delta", argsText: evt.delta, toolCallId });
			}
			return;
		}
		case "toolcall_end": {
			const idx = Number(evt.contentIndex ?? 0);
			const toolCallId = ctx.toolCallIds.get(idx)?.toolCallId;
			writeChunk(ctx, { type: "tool-call-end", toolCallId });
			return;
		}
		case "done": {
			const finishReason = evt.reason ?? "stop";
			const mappedUsage = mapUsageSummary(evt.message?.usage ?? evt.partial?.usage) ?? ctx.lastAssistantUsage;
			writeChunk(ctx, { type: "finish", finishReason, usage: mappedUsage });
			writeDone(ctx);
			return;
		}
		case "error": {
			const reason = evt.reason ?? "error";
			writeChunk(ctx, { type: "error", error: reason });
			writeDone(ctx);
			return;
		}
		default:
			return;
	}
}

function handleAssistantMessageEnd(ctx: RequestContext, message: unknown) {
	if (!message || typeof message !== "object") return;
	if ((message as { role?: unknown }).role !== "assistant") return;

	updateResponseModelFromMessage(ctx, message);

	const stopReason = normalizeLogString((message as { stopReason?: unknown }).stopReason);
	if (stopReason) ctx.lastAssistantFinishReason = stopReason;

	const usage = mapUsageSummary((message as { usage?: unknown }).usage);
	if (usage) ctx.lastAssistantUsage = usage;

	if (ctx.piAssistantTextSeen) return;

	const text = extractTextParts((message as { content?: unknown }).content).trim();
	if (!text) return;

	emitAssistantText(ctx, text);
}

function runPiPrompt(
	prompt: string,
	ctx: RequestContext,
	options: {
		cwd: string;
		projectId: string | null;
		agentConfig: AgentConfig | null;
		envOverrides?: NodeJS.ProcessEnv;
	},
) {
	mkdirSync(sessionDir, { recursive: true });
	const sessionPath = getSessionPath(ctx.sessionKey);
	const args = ["--mode", "json", "--session", sessionPath];
	let promptPath: string | null = null;
	const boundModelArg = buildPiModelArgument(ctx.sessionModelBinding);
	const scopedPiAgentDir = ensureSessionScopedPiAgentDir({
		sessionKey: ctx.sessionKey,
		sessionConfigOverrides: ctx.sessionConfigOverrides,
		env: {
			...process.env,
			...(options.envOverrides ?? {}),
		},
	});
	ctx.piAssistantTextSeen = false;
	ctx.lastAssistantFinishReason = null;
	ctx.lastAssistantUsage = undefined;

	if (options.agentConfig) {
		if (boundModelArg) {
			args.push("--model", boundModelArg);
		} else if (options.agentConfig.model) {
			args.push("--model", options.agentConfig.model);
		}
		const builtInTools = options.agentConfig.tools?.filter((tool) => PI_BUILT_IN_TOOL_NAMES.has(tool)) ?? [];
		if (builtInTools.length) args.push("--tools", builtInTools.join(","));
		promptPath = writePromptToTempFile(options.agentConfig.name, options.agentConfig.systemPrompt);
		args.push("--append-system-prompt", promptPath);
	} else if (boundModelArg) {
		args.push("--model", boundModelArg);
	}

	args.push(prompt);
	const child = spawn("pi", args, {
		cwd: options.cwd,
		env: buildMainsequenceStoredAuthEnv({
			...process.env,
			...buildSessionModelEnv(ctx.sessionModelBinding, process.env),
			...(scopedPiAgentDir ? { PI_CODING_AGENT_DIR: scopedPiAgentDir } : {}),
			...(options.envOverrides ?? {}),
			ASTRO_TELEMETRY: "0",
			ASTRO_MAINSEQUENCE_USER_ID: ctx.userId,
			...(options.agentConfig
				? {
						ASTRO_SUBAGENT_CHILD: "1",
						ASTRO_ACTIVE_SPECIALIST: options.agentConfig.name,
				  }
				: {}),
			...(options.projectId ? { ASTRO_TARGET_PROJECT_ID: options.projectId } : {}),
		}),
		stdio: ["ignore", "pipe", "pipe"],
	});
	ctx.piProcess = child;

	const stdout = createInterface({ input: child.stdout });
	stdout.on("line", (line) => {
		let parsed: any;
		try {
			parsed = JSON.parse(line);
		} catch {
			if (logTraffic) {
				console.log(
					`[astro-stream] NONJSON agent=${ctx.agentName} session=${ctx.sessionKey} thread=${ctx.threadId}: ${line}`,
				);
			}
			return;
		}

		if (ctx.finished || ctx.switching) return;

		if (parsed?.type === "message_start") {
			if (parsed.message?.role !== "assistant") return;
			ctx.piAssistantTextSeen = false;
			updateResponseModelFromMessage(ctx, parsed.message);
			return;
		}

		if (parsed?.type === "message_update") {
			if (parsed.message?.role !== "assistant") return;
			updateResponseModelFromMessage(ctx, parsed.message);
			const evt = parsed.assistantMessageEvent;
			if (!evt || typeof evt.type !== "string") return;
			handleAssistantDelta(ctx, evt);
			return;
		}

		if (parsed?.type === "message_end") {
			handleAssistantMessageEnd(ctx, parsed.message);
			return;
		}

		if (parsed?.type === "tool_execution_end") {
			const sessionSwitchRequest = parseSessionSwitchRequest(parsed.result);
			if (sessionSwitchRequest) {
				void handleProjectSessionSwitch(ctx, sessionSwitchRequest);
				return;
			}
			const toolCallId = parsed.toolCallId;
			if (typeof toolCallId === "string") {
				writeChunk(ctx, { type: "tool-result", toolCallId, result: parsed.result });
			}
			return;
		}
	});

	const stderr = createInterface({ input: child.stderr });
	stderr.on("line", (line) => {
		if (ctx.switching) return;
		if (logTraffic) {
			console.log(
				`[astro-stream] STDERR agent=${ctx.agentName} session=${ctx.sessionKey} thread=${ctx.threadId}: ${line}`,
			);
		}
		if (!ctx.finished) {
			writeChunk(ctx, { type: "error", error: line });
			writeDone(ctx);
		}
	});

	child.on("exit", (code, signal) => {
		ctx.piProcess = null;
		cleanupPromptFile(promptPath);
		if (ctx.switching) return;
		if (ctx.finished) return;
		if (signal || (typeof code === "number" && code !== 0)) {
			const reason = signal ? `Process exited with signal ${signal}.` : `Process exited with code ${code}.`;
			writeChunk(ctx, { type: "error", error: reason });
			writeDone(ctx);
			return;
		}

		writeChunk(ctx, {
			type: "finish",
			finishReason: ctx.lastAssistantFinishReason ?? "stop",
			usage: ctx.lastAssistantUsage,
		});
		writeDone(ctx);
	});

	child.on("error", (error) => {
		ctx.piProcess = null;
		cleanupPromptFile(promptPath);
		if (ctx.switching) return;
		if (!ctx.finished) {
			writeChunk(ctx, { type: "error", error: error.message });
			writeDone(ctx);
		}
	});
}

const server = createServer(async (req, res) => {
	const normalizedReqUrl = normalizePossiblyEncodedChatUrl(req.url);
	const url = new URL(normalizedReqUrl, `http://${req.headers.host ?? host}`);
	registerHttpAccessLog(req, res, url);

	if (req.method === "OPTIONS") {
		if (!applyCorsHeaders(req, res, { preflight: true })) {
			writeCorsOriginNotAllowed(req, res, url);
			return;
		}
		res.writeHead(204);
		res.end();
		return;
	}

	if (!applyCorsHeaders(req, res)) {
		writeCorsOriginNotAllowed(req, res, url);
		return;
	}

	if (req.method === "GET" && url.pathname === "/health") {
		json(res, 200, { ok: true });
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/chat/get_available_models") {
		try {
			const availableModels = await collectAvailableModels({ env: process.env });
			json(res, 200, availableModels);
		} catch (error) {
			const message =
				error instanceof Error ? error.message : "Unknown available-model discovery failure.";
			json(res, 500, {
				error: "available_models_unavailable",
				message,
			});
		}
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/models/catalog") {
		try {
			const modelCatalog = collectModelCatalog({ env: process.env });
			json(res, 200, modelCatalog);
		} catch (error) {
			const message = error instanceof Error ? error.message : "Unknown model catalog failure.";
			json(res, 500, {
				error: "model_catalog_unavailable",
				message,
			});
		}
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/model-providers") {
		json(res, 200, getModelProviderAuthResponse(process.env));
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/storage/usage") {
		try {
			json(res, 200, readStorageUsage(process.env));
		} catch (error) {
			const message = error instanceof Error ? error.message : "Unknown storage usage failure.";
			json(res, 500, {
				error: "storage_usage_unavailable",
				message,
			});
		}
		return;
	}

	const modelProviderSignInAttemptPath = matchModelProviderSignInAttemptPath(url.pathname);
	if (
		modelProviderSignInAttemptPath &&
		((req.method === "GET" && modelProviderSignInAttemptPath.action === "status") ||
			(req.method === "POST" &&
				(modelProviderSignInAttemptPath.action === "manual" ||
					modelProviderSignInAttemptPath.action === "cancel")))
	) {
		if (req.method === "GET") {
			const result = getModelProviderSignInAttempt(
				modelProviderSignInAttemptPath.provider,
				modelProviderSignInAttemptPath.attemptId,
				process.env,
			);
			if (result.ok === false) {
				json(res, result.statusCode, {
					error: result.error,
					message: result.message,
				});
				return;
			}
			json(res, 200, {
				version: 1,
				attempt: result.attempt,
			});
			return;
		}

		if (modelProviderSignInAttemptPath.action === "manual") {
			let body: any;
			try {
				body = await parseJson(req);
			} catch {
				badRequest(res, "Invalid JSON body.");
				return;
			}

			const manualInput = typeof body?.input === "string" ? body.input : "";
			const result = submitModelProviderSignInManualInput(
				modelProviderSignInAttemptPath.provider,
				modelProviderSignInAttemptPath.attemptId,
				manualInput,
				process.env,
			);
			if (result.ok === false) {
				json(res, result.statusCode, {
					error: result.error,
					message: result.message,
					...("attempt" in result && result.attempt ? { attempt: result.attempt } : {}),
				});
				return;
			}
			json(res, result.statusCode, {
				ok: true,
				provider: modelProviderSignInAttemptPath.provider,
				attempt: result.attempt,
			});
			return;
		}

		const result = cancelModelProviderSignInAttempt(
			modelProviderSignInAttemptPath.provider,
			modelProviderSignInAttemptPath.attemptId,
			process.env,
		);
		if (result.ok === false) {
			json(res, result.statusCode, {
				error: result.error,
				message: result.message,
				...(result.attempt ? { attempt: result.attempt } : {}),
			});
			return;
		}
		json(res, result.statusCode, {
			ok: true,
			provider: modelProviderSignInAttemptPath.provider,
			attempt: result.attempt,
		});
		return;
	}

	const modelProviderAuthAction = req.method === "POST" ? matchModelProviderAuthActionPath(url.pathname) : null;
	if (req.method === "POST" && modelProviderAuthAction) {
		const result =
			modelProviderAuthAction.action === "signin"
				? startModelProviderSignIn(modelProviderAuthAction.provider, process.env)
				: signOffModelProvider(modelProviderAuthAction.provider, process.env);
		if (result.ok === false) {
			json(res, result.statusCode, {
				error: result.error,
				message: result.message,
				...("attempt" in result && result.attempt ? { attempt: result.attempt } : {}),
			});
			return;
		}
		json(res, result.statusCode, result);
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/chat/session-model") {
		const sessionKey = normalizeRuntimeSessionId(
			url.searchParams.get("sessionId") ??
				url.searchParams.get("runtime_session_id") ??
				url.searchParams.get("runtimeSessionId"),
		);
		if (!sessionKey) {
			badRequest(res, "Missing sessionId.");
			return;
		}

		const metadata = readSessionMetadata(sessionKey);
		if (!metadata) {
			json(res, 404, {
				error: sessionExists(sessionKey) ? "session_metadata_missing" : "session_not_found",
				message: sessionExists(sessionKey)
					? "No session metadata is available for the provided session."
					: "No local session found for the provided session id.",
			});
			return;
		}

		json(res, 200, {
			sessionId: sessionKey,
			model: metadata.sessionModelBinding,
		});
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/chat/session-insights") {
		const sessionKey = normalizeRuntimeSessionId(
			url.searchParams.get("sessionId") ??
				url.searchParams.get("runtime_session_id") ??
				url.searchParams.get("runtimeSessionId"),
		);
		if (!sessionKey) {
			badRequest(res, "Missing sessionId.");
			return;
		}

		const metadata = readSessionMetadata(sessionKey);
		if (!metadata) {
			json(res, 404, {
				error: sessionExists(sessionKey) ? "session_metadata_missing" : "session_not_found",
				message: sessionExists(sessionKey)
					? "No session metadata is available for the provided session."
					: "No local session found for the provided session id.",
			});
			return;
		}

		const insights = readSessionInsights({
			sessionDir,
			sessionKey,
			metadata,
		});
		if (!insights) {
			json(res, 404, {
				error: "session_insights_unavailable",
				message: "No Pi session file is available for the provided session.",
			});
			return;
		}

		json(res, 200, {
			version: 1,
			session: insights.usage.session,
			model: insights.context.model,
			usage: insights.usage.usage,
			context: insights.context.context,
			lastTurn: insights.usage.lastTurn,
			config: insights.config,
			editable: insights.editable,
			info: insights.info,
		});
		return;
	}

	if (req.method === "PATCH" && url.pathname === "/api/chat/session-config") {
		let body: any;
		try {
			body = await parseJson(req);
		} catch {
			badRequest(res, "Invalid JSON body.");
			return;
		}

		const sessionKey = normalizeRuntimeSessionId(
			body?.sessionId ?? body?.runtime_session_id ?? body?.runtimeSessionId,
		);
		if (!sessionKey) {
			badRequest(res, "Missing sessionId.");
			return;
		}

		const metadata = readSessionMetadata(sessionKey);
		if (!metadata) {
			json(res, 404, {
				error: sessionExists(sessionKey) ? "session_metadata_missing" : "session_not_found",
				message: sessionExists(sessionKey)
					? "No session metadata is available for the provided session."
					: "No local session found for the provided session id.",
			});
			return;
		}

		const runtimeLimits = resolveSessionRuntimeLimits(metadata.sessionModelBinding);
		const patchResult = validateSessionConfigPatch({
			body,
			currentOverrides: metadata.sessionConfigOverrides,
			contextWindow: runtimeLimits.contextWindow,
		});
		if (patchResult.ok === false) {
			json(res, patchResult.statusCode, {
				error: patchResult.error,
				message: patchResult.message,
			});
			return;
		}

		writeSessionMetadata(sessionKey, {
			...metadata,
			sessionConfigOverrides: patchResult.overrides,
		});

		json(res, 200, {
			ok: true,
			sessionId: sessionKey,
			updatedAt: new Date().toISOString(),
			updatedFields: patchResult.updatedFields,
		});
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/chat/history") {
		const sessionKey = normalizeRuntimeSessionId(
			url.searchParams.get("sessionId") ??
				url.searchParams.get("runtime_session_id") ??
				url.searchParams.get("runtimeSessionId"),
		);
		if (!sessionKey) {
			badRequest(res, "Missing sessionId.");
			return;
		}

		const history = readConversationHistorySync({ sessionDir, sessionKey });
		if (!history) {
			json(res, 404, {
				error: sessionExists(sessionKey) ? "history_not_available" : "session_not_found",
				message: sessionExists(sessionKey)
					? "No compact history snapshot is available for the provided session."
					: "No local session found for the provided session id.",
			});
			return;
		}

		json(res, 200, history);
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/chat/session-tools") {
		const sessionKey = normalizeRuntimeSessionId(
			url.searchParams.get("sessionId") ??
				url.searchParams.get("runtime_session_id") ??
				url.searchParams.get("runtimeSessionId"),
		);
		if (!sessionKey) {
			badRequest(res, "Missing sessionId.");
			return;
		}

		const metadata = readSessionMetadata(sessionKey);
		if (!metadata) {
			json(res, 404, {
				error: sessionExists(sessionKey) ? "session_metadata_missing" : "session_not_found",
				message: sessionExists(sessionKey)
					? "No session metadata is available for the provided session."
					: "No local session found for the provided session id.",
			});
			return;
		}

		json(res, 200, {
			version: 1,
			session: {
				sessionId: sessionKey,
				agentName: metadata.agentName,
				agentId: metadata.agentId,
				agentUniqueId: metadata.agentUniqueId,
				agentSessionId: metadata.agentSessionId,
				projectId: metadata.projectId,
			},
			available_tools: buildAvailableSessionTools(sessionKey, metadata),
		});
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/chat/diff") {
		const sessionKey = normalizeRuntimeSessionId(
			url.searchParams.get("sessionId") ??
				url.searchParams.get("runtime_session_id") ??
				url.searchParams.get("runtimeSessionId"),
		);
		if (!sessionKey) {
			badRequest(res, "Missing sessionId.");
			return;
		}

		const metadata = readSessionMetadata(sessionKey);
		if (!metadata) {
			json(res, 404, {
				error: sessionExists(sessionKey) ? "session_metadata_missing" : "session_not_found",
				message: sessionExists(sessionKey)
					? "No session metadata is available for the provided session."
					: "No local session found for the provided session id.",
			});
			return;
		}

		if (metadata.agentName !== "mainsequence-project-coder") {
			json(res, 409, {
				error: "diff_not_available",
				message: "Repo diff snapshots are only available for mainsequence-project-coder sessions.",
			});
			return;
		}

		const resolvedRepoRoot = resolveSessionRepoRoot(sessionKey, metadata);
		if (!resolvedRepoRoot || !isExistingDirectory(resolvedRepoRoot)) {
			json(res, 409, {
				error: "diff_not_available",
				message: "The frozen project repo root is unavailable for this session.",
			});
			return;
		}

		const diffSnapshot = buildRepoDiffSnapshot(resolvedRepoRoot);
		if (!diffSnapshot.ok) {
			json(res, 409, {
				error: "diff_not_available",
				message: diffSnapshot.error || "Failed to build the repo diff snapshot for this session.",
			});
			return;
		}

		json(res, 200, {
			version: 1,
			session: {
				sessionId: sessionKey,
				agentName: metadata.agentName,
				agentId: metadata.agentId,
				agentUniqueId: metadata.agentUniqueId,
				agentSessionId: metadata.agentSessionId,
				projectId: metadata.projectId,
			},
			diff: {
				base: diffSnapshot.base,
				hasChanges: diffSnapshot.files.length > 0,
				patch: diffSnapshot.patch,
				files: diffSnapshot.files,
			},
		});
		return;
	}

	if (url.pathname === "/api/chat" && req.method === "GET") {
		json(res, 200, {
			ok: true,
			message: "Use POST /api/chat with assistant-ui data-stream payload.",
		});
		return;
	}

	if (req.method !== "POST" || url.pathname !== "/api/chat") {
		notFound(res);
		return;
	}

	let body: any;
	try {
		body = await parseJson(req);
	} catch {
		badRequest(res, "Invalid JSON.");
		return;
	}

	if (logRequestBodies) {
		console.log(`[astro-stream] IN ${url.pathname}: ${JSON.stringify(body)}`);
	}

	const messages = Array.isArray(body.messages) ? body.messages : [];
	if (!messages.length) {
		badRequest(res, "Missing messages array.");
		return;
	}

	const latestUserMessage = extractLatestUserMessage(messages);
	if (!latestUserMessage) {
		badRequest(res, "Missing latest user message.");
		return;
	}

	const requestedThreadId =
		typeof body.threadId === "string" && body.threadId.trim() ? body.threadId.trim() : null;
	if (shouldUseMockResponse(latestUserMessage)) {
		writeMockStreamResponse(req, res, {
			threadId: requestedThreadId ?? randomUUID(),
			latestUserMessage,
		});
		return;
	}

	if (!(await ensureRequestCliAuth(res)).ok) {
		return;
	}

	const tools = body.tools === undefined ? {} : body.tools;
	if (!isPlainObject(tools)) {
		badRequest(res, "`tools` must be an object.");
		return;
	}

	const context = body.context === undefined ? {} : body.context;
	if (!isPlainObject(context)) {
		badRequest(res, "`context` must be an object.");
		return;
	}

	const requestedAgentName = normalizeAgentName(body.agentName);
	if (!requestedAgentName) {
		badRequest(res, "Missing agentName.");
		return;
	}
	if (!ALLOWED_AGENTS.has(requestedAgentName)) {
		json(res, 400, { error: "unknown_agent", message: `Unknown agent "${requestedAgentName}".` });
		return;
	}
	let agentName = requestedAgentName;

		const userId = resolveUserId(body.userId);
		if (!userId) {
			badRequest(res, "Missing or invalid userId.");
			return;
		}
	let newChat = body.newChat === true;
	if (body.newChat !== undefined && typeof body.newChat !== "boolean") {
		badRequest(res, "`newChat` must be a boolean.");
		return;
	}
	const explicitRuntimeSessionId = normalizeRuntimeSessionId(
		(body.runtime_session_id as unknown) ?? (body.runtimeSessionId as unknown),
	);
	const runtimeSessionFromRequest = !!explicitRuntimeSessionId;
	const runtimeSessionId = explicitRuntimeSessionId;
	if (runtimeSessionFromRequest) {
		newChat = false;
	}
	if (!newChat && !runtimeSessionId) {
		json(res, 400, {
			error: "missing_runtime_session_id",
			message: "runtime_session_id is required when newChat is false.",
		});
		return;
	}
	const registrationRequired = shouldRegisterAgents(process.env);
	let hydratedBackendSession: HydratedBackendOrchestratorSession | null = null;
	const localSessionExists = runtimeSessionId ? sessionExists(runtimeSessionId) : false;
	if (
		runtimeSessionId &&
		!localSessionExists &&
		registrationRequired &&
		agentName === "astro-orchestrator"
	) {
		logStructuredEvent({
			component: "astro-stream",
			event: "backend_session_hydration_local_session_missing",
			message: "Local session files were missing, so Astro is attempting backend orchestrator hydration.",
			data: {
				runtimeSessionId,
				agentName,
				userId,
				requestedThreadId,
			},
		});
		const hydrationResult = await attachHydratedBackendSession({
			runtimeSessionId,
			userId,
			requestedThreadId,
			log: undefined,
		});
		if (hydrationResult.ok === false) {
			json(res, hydrationResult.statusCode, {
				error: hydrationResult.error,
				message: hydrationResult.message,
			});
			return;
		}
		hydratedBackendSession = hydrationResult.hydrated;
		logStructuredEvent({
			component: "astro-stream",
			event: "backend_session_hydration_attached",
			message: "Astro attached the hydrated backend orchestrator session to the current request.",
			data: {
				runtimeSessionId,
				threadId: hydratedBackendSession.metadata.threadId,
				agentId: hydratedBackendSession.agentId,
				agentSessionId: hydratedBackendSession.metadata.agentSessionId,
			},
		});
	}
	if (runtimeSessionId && !localSessionExists && !hydratedBackendSession) {
		json(res, 409, {
			error: "session_not_found",
			message: "No local session found for the provided runtime_session_id.",
		});
		return;
	}
	let existingSessionMetadata = runtimeSessionId ? readSessionMetadata(runtimeSessionId) : null;
	if (!existingSessionMetadata && hydratedBackendSession) {
		existingSessionMetadata = hydratedBackendSession.metadata;
	}
	if (!newChat && runtimeSessionId && !existingSessionMetadata) {
		json(res, 409, {
			error: "session_metadata_missing",
			message: "No session metadata found for the provided runtime_session_id.",
		});
		return;
	}
	if (existingSessionMetadata?.agentName) {
		if (existingSessionMetadata.agentName !== agentName) {
			json(res, 409, {
				error: "session_mismatch",
				message: "runtime_session_id does not match the requested agent.",
			});
			return;
		}
	}

	if (body.model !== undefined && body.model !== null && !isPlainObject(body.model)) {
		badRequest(res, "`model` must be an object or null.");
		return;
	}

	let sessionModelBinding = existingSessionMetadata?.sessionModelBinding ?? null;
	if (body.model === null) {
		sessionModelBinding = null;
	} else if (isPlainObject(body.model)) {
		const resolvedModelBinding = await resolveSessionModelBinding(body.model, { env: process.env });
		if (resolvedModelBinding.ok === false) {
			json(res, resolvedModelBinding.statusCode, {
				error: resolvedModelBinding.error,
				message: resolvedModelBinding.message,
			});
			return;
		}
		sessionModelBinding = resolvedModelBinding.binding;
	}

	if (sessionModelBinding && !isProviderUsableForExecution(sessionModelBinding.provider, process.env)) {
		json(res, 409, {
			error: "provider_not_authenticated",
			message: `The selected model provider "${sessionModelBinding.provider}" is not currently authenticated.`,
		});
		return;
	}

	const requestedProjectId = normalizeProjectId(body.projectId);
	const requestedCwd = normalizeProjectCwd(body.cwd);
	if (
		!newChat &&
		agentName === "mainsequence-project-coder" &&
		existingSessionMetadata?.projectId &&
		requestedProjectId &&
		requestedProjectId !== existingSessionMetadata.projectId
	) {
		json(res, 409, {
			error: "session_mismatch",
			message: "runtime_session_id does not match the requested projectId.",
		});
		return;
	}
	if (
		!newChat &&
		agentName === "mainsequence-project-coder" &&
		existingSessionMetadata?.cwd &&
		requestedCwd &&
		requestedCwd !== existingSessionMetadata.cwd
	) {
		json(res, 409, {
			error: "session_mismatch",
			message: "runtime_session_id does not match the requested cwd.",
		});
		return;
	}

	const projectId =
		agentName === "mainsequence-project-coder"
			? requestedProjectId ?? existingSessionMetadata?.projectId ?? null
			: null;
	const agentCwd =
		agentName === "mainsequence-project-coder"
			? requestedCwd ?? existingSessionMetadata?.cwd ?? null
			: repoRoot;
	const agentConfig = agentName === "mainsequence-project-coder" ? loadSpecialistAgent(agentName) : null;
	if (agentName === "mainsequence-project-coder") {
		if (!projectId) {
			json(res, newChat ? 400 : 409, {
				error: "missing_project_id",
				message: "mainsequence-project-coder requires projectId.",
			});
			return;
		}
		if (!agentCwd) {
			json(res, newChat ? 400 : 409, {
				error: "missing_cwd",
				message: "mainsequence-project-coder requires cwd.",
			});
			return;
		}
		if (!isExistingDirectory(agentCwd)) {
			json(res, newChat ? 400 : 409, {
				error: "invalid_cwd",
				message: "mainsequence-project-coder requires cwd to be an existing project directory.",
			});
			return;
		}
		if (!agentConfig) {
			json(res, 500, {
				error: "agent_prompt_not_found",
				message: "Could not load the mainsequence-project-coder specialist prompt.",
			});
			return;
		}
	}

	let projectRuntime: ProjectRuntimeSnapshot | null = existingSessionMetadata?.projectRuntime ?? null;
	const system = typeof body.system === "string" ? body.system : undefined;
	const shouldRunPendingOnboarding =
		agentName === "mainsequence-project-coder" && existingSessionMetadata?.pendingOnboarding === true;
	let pendingRuntimeBootstrap =
		agentName === "mainsequence-project-coder" &&
		(newChat || existingSessionMetadata?.pendingRuntimeBootstrap === true);
	const switchSummary = existingSessionMetadata?.switchSummary ?? null;

	const threadId = existingSessionMetadata?.threadId ?? requestedThreadId ?? randomUUID();
	if (!registrationRequired) {
		json(res, 503, {
			error: "agent_registration_disabled",
			message: "BUILD_AGENTS_IN_BACKEND must be enabled for session creation.",
		});
		return;
	}
	let agentId: number | null = null;
	let agentUniqueId: string | null = null;
	if (hydratedBackendSession) {
		agentId = hydratedBackendSession.agentId;
		agentUniqueId = hydratedBackendSession.agentUniqueId;
	} else {
		const registration = await registerMainsequenceAgent({
			agentName,
			agentRole: agentName === "astro-orchestrator" ? "orchestrator" : "specialist",
			cwd: agentCwd ?? repoRoot,
			projectId,
			userId,
			log: (message) => {
				console.log(`[astro-stream] ${message}`);
			},
		});

		if (!registration.agentId) {
			logAgentResolutionFailure({
				threadId,
				newChat,
				error: registration.stderr || "Agent registration failed.",
				context: { userId, agentName },
			});
			json(res, 502, {
				error: "agent_registration_failed",
				message: registration.stderr || "Failed to resolve backend agent id.",
			});
			return;
		}

		agentId = registration.agentId;
		agentUniqueId = registration.agentUniqueId ?? buildAgentUniqueId({ agentName, userId, projectId });
	}

	if (agentId == null || !agentUniqueId) {
		json(res, 409, {
			error: "session_hydration_failed",
			message: "The backend-owned session could not be attached safely.",
		});
		return;
	}

	let sessionKey: string;
	let agentSessionId: number | null = null;
	let startedAt: string | null = null;
	const persistedCwd = agentName === "mainsequence-project-coder" ? agentCwd : null;
	const frozenRepoRoot =
		agentName === "mainsequence-project-coder"
			? existingSessionMetadata?.repoRoot ?? (agentCwd ? resolveGitRepoRoot(agentCwd) : null)
			: null;

	if (newChat) {
		startedAt = new Date().toISOString();
		const pendingRuntimeSessionId = buildPendingRuntimeSessionId(agentUniqueId, startedAt);
		const sessionMetadataInput = isPlainObject(body.sessionMetadata) ? body.sessionMetadata : {};
		const runtimeConfig = buildRuntimeConfigSnapshot(sessionModelBinding);

		const payload: Record<string, unknown> = {
			status: "running",
			started_at: startedAt,
			ended_at: null,
			created_by_user: userId,
			llm_provider: resolveBackendLlmProvider(sessionModelBinding),
			llm_model: resolveBackendLlmModel(sessionModelBinding),
			engine_name: agentName === "astro-orchestrator" ? "astro_router_v1" : "astro_project_session_v1",
			runtime_config_snapshot: runtimeConfig,
			error_detail: "",
			external_session_id:
				typeof body.external_session_id === "string" ? body.external_session_id : "",
			runtime_session_id: pendingRuntimeSessionId,
			thread_id: threadId,
			usage_summary: {
				input_tokens: 0,
				output_tokens: 0,
				total_tokens: 0,
				estimated_cost_usd: 0,
			},
			session_metadata: {
				source: "frontend",
				workflow_key: agentName,
				...(projectId ? { project_id: projectId } : {}),
				...(persistedCwd ? { project_cwd: persistedCwd } : {}),
				...(frozenRepoRoot ? { project_repo_root: frozenRepoRoot } : {}),
				...(projectRuntime ? { project_runtime_snapshot: projectRuntime } : {}),
				...(sessionModelBinding ? { session_model_binding: sessionModelBinding } : {}),
				...sessionMetadataInput,
			},
		};

		const sessionStart = await startBackendAgentSession({
			agentId,
			payload,
			env: process.env,
			log: (message) => {
				console.log(`[astro-stream] ${message}`);
			},
		});

		if (!sessionStart.ok || !sessionStart.agentSessionId) {
			json(res, 502, {
				error: "agent_session_start_failed",
				message: sessionStart.error || "Failed to start backend agent session.",
			});
			return;
		}

		agentSessionId = sessionStart.agentSessionId;
		sessionKey = buildBackendRuntimeSessionId(agentSessionId);
		writeSessionMetadata(sessionKey, {
			agentId,
			agentUniqueId,
			agentSessionId,
			threadId,
			startedAt,
			agentName,
			projectId,
			cwd: persistedCwd,
			repoRoot: frozenRepoRoot,
			pendingOnboarding: false,
			pendingRuntimeBootstrap: agentName === "mainsequence-project-coder",
			switchSummary: null,
			projectRuntime: null,
			sessionModelBinding,
			sessionConfigOverrides: null,
		});
	} else {
		if (!runtimeSessionId) {
			json(res, 400, {
				error: "missing_runtime_session_id",
				message: "runtime_session_id is required when newChat is false.",
			});
			return;
		}
		if (existingSessionMetadata?.agentUniqueId && existingSessionMetadata.agentUniqueId !== agentUniqueId) {
			json(res, 409, {
				error: "session_mismatch",
				message: "runtime_session_id does not match the active agent.",
			});
			return;
		}
		agentSessionId = existingSessionMetadata?.agentSessionId ?? null;
		startedAt = existingSessionMetadata?.startedAt ?? null;
		sessionKey = runtimeSessionId;
		writeSessionMetadata(sessionKey, {
			agentId,
			agentUniqueId,
			agentSessionId,
			threadId,
			startedAt,
			agentName,
			projectId,
			cwd: persistedCwd,
			repoRoot: frozenRepoRoot,
			pendingOnboarding: existingSessionMetadata?.pendingOnboarding ?? false,
			pendingRuntimeBootstrap,
			switchSummary: existingSessionMetadata?.switchSummary ?? null,
			projectRuntime,
			sessionModelBinding,
			sessionConfigOverrides: existingSessionMetadata?.sessionConfigOverrides ?? null,
		});
	}

	writeThreadBinding({
		threadId,
		runtimeSessionId: sessionKey,
		updatedAt: new Date().toISOString(),
	});

	if (logTraffic) {
		const selectedModelForLog =
			formatLoggedModel({
				responseProvider: null,
				responseModel: null,
				sessionModelBinding,
			}) ?? agentConfig?.model ?? null;
		console.log(
			`[astro-stream] SESSION agent=${agentName} session=${sessionKey} thread=${threadId} agent_id=${agentId} agent_session_id=${agentSessionId ?? "n/a"}${selectedModelForLog ? ` model=${selectedModelForLog}` : ""}`,
		);
	}

	let conversationStore: ConversationStore;
	try {
		conversationStore = createConversationStore({
			sessionDir,
			sessionKey,
			threadId,
			agentName,
			agentId,
			agentSessionId,
			startedAt,
		});
		conversationStore.recordUserMessageSync({ text: latestUserMessage });
	} catch (error) {
		console.error(
			`[astro-stream] failed to initialize conversation history for session=${sessionKey}: ${
				error instanceof Error ? error.message : String(error)
			}`,
		);
		json(res, 500, {
			error: "conversation_persistence_failed",
			message: "Failed to persist conversation history before starting the stream.",
		});
		return;
	}

	res.writeHead(200, {
		"Content-Type": "text/event-stream",
		"Cache-Control": "no-cache",
		Connection: "keep-alive",
		"X-Thread-Id": threadId,
		...(agentId != null ? { "X-Agent-Id": String(agentId) } : {}),
		...(agentUniqueId ? { "X-Agent-Unique-Id": agentUniqueId } : {}),
		...(agentSessionId != null ? { "X-Agent-Session-Id": String(agentSessionId) } : {}),
		"X-Session-Key": sessionKey,
		"X-Stream-Protocol": "ui-message-stream",
		Protocol: "ui-message-stream",
	});

	res.write("retry: 1000\n\n");

	const messageId = `msg_${Date.now().toString(36)}_${Math.floor(Math.random() * 1e6).toString(36)}`;
	const ctx: RequestContext = {
		res,
		messageId,
		threadId,
		sessionKey,
		agentId,
		agentUniqueId,
		agentSessionId,
		agentName,
		userId,
		conversationStore,
		logState: {
			reasoning: null,
			text: null,
			toolCalls: new Map(),
		},
		eventId: 0,
		textCounter: 0,
		reasoningCounter: 0,
		runtimeToolCounter: 0,
		toolCallIds: new Map(),
		switching: false,
		piProcess: null,
		finished: false,
		system,
		uiContext: context,
		uiTools: tools,
		sessionModelBinding,
		sessionConfigOverrides: existingSessionMetadata?.sessionConfigOverrides ?? null,
		responseProvider: null,
		responseModel: null,
		piAssistantTextSeen: false,
		lastAssistantFinishReason: null,
		lastAssistantUsage: undefined,
	};

	if (newChat && agentSessionId != null && agentUniqueId) {
		writeChunk(ctx, {
			type: "new_session",
			new_session: {
				agent_session_id: agentSessionId,
				session_key: sessionKey,
				agent_unique_id: agentUniqueId,
				thread_id: threadId,
				agent_id: agentId ?? -1,
			},
		});
	}

	writeChunk(ctx, { type: "start", messageId });

	if (pendingRuntimeBootstrap && agentName === "mainsequence-project-coder" && agentCwd && agentUniqueId) {
		emitAssistantText(ctx, buildProjectRuntimeBootstrapAnnouncement(switchSummary));
		const runtimeBootstrapResult = runPendingProjectRuntimeBootstrap(ctx, {
			cwd: agentCwd,
			repoRoot: frozenRepoRoot,
			sessionKey,
			agentId,
			agentUniqueId,
			agentSessionId,
			threadId,
			startedAt,
			agentName,
			projectId,
			pendingOnboarding: existingSessionMetadata?.pendingOnboarding ?? false,
			switchSummary,
			sessionModelBinding,
			sessionConfigOverrides: existingSessionMetadata?.sessionConfigOverrides ?? null,
		});
		if (!runtimeBootstrapResult.ok) {
			writeChunk(ctx, {
				type: "error",
				error: formatProjectRuntimeBootstrapError(runtimeBootstrapResult),
			});
			writeDone(ctx);
			return;
		}
		projectRuntime = runtimeBootstrapResult.snapshot;
		pendingRuntimeBootstrap = false;
	}

	const prompt = buildPrompt(system, latestUserMessage, context, tools, {
		switchSummary,
		onboardingInstruction: shouldRunPendingOnboarding
			? buildCoderOnboardingInstruction(switchSummary, projectRuntime)
			: null,
		projectRuntimeSummary: projectRuntime ? formatProjectRuntimeSummary(projectRuntime) : null,
	});

	if (agentName === "mainsequence-project-coder") {
		writeSessionMetadata(sessionKey, {
			agentId,
			agentUniqueId,
			agentSessionId,
			threadId,
			startedAt,
			agentName,
			projectId,
			cwd: persistedCwd,
			repoRoot: frozenRepoRoot,
			pendingOnboarding: shouldRunPendingOnboarding ? false : (existingSessionMetadata?.pendingOnboarding ?? false),
			pendingRuntimeBootstrap,
			switchSummary,
			projectRuntime,
			sessionModelBinding,
			sessionConfigOverrides: existingSessionMetadata?.sessionConfigOverrides ?? null,
		});
	}

	runPiPrompt(prompt, ctx, {
		cwd: agentCwd ?? repoRoot,
		projectId,
		agentConfig,
		envOverrides:
			agentName === "mainsequence-project-coder"
				? buildActivatedProjectEnv(process.env, projectRuntime, {
						projectId,
						cwd: agentCwd,
				  })
				: undefined,
	});
});

server.listen(port, host, () => {
	console.log(`[astro-stream] Listening on http://${host}:${port}`);
	console.log("[astro-stream] POST /api/chat to start a data-stream response");
});
