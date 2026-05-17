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
	renameSync,
	readFileSync,
	rmSync,
	statSync,
	writeFileSync,
} from "node:fs";
import { randomUUID } from "node:crypto";
import {
	attachAgentId,
	serializeSse,
	type StreamErrorSource,
	type StreamEvent,
} from "./protocol.js";
import {
	createConversationStore,
	type ConversationStore,
} from "./conversation-store.js";
import {
	a2aEnvelopeToUserProvenance,
	mergeA2AEnvelopes,
	normalizeA2AEnvelope,
	normalizeA2AResponseFormat,
	type A2AEnvelope,
} from "./a2a-envelope.js";
import {
	repairPiSessionJsonlCurrentBranch,
	validatePiSessionJsonlCurrentBranch,
	type PiSessionJsonlRepair,
} from "./pi-history-projector.js";
import {
	SessionCheckpointClient,
	type CheckpointBundle,
	type CheckpointLatestResponse,
	type CheckpointLeaseResponse,
	type SessionCheckpointClientResult,
} from "./session-checkpoint-client.js";
import {
	collectAvailableModels,
	collectModelCatalog,
	DEFAULT_OPENAI_MODEL,
	DEFAULT_OPENAI_PROVIDER,
	type AvailableModelsResponse,
	type RunConfigReasoningEffort,
} from "./available-models.js";
import {
	buildPiModelArgument,
	buildSessionModelEnv,
	deriveSessionModelBindingFromSessionPayload,
	normalizeSessionModelBinding,
	rebindSessionModelBindingFromIdentity,
	type SessionModelBinding,
} from "./session-model.js";
import {
	ensureSessionScopedPiAgentDir,
	normalizeSessionConfigOverrides,
	resolveSessionRuntimeLimits,
	validateSessionConfigPatch,
	type SessionConfigOverrides,
} from "./session-config.js";
import { resolveProviderDefinition } from "./model-provider-definitions.js";
import { readSessionInsights } from "./session-insights.js";
import {
	listModelProviderAuthStatuses,
	signOffModelProvider,
} from "./model-provider-auth.js";
import {
	cleanupScopedPiAgentDir,
	flushScopedProviderCredential,
	hydrateScopedProviderCredentials,
} from "./model-provider-scoped-auth.js";
import {
	cancelModelProviderSignInAttempt,
	getModelProviderSignInAttempt,
	startModelProviderSignIn,
	submitModelProviderSignInManualInput,
} from "./model-provider-signin.js";
import type { AgentConfig } from "../../pi/extensions/tools/specialist-delegate/agents.js";
import {
	fetchBackendAgentSession,
	resolveMainsequenceUserId,
	shouldRegisterAgents,
} from "../../pi/extensions/shared/agent-registration.js";
import {
	buildA2ASystemInstruction,
} from "../../pi/extensions/shared/a2a.js";
import { logStructuredEvent } from "../../pi/extensions/shared/structured-logging.js";
import {
	buildMainsequenceStoredAuthEnv,
	bootstrapMainsequenceCliAuth,
	loadEnvFile,
	startMainsequenceCredentialExchangeLoop,
} from "../../scripts/mainsequence_runtime_auth.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const ASTRO_EXECUTION_MODE_ENV = "ASTRO_EXECUTION_MODE";
const ASTRO_FIXED_AGENT_TYPE_ENV = "ASTRO_FIXED_AGENT_TYPE";
const ASTRO_FIXED_PROJECT_ID_ENV = "ASTRO_FIXED_PROJECT_ID";
const ASTRO_FIXED_PROJECT_CWD_ENV = "ASTRO_FIXED_PROJECT_CWD";
const ASTRO_PROJECT_IMAGE_REF_ENV = "ASTRO_PROJECT_IMAGE_REF";
const PROJECT_SESSION_AGENT_TYPES = new Set(["mainsequence-project-executor"]);
const ALLOWED_AGENT_TYPES = new Set(["astro-orchestrator", ...PROJECT_SESSION_AGENT_TYPES]);
const PI_BUILT_IN_TOOL_NAMES = new Set(["read", "bash", "edit", "write", "grep", "find", "ls"]);

loadEnvFile(repoRoot);

const host = process.env.ASTRO_STREAM_HOST ?? "0.0.0.0";
const configuredPort = Number(process.env.ASTRO_STREAM_PORT ?? "8787");
const port = Number.isFinite(configuredPort) && configuredPort > 0 ? configuredPort : 8787;
const logTraffic = process.env.ASTRO_STREAM_LOG_TRAFFIC !== "0";
const logHealthTraffic = process.env.ASTRO_STREAM_LOG_HEALTH_TRAFFIC === "1";
const logRequestBodies = process.env.ASTRO_STREAM_LOG_REQUEST_BODIES === "1";
const sessionDir =
	process.env.ASTRO_STREAM_SESSION_DIR ?? path.join(repoRoot, ".astro", "stream-sessions");
const runtimeHealthStartedAt = new Date().toISOString();
const runtimeHealthStatePath =
	process.env.ASTRO_STREAM_HEALTH_STATE_PATH ??
	path.join(
		process.env.ASTRO_CONTAINER_DATA_DIR
			? path.join(path.resolve(process.env.ASTRO_CONTAINER_DATA_DIR), ".astro")
			: path.join(repoRoot, ".astro"),
		"stream-health.json",
	);
const providerCredentialFlushIntervalMs = (() => {
	const configured = Number(process.env.ASTRO_PROVIDER_CREDENTIAL_FLUSH_INTERVAL_MS ?? "10000");
	return Number.isFinite(configured) && configured >= 1000 ? Math.trunc(configured) : 10000;
})();
const sessionCancelGraceMs = (() => {
	const configured = Number(process.env.ASTRO_SESSION_CANCEL_GRACE_MS ?? "5000");
	return Number.isFinite(configured) && configured >= 500 ? Math.trunc(configured) : 5000;
})();
let mainsequenceCredentialExchangeLoop: ReturnType<typeof startMainsequenceCredentialExchangeLoop> | null = null;
let mainsequenceCredentialExchangeLoopStarted = false;
let mainsequenceCliAuthReady = false;
let mainsequenceCliAuthBootstrapPromise: Promise<void> | null = null;
let checkpointRestoreCount = 0;

type ActiveScopedProviderCredential = {
	scopedPiAgentDir: string;
	createdByUser: string;
	agentSessionId: number | null;
	provider: string;
	env: NodeJS.ProcessEnv;
	sessionKey: string;
};

const activeScopedProviderCredentials = new Map<string, ActiveScopedProviderCredential>();

function resolveOrchestratorRuntimeCwd(): string {
	const configured = process.env.ASTRO_ORCHESTRATOR_CWD?.trim();
	return configured ? path.resolve(configured) : repoRoot;
}

type RuntimeProfileKind = "orchestrator" | "project_worker";

type RuntimeProfile = {
	kind: RuntimeProfileKind;
	executionMode: string | null;
	fixedAgentType: string | null;
	fixedProjectId: string | null;
	fixedProjectCwd: string | null;
	projectImageRef: string | null;
};

type RuntimeProfileLogPayload = {
	kind: RuntimeProfileKind;
	executionMode: string | null;
	fixedAgentType: string | null;
	fixedProjectId: string | null;
	fixedProjectCwd: string | null;
	projectImageRef: string | null;
};

type RuntimeProfileValidationResult =
	| { ok: true }
	| {
			ok: false;
			statusCode: number;
			error: string;
			message: string;
	  };

type RuntimeHealthSeverity = "warning" | "error" | "fatal";

type RuntimeHealthIssue = {
	id: string;
	source: string;
	severity: RuntimeHealthSeverity;
	at: string;
	message: string;
	name: string | null;
	stack: string | null;
	context: Record<string, unknown> | null;
};

type RuntimeHealthSnapshot = {
	ok: true;
	status: "ok" | "degraded";
	degraded: boolean;
	pid: number;
	startedAt: string;
	lastUpdatedAt: string;
	uptimeSeconds: number;
	healthStatePath: string;
	runtimeProfile: RuntimeProfileLogPayload;
	issueCount: number;
	recentIssues: RuntimeHealthIssue[];
	previousRun: {
		pid: number | null;
		startedAt: string | null;
		lastUpdatedAt: string | null;
		status: string | null;
		issueCount: number;
		recentIssues: RuntimeHealthIssue[];
	} | null;
};

const MAX_RUNTIME_HEALTH_ISSUES = 25;
const previousRuntimeHealthSnapshot = readPreviousRuntimeHealthSnapshot();
const runtimeHealthIssues: RuntimeHealthIssue[] = [];
let runtimeHealthIssueCount = 0;

function readPreviousRuntimeHealthSnapshot(): RuntimeHealthSnapshot | null {
	try {
		if (!existsSync(runtimeHealthStatePath)) return null;
		const parsed = JSON.parse(readFileSync(runtimeHealthStatePath, "utf8"));
		if (!parsed || typeof parsed !== "object") return null;
		return parsed as RuntimeHealthSnapshot;
	} catch {
		return null;
	}
}

function serializeRuntimeHealthError(error: unknown): {
	name: string | null;
	message: string;
	stack: string | null;
} {
	if (error instanceof Error) {
		return {
			name: error.name || null,
			message: redactRuntimeHealthText(error.message || String(error)) ?? "Unknown error",
			stack: redactRuntimeHealthText(typeof error.stack === "string" ? error.stack : null),
		};
	}
	if (typeof error === "string") {
		return { name: null, message: redactRuntimeHealthText(error) ?? "Unknown error", stack: null };
	}
	try {
		return {
			name: null,
			message: redactRuntimeHealthText(JSON.stringify(error)) ?? "Unknown error",
			stack: null,
		};
	} catch {
		return { name: null, message: redactRuntimeHealthText(String(error)) ?? "Unknown error", stack: null };
	}
}

function redactRuntimeHealthText(value: string | null): string | null {
	if (!value) return value;
	let redacted = value;
	for (const [key, rawSecret] of Object.entries(process.env)) {
		if (!rawSecret || rawSecret.length < 4) continue;
		if (!/(TOKEN|SECRET|PASSWORD|API_?KEY|CREDENTIAL)/i.test(key)) continue;
		redacted = redacted.split(rawSecret).join(`[redacted:${key}]`);
	}
	return redacted;
}

function buildRuntimeHealthSnapshot(): RuntimeHealthSnapshot {
	const lastUpdatedAt = new Date().toISOString();
	const recentIssues = runtimeHealthIssues.slice(-MAX_RUNTIME_HEALTH_ISSUES);
	return {
		ok: true,
		status: recentIssues.length > 0 ? "degraded" : "ok",
		degraded: recentIssues.length > 0,
		pid: process.pid,
		startedAt: runtimeHealthStartedAt,
		lastUpdatedAt,
		uptimeSeconds: Math.round(process.uptime()),
		healthStatePath: runtimeHealthStatePath,
		runtimeProfile: serializeRuntimeProfile(resolveRuntimeProfile()),
		issueCount: runtimeHealthIssueCount,
		recentIssues,
		previousRun: previousRuntimeHealthSnapshot
			? {
					pid:
						typeof previousRuntimeHealthSnapshot.pid === "number"
							? previousRuntimeHealthSnapshot.pid
							: null,
					startedAt:
						typeof previousRuntimeHealthSnapshot.startedAt === "string"
							? previousRuntimeHealthSnapshot.startedAt
							: null,
					lastUpdatedAt:
						typeof previousRuntimeHealthSnapshot.lastUpdatedAt === "string"
							? previousRuntimeHealthSnapshot.lastUpdatedAt
							: null,
					status:
						typeof previousRuntimeHealthSnapshot.status === "string"
							? previousRuntimeHealthSnapshot.status
							: null,
					issueCount:
						typeof previousRuntimeHealthSnapshot.issueCount === "number"
							? previousRuntimeHealthSnapshot.issueCount
							: 0,
					recentIssues: Array.isArray(previousRuntimeHealthSnapshot.recentIssues)
						? previousRuntimeHealthSnapshot.recentIssues.slice(-MAX_RUNTIME_HEALTH_ISSUES)
						: [],
			  }
			: null,
	};
}

async function flushActiveScopedProviderCredentialsForShutdown(signal: string) {
	const records = [...activeScopedProviderCredentials.values()];
	if (records.length === 0) return;
	logStructuredEvent({
		component: "astro-stream",
		event: "provider_credentials_shutdown_flush_started",
		message: "Astro is flushing active scoped provider credentials before shutdown.",
		data: {
			signal,
			count: records.length,
		},
	});
	await Promise.allSettled(
		records.map(async (record) => {
			const flushed = await flushScopedProviderCredential({
				scopedPiAgentDir: record.scopedPiAgentDir,
				createdByUser: record.createdByUser,
				agentSessionId: record.agentSessionId,
				provider: record.provider,
				reason: "shutdown_flush",
				env: record.env,
				log: (message) => console.log(`[astro-stream] ${message}`),
			});
			if (flushed.ok === false) {
				logStructuredEvent({
					severity: "ERROR",
					component: "astro-stream",
					event: "provider_credentials_shutdown_flush_failed",
					message: "Astro could not flush scoped provider credentials during shutdown.",
					data: {
						signal,
						sessionId: record.sessionKey,
						agentSessionId: record.agentSessionId,
						provider: record.provider,
						error: flushed.error,
						backendMessage: flushed.message,
					},
				});
				return;
			}
			logStructuredEvent({
				component: "astro-stream",
				event: "provider_credentials_shutdown_flushed",
				message: "Astro flushed scoped provider credentials during shutdown.",
				data: {
					signal,
					sessionId: record.sessionKey,
					agentSessionId: record.agentSessionId,
					provider: record.provider,
					version: flushed.value.version,
					credentialHash: flushed.value.credential_hash,
				},
			});
		}),
	);
}

function persistRuntimeHealthSnapshot() {
	try {
		mkdirSync(path.dirname(runtimeHealthStatePath), { recursive: true });
		writeFileSync(runtimeHealthStatePath, JSON.stringify(buildRuntimeHealthSnapshot(), null, 2));
	} catch (error) {
		const serialized = serializeRuntimeHealthError(error);
		console.error(`[astro-stream] failed to persist runtime health state: ${serialized.message}`);
	}
}

function recordRuntimeHealthIssue(options: {
	source: string;
	error: unknown;
	severity?: RuntimeHealthSeverity;
	context?: Record<string, unknown>;
}): RuntimeHealthIssue {
	const serialized = serializeRuntimeHealthError(options.error);
	const issue: RuntimeHealthIssue = {
		id: randomUUID(),
		source: options.source,
		severity: options.severity ?? "error",
		at: new Date().toISOString(),
		message: serialized.message,
		name: serialized.name,
		stack: serialized.stack,
		context: options.context ?? null,
	};
	runtimeHealthIssues.push(issue);
	runtimeHealthIssueCount += 1;
	if (runtimeHealthIssues.length > MAX_RUNTIME_HEALTH_ISSUES) {
		runtimeHealthIssues.splice(0, runtimeHealthIssues.length - MAX_RUNTIME_HEALTH_ISSUES);
	}
	persistRuntimeHealthSnapshot();
	return issue;
}

if (!Number.isFinite(configuredPort) || configuredPort <= 0) {
	recordRuntimeHealthIssue({
		source: "startup_config",
		severity: "warning",
		error: new Error(
			`Invalid ASTRO_STREAM_PORT "${process.env.ASTRO_STREAM_PORT}", falling back to ${port}.`,
		),
	});
} else {
	persistRuntimeHealthSnapshot();
}

if (process.env.ASTRO_STREAM_BOOTSTRAP_ERROR) {
	let bootstrapError: unknown = process.env.ASTRO_STREAM_BOOTSTRAP_ERROR;
	try {
		const parsed = JSON.parse(process.env.ASTRO_STREAM_BOOTSTRAP_ERROR);
		const error = new Error(
			typeof parsed?.message === "string" ? parsed.message : process.env.ASTRO_STREAM_BOOTSTRAP_ERROR,
		);
		error.name = typeof parsed?.name === "string" && parsed.name ? parsed.name : "StartupBootstrapError";
		if (typeof parsed?.stack === "string") {
			error.stack = parsed.stack;
		}
		bootstrapError = error;
	} catch {
		// Keep the raw serialized value.
	}
	recordRuntimeHealthIssue({
		source: "startup_bootstrap",
		severity: "fatal",
		error: bootstrapError,
	});
}

function ensureMainsequenceCredentialExchangeLoopStarted() {
	if (mainsequenceCredentialExchangeLoopStarted) return;
	mainsequenceCredentialExchangeLoop = startMainsequenceCredentialExchangeLoop({
		env: process.env,
		log: (message) => console.error(`[astro] ${message}`),
	});
	mainsequenceCredentialExchangeLoopStarted = true;
}

async function ensureMainsequenceCliAuthReady() {
	if (mainsequenceCliAuthReady) {
		ensureMainsequenceCredentialExchangeLoopStarted();
		return;
	}

	if (!mainsequenceCliAuthBootstrapPromise) {
		mainsequenceCliAuthBootstrapPromise = bootstrapMainsequenceCliAuth({
			env: process.env,
			log: (message) => console.log(`[astro] ${message}`),
		})
			.then(() => {
				mainsequenceCliAuthReady = true;
				ensureMainsequenceCredentialExchangeLoopStarted();
			})
			.catch((error) => {
				mainsequenceCliAuthReady = false;
				throw error;
			})
			.finally(() => {
				mainsequenceCliAuthBootstrapPromise = null;
			});
	}

	await mainsequenceCliAuthBootstrapPromise;
}

process.once("exit", () => {
	mainsequenceCredentialExchangeLoop?.stop();
});

process.on("uncaughtException", (error, origin) => {
	const issue = recordRuntimeHealthIssue({
		source: "uncaught_exception",
		severity: "fatal",
		error,
		context: { origin },
	});
	console.error(`[astro-stream] captured uncaught exception issue=${issue.id}: ${issue.message}`);
});

process.on("unhandledRejection", (reason) => {
	const issue = recordRuntimeHealthIssue({
		source: "unhandled_rejection",
		severity: "fatal",
		error: reason,
	});
	console.error(`[astro-stream] captured unhandled rejection issue=${issue.id}: ${issue.message}`);
});

type RequestContext = {
	res: import("node:http").ServerResponse;
	messageId: string;
	threadId: string;
	sessionKey: string;
	agentId: number | null;
	agentUniqueId: string | null;
	agentSessionId: number | null;
	agentType: string;
	userId: string;
	conversationStore: ConversationStore;
	logState: RequestLogState;
	eventId: number;
	textCounter: number;
	reasoningCounter: number;
	activeReasoningAnnotationOrdinal: number | null;
	runtimeToolCounter: number;
	toolCallIds: Map<number, { toolCallId: string; toolName: string }>;
	piProcess: ChildProcess | null;
	cancelKillTimer: ReturnType<typeof setTimeout> | null;
	cancellation: ActiveRunCancellation | null;
	clientAttached: boolean;
	finished: boolean;
	terminalError: { errorCode: string | null; errorDetail: string | null } | null;
	system: string | undefined;
	uiContext: Record<string, unknown>;
	uiTools: Record<string, unknown>;
	sessionModelBinding: SessionModelBinding | null;
	sessionConfigOverrides: SessionConfigOverrides | null;
	responseProvider: string | null;
	responseModel: string | null;
	piAssistantTextSeen: boolean;
	lastAssistantFinishReason: string | null;
	lastAssistantErrorMessage: string | null;
	lastAssistantUsage: { inputTokens?: number; outputTokens?: number } | undefined;
	checkpointLease: CheckpointLeaseState | null;
};

type ActiveRunCancellation = {
	requested: true;
	cancellationId: string | null;
	reason: string;
	message: string | null;
};

type RequestLogState = {
	reasoning: { id: string; text: string } | null;
	text: { id: string; text: string } | null;
	toolCalls: Map<string, { toolName: string; args: string }>;
};

type ActiveStreamSession = {
	sessionKey: string;
	threadId: string;
	agentSessionId: number | null;
	messageId: string;
	agentType: string;
	startedAt: string;
	clientAttached: boolean;
	lastPiEventAt: string | null;
	checkpointVersion: number | null;
	bundleHash: string | null;
	cancelling: boolean;
	cancellationId: string | null;
};

const activeStreamSessions = new Map<string, ActiveStreamSession>();
const activeStreamContexts = new Map<string, RequestContext>();

function getActiveStreamSessionKey(sessionKey: string, agentSessionId: number | null): string {
	return agentSessionId == null ? `session:${sessionKey}` : `agent_session:${agentSessionId}`;
}

function getActiveStreamSessionKeyFromContext(ctx: RequestContext): string {
	return getActiveStreamSessionKey(ctx.sessionKey, ctx.agentSessionId);
}

function markActiveStreamSession(ctx: RequestContext) {
	activeStreamSessions.set(getActiveStreamSessionKeyFromContext(ctx), {
		sessionKey: ctx.sessionKey,
		threadId: ctx.threadId,
		agentSessionId: ctx.agentSessionId,
		messageId: ctx.messageId,
		agentType: ctx.agentType,
		startedAt: new Date().toISOString(),
		clientAttached: ctx.clientAttached,
		lastPiEventAt: null,
		checkpointVersion: ctx.checkpointLease?.checkpointVersion ?? null,
		bundleHash: ctx.checkpointLease?.bundleHash ?? null,
		cancelling: ctx.cancellation?.requested === true,
		cancellationId: ctx.cancellation?.cancellationId ?? null,
	});
	activeStreamContexts.set(getActiveStreamSessionKeyFromContext(ctx), ctx);
}

function clearActiveStreamSession(ctx: RequestContext) {
	const activeKey = getActiveStreamSessionKeyFromContext(ctx);
	const active = activeStreamSessions.get(activeKey);
	if (!active || active.messageId !== ctx.messageId) return;
	activeStreamSessions.delete(activeKey);
	activeStreamContexts.delete(activeKey);
}

function updateActiveStreamSession(ctx: RequestContext, updates: Partial<ActiveStreamSession>) {
	const activeKey = getActiveStreamSessionKeyFromContext(ctx);
	const active = activeStreamSessions.get(activeKey);
	if (!active || active.messageId !== ctx.messageId) return;
	activeStreamSessions.set(activeKey, {
		...active,
		...updates,
	});
}

function getActiveStreamSession(sessionKey: string, agentSessionId: number | null = normalizeNumericId(sessionKey)): ActiveStreamSession | null {
	return (
		activeStreamSessions.get(getActiveStreamSessionKey(sessionKey, agentSessionId)) ??
		activeStreamSessions.get(getActiveStreamSessionKey(sessionKey, null)) ??
		null
	);
}

function getActiveStreamContext(sessionKey: string, agentSessionId: number | null = normalizeNumericId(sessionKey)): RequestContext | null {
	return (
		activeStreamContexts.get(getActiveStreamSessionKey(sessionKey, agentSessionId)) ??
		activeStreamContexts.get(getActiveStreamSessionKey(sessionKey, null)) ??
		null
	);
}

type SessionMetadata = {
	agentId: number | null;
	agentUniqueId: string | null;
	agentSessionId: number | null;
	threadId: string | null;
	startedAt: string | null;
	agentType: string | null;
	projectId: string | null;
	cwd: string | null;
	repoRoot: string | null;
	projectImageRef?: string | null;
	sessionModelBinding: SessionModelBinding | null;
	sessionConfigOverrides: SessionConfigOverrides | null;
	a2a?: A2AEnvelope | null;
	history_annotations?: HistoryAnnotations;
};

type HistoryAnnotation = {
	assistant_ordinal: number;
	pi_entry_id: string | null;
	stream_message_id: string | null;
	had_reasoning: true;
	reasoning_text_persisted: boolean;
	reasoning_started_at: string | null;
	reasoning_completed_at: string | null;
};

type HistoryAnnotations = {
	version: 1;
	assistant_messages: HistoryAnnotation[];
};

type CheckpointManifest = {
	session_id: string;
	checkpoint_version: number;
	restored_at: string;
	bundle_hash: string;
	lease_holder_id: string;
	lease_token: string;
	lease_expires_at: string;
};

type CheckpointLifecycleState = {
	session_id: string;
	state: "running" | "finalizing_checkpoint" | "idle";
	updated_at: string;
	lease_holder_id: string | null;
	lease_token: string | null;
	checkpoint_version: number | null;
	bundle_hash: string | null;
	reason: string | null;
	marker_file?: string | null;
};

type CheckpointLeaseState = {
	holderId: string;
	leaseToken: string;
	leaseExpiresAt: string;
	checkpointVersion: number;
	bundleHash: string;
	renewTimer: ReturnType<typeof setInterval> | null;
};

type ThreadSessionBinding = {
	threadId: string;
	runtimeSessionId: string;
	updatedAt: string | null;
};

type HydratedBackendSession = {
	agentId: number;
	agentUniqueId: string | null;
	metadata: SessionMetadata;
};

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
	if (!logHealthTraffic && req.method === "GET" && url.pathname === "/health") return;

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

function summarizeAvailableModelsForLog(availableModels: AvailableModelsResponse): {
	providerCount: number;
	modelCount: number;
	sourceSummaries: Array<{
		source: string;
		ok: boolean;
		count: number;
		error: string | null;
		authProviderCount: number | null;
		authStatusError: string | null;
		totalModelCount: number | null;
		availableModelCount: number | null;
	}>;
	piModelRegistrySource: {
		ok: boolean;
		count: number;
		error: string | null;
		authProviderCount: number | null;
		authStatusError: string | null;
		totalModelCount: number | null;
		availableModelCount: number | null;
	} | null;
} {
	const sourceSummaries = availableModels.sources.map((source) => {
		const details = source.details ?? {};
		const authProviders = Array.isArray(details.auth_providers) ? details.auth_providers : null;
		return {
			source: source.source,
			ok: source.ok,
			count: source.count,
			error: typeof source.error === "string" && source.error.trim() ? source.error.trim() : null,
			authProviderCount: authProviders ? authProviders.length : null,
			authStatusError:
				typeof details.auth_status_error === "string" && details.auth_status_error.trim()
					? details.auth_status_error.trim()
					: null,
			totalModelCount:
				typeof details.total_model_count === "number" && Number.isFinite(details.total_model_count)
					? details.total_model_count
					: null,
			availableModelCount:
				typeof details.available_model_count === "number" &&
				Number.isFinite(details.available_model_count)
					? details.available_model_count
					: null,
		};
	});
	const piModelRegistrySource =
		sourceSummaries.find((source) => source.source === "pi-model-registry") ?? null;
	return {
		providerCount: availableModels.providers.length,
		modelCount: availableModels.providers.reduce((total, group) => total + group.models.length, 0),
		sourceSummaries,
		piModelRegistrySource,
	};
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
	reasoning_effort?: RunConfigReasoningEffort;
} {
	const snapshot: {
		temperature: number;
		top_p: number;
		max_output_tokens: number;
		reasoning_effort?: RunConfigReasoningEffort;
	} = {
		temperature: 0.35,
		top_p: 0.9,
		max_output_tokens: 4000,
	};
	if (sessionModelBinding?.piThinkingLevel) {
		snapshot.reasoning_effort = sessionModelBinding.piThinkingLevel;
	}
	return snapshot;
}

function resolveBackendLlmProvider(sessionModelBinding: SessionModelBinding | null): string {
	return sessionModelBinding?.provider ?? DEFAULT_OPENAI_PROVIDER;
}

function resolveBackendLlmModel(sessionModelBinding: SessionModelBinding | null): string {
	return sessionModelBinding?.model ?? DEFAULT_OPENAI_MODEL;
}

function resolveBackendLlmThinking(sessionModelBinding: SessionModelBinding | null): string {
	const rawThinking = sessionModelBinding?.metadata?.backend_llm_thinking;
	if (typeof rawThinking === "string") return rawThinking;
	const normalizedThinking = sessionModelBinding?.runConfig.reasoning_effort;
	return typeof normalizedThinking === "string" ? normalizedThinking : "";
}

function doesSessionModelIdentityMatch(
	sessionModelBinding: SessionModelBinding | null,
	provider: string | null,
	model: string | null,
): boolean {
	return (
		sessionModelBinding?.provider === (provider?.trim() || null) &&
		sessionModelBinding?.model === (model?.trim() || null)
	);
}

async function ensureRequestCliAuth(
	res: import("node:http").ServerResponse,
	runtimeProfile: RuntimeProfile = resolveRuntimeProfile(),
): Promise<{ ok: true } | { ok: false }> {
	if (runtimeProfile.kind === "project_worker") {
		return { ok: true };
	}

	try {
		await ensureMainsequenceCliAuthReady();
		return { ok: true };
	} catch (error) {
		const message =
			error instanceof Error ? error.message : "Unknown Main Sequence CLI auth bootstrap failure.";
		json(res, 503, {
			error: "runtime_auth_unavailable",
			message: `Main Sequence runtime auth failed before the session started: ${message}`,
		});
		return { ok: false };
	}
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

function normalizeAgentType(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function isProjectSessionAgentType(agentType: string | null | undefined): boolean {
	return typeof agentType === "string" && PROJECT_SESSION_AGENT_TYPES.has(agentType);
}

function isImageBackedProjectExecutor(agentType: string | null | undefined): boolean {
	return agentType === "mainsequence-project-executor";
}

function resolveFixedAgentType(env: NodeJS.ProcessEnv = process.env): string | null {
	return normalizeAgentType(env[ASTRO_FIXED_AGENT_TYPE_ENV]);
}

function normalizeExecutionMode(value: unknown): string | null {
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

function resolveHeaderString(
	req: import("node:http").IncomingMessage,
	headerNames: string[],
): string | null {
	for (const headerName of headerNames) {
		const rawValue = req.headers[headerName.toLowerCase()];
		const value = Array.isArray(rawValue) ? rawValue.find((entry) => entry.trim()) : rawValue;
		if (typeof value === "string" && value.trim()) return value.trim();
	}
	return null;
}

function parseJwtPayload(token: string): Record<string, unknown> | null {
	const [, payload] = token.split(".");
	if (!payload) return null;
	try {
		const normalizedPayload = payload.replace(/-/g, "+").replace(/_/g, "/");
		const paddedPayload = normalizedPayload.padEnd(
			normalizedPayload.length + ((4 - (normalizedPayload.length % 4)) % 4),
			"=",
		);
		const parsed = JSON.parse(Buffer.from(paddedPayload, "base64").toString("utf8"));
		return isPlainObject(parsed) ? parsed : null;
	} catch {
		return null;
	}
}

function resolveUserIdFromAuthorizationHeader(req: import("node:http").IncomingMessage): string | null {
	const authorization = resolveHeaderString(req, ["authorization"]);
	const matched = authorization?.match(/^Bearer\s+(.+)$/i);
	if (!matched) return null;
	const payload = parseJwtPayload(matched[1].trim());
	if (!payload) return null;
	return resolveUserId(
		payload.userId ??
			payload.user_id ??
			payload.created_by_user ??
			payload.createdByUser ??
			payload.mainsequence_user_id ??
			payload.sub,
	);
}

function resolveUserIdFromHeaders(req: import("node:http").IncomingMessage): string | null {
	return resolveUserId(
		resolveHeaderString(req, [
			"x-mainsequence-user-id",
			"x-ms-user-id",
			"x-user-id",
			"x-created-by-user",
		]),
	);
}

function resolveUserIdFromRequest(
	req: import("node:http").IncomingMessage,
	url: URL,
	body?: Record<string, unknown>,
): string | null {
	return (
		resolveUserId(
			body?.userId ??
				body?.user_id ??
				body?.created_by_user ??
				body?.createdByUser ??
				url.searchParams.get("userId") ??
				url.searchParams.get("user_id") ??
				url.searchParams.get("created_by_user") ??
				url.searchParams.get("createdByUser"),
		) ??
		resolveUserIdFromHeaders(req) ??
		resolveUserIdFromAuthorizationHeader(req) ??
		resolveUserId(undefined)
	);
}

function resolveOptionalAgentSessionIdFromBodyOrSearch(
	body: Record<string, unknown>,
	url: URL,
): number | null {
	return normalizeNumericId(
		body.agent_session_id ??
			body.agentSessionId ??
			body.session_id ??
			body.sessionId ??
			url.searchParams.get("agent_session_id") ??
			url.searchParams.get("agentSessionId") ??
			url.searchParams.get("session_id") ??
			url.searchParams.get("sessionId"),
	);
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

function normalizeProjectImageRef(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function normalizeProjectCwd(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? path.resolve(trimmed) : null;
}

function resolveFixedProjectId(env: NodeJS.ProcessEnv = process.env): string | null {
	return normalizeProjectId(env[ASTRO_FIXED_PROJECT_ID_ENV]);
}

function resolveFixedProjectCwd(env: NodeJS.ProcessEnv = process.env): string | null {
	return normalizeProjectCwd(env[ASTRO_FIXED_PROJECT_CWD_ENV]);
}

function resolveConfiguredProjectImageRef(env: NodeJS.ProcessEnv = process.env): string | null {
	return normalizeProjectImageRef(env[ASTRO_PROJECT_IMAGE_REF_ENV]);
}

function resolveRuntimeProfile(env: NodeJS.ProcessEnv = process.env): RuntimeProfile {
	const fixedAgentType = resolveFixedAgentType(env);
	const executionMode = normalizeExecutionMode(env[ASTRO_EXECUTION_MODE_ENV]);
	const fixedProjectId = resolveFixedProjectId(env);
	const fixedProjectCwd = resolveFixedProjectCwd(env);
	const projectImageRef = resolveConfiguredProjectImageRef(env);
	const kind: RuntimeProfileKind = fixedAgentType
		? isProjectSessionAgentType(fixedAgentType)
			? "project_worker"
			: "orchestrator"
		: executionMode === "remote_project_worker" || fixedProjectCwd
			? "project_worker"
			: "orchestrator";

	return {
		kind,
		executionMode,
		fixedAgentType,
		fixedProjectId,
		fixedProjectCwd,
		projectImageRef,
	};
}

function serializeRuntimeProfile(profile: RuntimeProfile): RuntimeProfileLogPayload {
	return {
		kind: profile.kind,
		executionMode: profile.executionMode,
		fixedAgentType: profile.fixedAgentType,
		fixedProjectId: profile.fixedProjectId,
		fixedProjectCwd: profile.fixedProjectCwd,
		projectImageRef: profile.projectImageRef,
	};
}

function validateRuntimeProfile(profile: RuntimeProfile): RuntimeProfileValidationResult {
	if (profile.fixedAgentType && !ALLOWED_AGENT_TYPES.has(profile.fixedAgentType)) {
		return {
			ok: false,
			statusCode: 503,
			error: "invalid_runtime_profile",
			message: `${ASTRO_FIXED_AGENT_TYPE_ENV} is set to unknown agent type "${profile.fixedAgentType}".`,
		};
	}

	if (
		profile.executionMode === "remote_project_worker" &&
		profile.fixedAgentType &&
		!isProjectSessionAgentType(profile.fixedAgentType)
	) {
		return {
			ok: false,
			statusCode: 503,
			error: "invalid_runtime_profile",
			message: `${ASTRO_EXECUTION_MODE_ENV}=remote_project_worker requires a project-worker ${ASTRO_FIXED_AGENT_TYPE_ENV}.`,
		};
	}

	if (profile.kind === "project_worker" && !profile.fixedAgentType) {
		return {
			ok: false,
			statusCode: 503,
			error: "invalid_runtime_profile",
			message: `Project-worker runtime profile requires ${ASTRO_FIXED_AGENT_TYPE_ENV}.`,
		};
	}

	if (profile.kind === "project_worker" && !profile.fixedProjectCwd) {
		return {
			ok: false,
			statusCode: 503,
			error: "invalid_runtime_profile",
			message: `Project-worker runtime profile requires ${ASTRO_FIXED_PROJECT_CWD_ENV}.`,
		};
	}

	if (profile.fixedProjectCwd && !isExistingDirectory(profile.fixedProjectCwd)) {
		return {
			ok: false,
			statusCode: 503,
			error: "invalid_runtime_profile",
			message: `${ASTRO_FIXED_PROJECT_CWD_ENV} must point to an existing directory.`,
		};
	}

	return { ok: true };
}

type ProjectAttachment = {
	attached: boolean;
	projectId: string | null;
	cwd: string | null;
	repoRoot: string | null;
	projectImageRef: string | null;
	requestedProjectId: string | null;
	requestedCwd: string | null;
	fixedProjectId: string | null;
	fixedProjectCwd: string | null;
};

type ProjectAttachmentResolution =
	| ({ ok: true } & ProjectAttachment)
	| {
			ok: false;
			statusCode: number;
			error: string;
			message: string;
	  };

function resolveProjectAttachment(input: {
	agentType: string;
	body: Record<string, unknown>;
	existingSessionMetadata: SessionMetadata;
	runtimeProfile: RuntimeProfile;
}): ProjectAttachmentResolution {
	const requestedProjectId = normalizeProjectId(input.body.projectId);
	const requestedCwd = normalizeProjectCwd(input.body.cwd);
	const imageBackedProjectExecutor = isImageBackedProjectExecutor(input.agentType);
	const fixedProjectId = imageBackedProjectExecutor ? null : input.runtimeProfile.fixedProjectId;
	const fixedProjectCwd = input.runtimeProfile.fixedProjectCwd;
	const effectiveRequestedProjectId = requestedProjectId ?? fixedProjectId;
	const effectiveRequestedCwd = requestedCwd ?? fixedProjectCwd;
	const projectImageRef =
		input.runtimeProfile.projectImageRef ?? input.existingSessionMetadata.projectImageRef ?? null;
	const projectAttachmentRequired =
		input.runtimeProfile.kind === "project_worker" || isProjectSessionAgentType(input.agentType);
	const attached =
		projectAttachmentRequired ||
		Boolean(effectiveRequestedCwd ?? input.existingSessionMetadata.cwd);

	if (fixedProjectId && requestedProjectId && requestedProjectId !== fixedProjectId) {
		return {
			ok: false,
			statusCode: 409,
			error: "fixed_project_mismatch",
			message: `This runtime is pinned to projectId "${fixedProjectId}".`,
		};
	}

	if (fixedProjectCwd && requestedCwd && requestedCwd !== fixedProjectCwd) {
		return {
			ok: false,
			statusCode: 409,
			error: "fixed_project_cwd_mismatch",
			message: `This runtime is pinned to cwd "${fixedProjectCwd}".`,
		};
	}

	if (
		attached &&
		!imageBackedProjectExecutor &&
		input.existingSessionMetadata.projectId &&
		effectiveRequestedProjectId &&
		effectiveRequestedProjectId !== input.existingSessionMetadata.projectId
	) {
		return {
			ok: false,
			statusCode: 409,
			error: "session_mismatch",
			message: "runtime_session_id does not match the requested projectId.",
		};
	}

	if (
		attached &&
		input.existingSessionMetadata.cwd &&
		effectiveRequestedCwd &&
		effectiveRequestedCwd !== input.existingSessionMetadata.cwd
	) {
		return {
			ok: false,
			statusCode: 409,
			error: "session_mismatch",
			message: "runtime_session_id does not match the requested cwd.",
		};
	}

	if (!attached) {
		return {
			ok: true,
			attached: false,
			projectId: null,
			cwd: resolveOrchestratorRuntimeCwd(),
			repoRoot: null,
			projectImageRef: null,
			requestedProjectId,
			requestedCwd,
			fixedProjectId,
			fixedProjectCwd,
		};
	}

	const projectId = imageBackedProjectExecutor
		? requestedProjectId ?? input.existingSessionMetadata.projectId ?? fixedProjectId ?? null
		: effectiveRequestedProjectId ?? input.existingSessionMetadata.projectId ?? null;
	const cwd = effectiveRequestedCwd ?? input.existingSessionMetadata.cwd ?? null;

	if (!projectId && !imageBackedProjectExecutor) {
		return {
			ok: false,
			statusCode: 409,
			error: "missing_project_id",
			message: `${input.agentType} requires projectId.`,
		};
	}

	if (!cwd) {
		return {
			ok: false,
			statusCode: 409,
			error: "missing_cwd",
			message: `${input.agentType} requires cwd.`,
		};
	}

	if (!isExistingDirectory(cwd)) {
		return {
			ok: false,
			statusCode: 409,
			error: "invalid_cwd",
			message: `${input.agentType} requires cwd to be an existing project directory.`,
		};
	}

	return {
		ok: true,
		attached: true,
		projectId,
		cwd,
		repoRoot: input.existingSessionMetadata.repoRoot ?? resolveGitRepoRoot(cwd),
		projectImageRef,
		requestedProjectId,
		requestedCwd,
		fixedProjectId,
		fixedProjectCwd,
	};
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

function extractAgentTypeFromSessionPayload(
	payload: Record<string, unknown>,
	sessionMetadata: Record<string, unknown> | null,
): string | null {
	const metadata = sessionMetadata ?? {};
	return (
		extractStringProperty(
			metadata,
			"agent_type",
			"agentType",
		) ??
		extractStringProperty(
			payload,
			"agent_type",
			"agentType",
		) ??
		(() => {
			const agentRecord = extractObjectPropertyRecord(payload, "agent");
			if (!agentRecord) return null;
			return extractStringProperty(agentRecord, "agent_type", "agentType");
		})()
	);
}

function extractNumericProperty(record: Record<string, unknown>, ...keys: string[]): number | null {
	for (const key of keys) {
		const value = normalizeNumericId(record[key]);
		if (value != null) return value;
	}
	return null;
}

function mergeSystemPrompt(base: string | undefined, injected: string): string {
	const parts = [typeof base === "string" ? base.trim() : "", injected.trim()].filter(Boolean);
	return parts.join("\n\n");
}

function normalizeA2AChatMessages(messages: unknown): Array<Record<string, unknown>> | null {
	if (!Array.isArray(messages)) return null;
	const normalized: Array<Record<string, unknown>> = [];
	for (const entry of messages) {
		if (!isPlainObject(entry)) continue;
		const role = extractStringProperty(entry, "role");
		if (!role) continue;
		const rawContent = entry.content;
		const content =
			typeof rawContent === "string"
				? [{ type: "text", text: rawContent }]
				: Array.isArray(rawContent)
					? rawContent
					: null;
		if (!content || extractText(content).trim().length === 0) continue;
		normalized.push({
			...entry,
			role,
			content,
		});
	}
	return normalized.length > 0 ? normalized : null;
}

function normalizeA2AChatRequestBody(
	body: Record<string, unknown>,
): { ok: true; body: Record<string, unknown> } | { ok: false; statusCode: number; error: string; message: string } {
	const runtimeSessionId =
		extractStringProperty(body, "runtime_session_id", "runtimeSessionId", "sessionId") ?? null;
	const normalizedMessages = normalizeA2AChatMessages(body.messages);
	const task = extractStringProperty(body, "task", "message", "input", "prompt", "request");
	if (!normalizedMessages && !task) {
		return {
			ok: false,
			statusCode: 400,
			error: "missing_a2a_task",
			message:
				"A2A chat requests require either canonical messages or a non-empty task, message, input, prompt, or request field.",
		};
	}

	const caller =
		extractObjectPropertyRecord(body, "caller", "caller_metadata", "callerMetadata") ?? {};
	const callerAgentType =
		extractStringProperty(caller, "agent_type", "agentType") ??
		"unknown-agent";
	const responseFormat = normalizeA2AResponseFormat(body.response_format ?? body.responseFormat);
	const context = extractObjectPropertyRecord(body, "context") ?? {};
	const userId =
		extractStringProperty(body, "userId", "user_id", "created_by_user", "createdByUser") ??
		extractStringProperty(context, "userId");
	const mergedContext: Record<string, unknown> = {
		...context,
		surfaceId: "a2a",
		surfaceTitle: "Agent-to-Agent",
		surfaceContextSource: "a2a",
		...(userId ? { userId } : {}),
		a2a: {
			enabled: true,
			caller,
			responseFormat,
		},
	};

	const injectedSystem = buildA2ASystemInstruction({
		callerAgentType,
		responseFormat,
		callerMetadata: caller,
	});

	return {
		ok: true,
		body: {
			...body,
			...(runtimeSessionId ? { runtime_session_id: runtimeSessionId } : {}),
			...(body.newChat !== undefined ? { newChat: body.newChat } : {}),
			...(runtimeSessionId ? { newChat: false } : {}),
			system: mergeSystemPrompt(
				typeof body.system === "string" ? body.system : undefined,
				injectedSystem,
			),
			context: mergedContext,
			tools: {},
			messages:
				normalizedMessages ??
				[
					{
						role: "user",
						content: [
							{
								type: "text",
								text: task,
							},
						],
					},
				],
		},
	};
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

function extractRequestedAgentId(payload: Record<string, unknown>): number | null {
	return (
		extractNumericProperty(payload, "agent_id", "agentId") ??
		(() => {
			const sessionPayload = extractRequestSessionPayload(payload);
			if (!sessionPayload) return null;
			return extractBackendSessionAgentId(sessionPayload);
		})() ??
		(() => {
			const sessionMetadata = extractObjectPropertyRecord(payload, "sessionMetadata", "session_metadata");
			if (!sessionMetadata) return null;
			return extractNumericProperty(sessionMetadata, "agent_id", "agentId");
		})()
	);
}

function extractRequestedAgentUniqueId(payload: Record<string, unknown>): string | null {
	return (
		extractStringProperty(payload, "agent_unique_id", "agentUniqueId") ??
		(() => {
			const sessionPayload = extractRequestSessionPayload(payload);
			if (!sessionPayload) return null;
			const agentRecord = extractObjectPropertyRecord(sessionPayload, "agent");
			return agentRecord ? extractStringProperty(agentRecord, "agent_unique_id", "agentUniqueId") : null;
		})() ??
		(() => {
			const sessionMetadata = extractObjectPropertyRecord(payload, "sessionMetadata", "session_metadata");
			if (!sessionMetadata) return null;
			return extractStringProperty(sessionMetadata, "agent_unique_id", "agentUniqueId");
		})()
	);
}

function extractRequestSessionPayload(payload: Record<string, unknown>): Record<string, unknown> | null {
	return extractObjectPropertyRecord(
		payload,
		"session",
		"agent_session",
		"agentSession",
		"backend_session",
		"backendSession",
		"session_serializer",
		"sessionSerializer",
	);
}

function validateRequestSessionPayloadAuthority(input: {
	sessionPayload: Record<string, unknown>;
	runtimeSessionId: string;
	agentType: string;
}):
	| { ok: true }
	| { ok: false; statusCode: 409; error: string; message: string; details: Record<string, unknown> } {
	const requestedRuntimeSessionId = normalizeNumericId(input.runtimeSessionId);
	const payloadAgentSessionId = extractNumericProperty(
		input.sessionPayload,
		"id",
		"agent_session_id",
		"agentSessionId",
	);
	if (
		requestedRuntimeSessionId != null &&
		payloadAgentSessionId != null &&
		payloadAgentSessionId !== requestedRuntimeSessionId
	) {
		return {
			ok: false,
			statusCode: 409,
			error: "session_payload_mismatch",
			message: "Request-carried session serializer does not match runtime_session_id.",
			details: {
				runtimeSessionId: input.runtimeSessionId,
				payloadAgentSessionId,
			},
		};
	}

	const sessionMetadata = extractObjectPropertyRecord(
		input.sessionPayload,
		"session_metadata",
		"sessionMetadata",
	);
	const payloadAgentType = extractAgentTypeFromSessionPayload(input.sessionPayload, sessionMetadata);
	if (payloadAgentType && payloadAgentType !== input.agentType) {
		return {
			ok: false,
			statusCode: 409,
			error: "session_payload_mismatch",
			message: "Request-carried session serializer does not match requested agentType.",
			details: {
				agentType: input.agentType,
				payloadAgentType,
			},
		};
	}

	return { ok: true };
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

function extractCanonicalA2AEnvelope(...sources: Array<Record<string, unknown> | null | undefined>): A2AEnvelope | null {
	for (const source of sources) {
		if (!source) continue;
		const normalized =
			normalizeA2AEnvelope(source.a2a) ??
			normalizeA2AEnvelope(source.a2a_envelope) ??
			null;
		if (normalized) return normalized;
	}
	return null;
}

function buildRequestA2AEnvelope(input: {
	enabled: boolean;
	body: Record<string, unknown>;
	a2aContext: Record<string, unknown>;
	caller: Record<string, unknown>;
}): A2AEnvelope | null {
	if (!input.enabled) return null;
	const responseFormat = normalizeA2AResponseFormat(
		input.body.response_format ??
			input.body.responseFormat ??
			input.a2aContext.response_format ??
			input.a2aContext.responseFormat,
	);
	return {
		version: 1,
		enabled: true,
		userOrigin: "agent",
		callerAgentType:
			extractStringProperty(input.caller, "agent_type", "agentType") ??
			extractStringProperty(
				input.a2aContext,
				"caller_agent_type",
				"callerAgentType",
			) ??
			null,
		callerMetadata: Object.keys(input.caller).length > 0 ? input.caller : null,
		responseFormat,
		handleUniqueId:
			extractStringProperty(input.body, "handle_unique_id", "handleUniqueId") ??
			extractStringProperty(input.a2aContext, "handle_unique_id", "handleUniqueId") ??
			null,
		callerAgentSessionId:
			extractNumericProperty(input.body, "caller_agent_session_id", "callerAgentSessionId") ??
			extractNumericProperty(input.caller, "agent_session_id", "agentSessionId", "session_id", "sessionId") ??
			null,
		targetAgentSessionId:
			extractNumericProperty(input.body, "target_agent_session_id", "targetAgentSessionId") ??
			extractNumericProperty(input.a2aContext, "target_agent_session_id", "targetAgentSessionId") ??
			extractNumericProperty(input.body, "runtime_session_id", "runtimeSessionId", "sessionId") ??
			extractNumericProperty(input.a2aContext, "runtime_session_id", "runtimeSessionId", "sessionId") ??
			null,
		targetAgentId:
			extractNumericProperty(input.body, "target_agent_id", "targetAgentId") ??
			extractNumericProperty(input.a2aContext, "target_agent_id", "targetAgentId") ??
			null,
	};
}

async function attachHydratedBackendSession(options: {
	runtimeSessionId: string;
	userId: string;
	requestedThreadId: string | null;
	existingMetadata?: SessionMetadata | null;
	log?: (message: string) => void;
}): Promise<
	| { ok: true; hydrated: HydratedBackendSession }
	| { ok: false; error: string; message: string; statusCode: number }
> {
	const normalizedAgentSessionId = normalizeNumericId(options.runtimeSessionId);
	logStructuredEvent({
		component: "astro-stream",
		event: "backend_session_hydration_attempt",
		message: "Attempting backend session hydration from backend authority.",
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
			error: "invalid_runtime_session_id",
			message:
				"The provided runtime_session_id is not a numeric backend AgentSession id, so Astro cannot hydrate it without local session files.",
			statusCode: 400,
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
				message:
					"The backend AgentSession for the provided runtime_session_id was not found, and no local session files exist.",
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

	const metadata = buildSessionMetadataFromBackendSessionPayload({
		sessionKey: options.runtimeSessionId,
		sessionPayload,
		requestedThreadId: options.requestedThreadId,
		existingMetadata: options.existingMetadata ?? null,
	});
	if (!metadata) {
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-stream",
			event: "backend_session_hydration_missing_agent_type",
			message: "Backend session hydration failed because the backend payload had no usable agentType identity.",
			data: {
				agentSessionId: normalizedAgentSessionId,
			},
		});
		return {
			ok: false,
			error: "session_hydration_failed",
			message: "The backend session did not include a valid agentType identity.",
			statusCode: 409,
		};
	}

	logStructuredEvent({
		component: "astro-stream",
		event: "backend_session_hydration_succeeded",
		message: "Backend session hydration succeeded.",
		data: {
			agentSessionId: normalizedAgentSessionId,
			agentId,
			threadId: metadata.threadId,
			agentUniqueId: metadata.agentUniqueId,
			startedAt: metadata.startedAt,
			agentType: metadata.agentType,
			hasSessionModelBinding: Boolean(metadata.sessionModelBinding),
		},
	});

	return {
		ok: true,
		hydrated: {
			agentId,
			agentUniqueId: metadata.agentUniqueId,
			metadata: {
				...metadata,
				agentSessionId: fetched.agentSessionId ?? normalizedAgentSessionId,
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

function isExistingDirectory(candidate: string): boolean {
	try {
		return statSync(candidate).isDirectory();
	} catch {
		return false;
	}
}

function writePromptToTempFile(promptName: string, prompt: string): string {
	const tempDir = mkdtempSync(path.join(tmpdir(), `astro-stream-${promptName.replace(/[^\w.-]+/g, "_")}-`));
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

function getSessionStateDir(): string {
	return path.dirname(sessionDir);
}

function getManifestDir(): string {
	return path.join(getSessionStateDir(), "manifests");
}

function getCheckpointMarkerDir(): string {
	return path.join(getSessionStateDir(), "checkpoints");
}

function getCheckpointLifecycleDir(): string {
	return path.join(getSessionStateDir(), "checkpoint-lifecycle");
}

function getCheckpointManifestPath(sessionKey: string): string {
	return path.join(getManifestDir(), `${sessionKey}.manifest.json`);
}

function getCheckpointLifecyclePath(sessionKey: string): string {
	return path.join(getCheckpointLifecycleDir(), `${sessionKey}.json`);
}

function getSessionOverridesPath(sessionKey: string): string {
	const piAgentDir =
		process.env.PI_CODING_AGENT_DIR?.trim() ||
		path.join(process.env.HOME?.trim() || process.cwd(), ".pi", "agent");
	const overridesRoot =
		process.env.ASTRO_SESSION_OVERRIDES_DIR?.trim() ||
		path.join(piAgentDir, ".astro-session-overrides");
	return path.join(overridesRoot, sessionKey, "settings.json");
}

function readSessionMetadata(sessionKey: string): SessionMetadata | null {
	const metadataPath = getSessionMetadataPath(sessionKey);
	if (!existsSync(metadataPath)) return null;

	try {
		const parsed = JSON.parse(readFileSync(metadataPath, "utf8"));
		if (!parsed || typeof parsed !== "object") return null;
		const rawAgentId =
			(parsed as { agentId?: unknown }).agentId ?? (parsed as { agent_id?: unknown }).agent_id;
		const rawAgentUniqueId =
			(parsed as { agentUniqueId?: unknown }).agentUniqueId ??
			(parsed as { agent_unique_id?: unknown }).agent_unique_id;
		const rawAgentSessionId =
			(parsed as { agentSessionId?: unknown }).agentSessionId ??
			(parsed as { agent_session_id?: unknown }).agent_session_id;
		const rawThreadId =
			(parsed as { threadId?: unknown }).threadId ?? (parsed as { thread_id?: unknown }).thread_id;
		const rawStartedAt =
			(parsed as { startedAt?: unknown }).startedAt ?? (parsed as { started_at?: unknown }).started_at;
		const rawAgentType =
			(parsed as { agentType?: unknown }).agentType ??
			(parsed as { agent_type?: unknown }).agent_type;
		const rawProjectId = (parsed as { projectId?: unknown }).projectId;
		const rawCwd = (parsed as { cwd?: unknown }).cwd;
		const rawRepoRoot = (parsed as { repoRoot?: unknown }).repoRoot;
		const rawProjectImageRef = (parsed as { projectImageRef?: unknown }).projectImageRef;
		const rawSessionModelBinding = (parsed as { sessionModelBinding?: unknown }).sessionModelBinding;
		const rawSessionConfigOverrides = (parsed as { sessionConfigOverrides?: unknown }).sessionConfigOverrides;
		const rawA2A = (parsed as { a2a?: unknown }).a2a;
		const rawHistoryAnnotations = (parsed as { history_annotations?: unknown }).history_annotations;
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
		const normalizedAgentType =
			typeof rawAgentType === "string" && rawAgentType.trim() ? rawAgentType.trim() : null;
		const normalizedProjectId = normalizeProjectId(rawProjectId);
		const normalizedCwd = normalizeProjectCwd(rawCwd);
		const normalizedRepoRoot = normalizeRepoRoot(rawRepoRoot);
		const normalizedProjectImageRef = normalizeProjectImageRef(rawProjectImageRef);
		const normalizedSessionModelBinding = normalizeSessionModelBinding(rawSessionModelBinding);
		const normalizedSessionConfigOverrides = normalizeSessionConfigOverrides(rawSessionConfigOverrides);
		const normalizedA2A = normalizeA2AEnvelope(rawA2A);
		const normalizedHistoryAnnotations = normalizeHistoryAnnotations(rawHistoryAnnotations);
		return {
			agentId: Number.isFinite(normalizedAgentId as number) ? (normalizedAgentId as number) : null,
			agentUniqueId: normalizedAgentUniqueId,
			agentSessionId: Number.isFinite(normalizedAgentSessionId as number)
				? (normalizedAgentSessionId as number)
				: null,
			threadId: normalizedThreadId,
			startedAt: normalizedStartedAt,
			agentType: normalizedAgentType,
			projectId: normalizedProjectId,
			cwd: normalizedCwd,
			repoRoot: normalizedRepoRoot,
			projectImageRef: normalizedProjectImageRef,
			sessionModelBinding: normalizedSessionModelBinding,
			sessionConfigOverrides: normalizedSessionConfigOverrides,
			...(normalizedA2A ? { a2a: normalizedA2A } : {}),
			...(normalizedHistoryAnnotations ? { history_annotations: normalizedHistoryAnnotations } : {}),
		};
	} catch {
		return null;
	}
}

function writeSessionMetadata(sessionKey: string, metadata: SessionMetadata) {
	mkdirSync(sessionDir, { recursive: true });
	const metadataPath = getSessionMetadataPath(sessionKey);
	const nextMetadata: Record<string, unknown> = { ...metadata };
	if (metadata.agentType) nextMetadata.agent_type = metadata.agentType;
	if (metadata.agentId != null) nextMetadata.agent_id = metadata.agentId;
	if (metadata.agentUniqueId) nextMetadata.agent_unique_id = metadata.agentUniqueId;
	if (metadata.agentSessionId != null) nextMetadata.agent_session_id = metadata.agentSessionId;
	if (metadata.threadId) nextMetadata.thread_id = metadata.threadId;
	if (!Object.prototype.hasOwnProperty.call(nextMetadata, "history_annotations")) {
		const existingAnnotations = normalizeHistoryAnnotations(
			readJsonFileObject(metadataPath)?.history_annotations,
		);
		if (existingAnnotations) nextMetadata.history_annotations = existingAnnotations;
	}
	if (!Object.prototype.hasOwnProperty.call(nextMetadata, "a2a")) {
		const existingA2A = normalizeA2AEnvelope(readJsonFileObject(metadataPath)?.a2a);
		if (existingA2A) nextMetadata.a2a = existingA2A;
	}
	writeFileSync(metadataPath, JSON.stringify(nextMetadata, null, 2));
}

function syncRuntimeReportedSessionModelBinding(ctx: RequestContext) {
	const provider = ctx.responseProvider;
	const model = ctx.responseModel;
	if (!provider || !model) return;
	if (doesSessionModelIdentityMatch(ctx.sessionModelBinding, provider, model)) return;

	const nextBinding = rebindSessionModelBindingFromIdentity({
		existingBinding: ctx.sessionModelBinding,
		provider,
		model,
		source: "session",
	});
	if (!nextBinding) return;

	const metadata = readSessionMetadata(ctx.sessionKey);
	const nextMetadata = metadata
		? {
				...metadata,
				sessionModelBinding: nextBinding,
			}
		: null;
	const previousProvider = ctx.sessionModelBinding?.provider ?? metadata?.sessionModelBinding?.provider ?? null;
	const previousModel = ctx.sessionModelBinding?.model ?? metadata?.sessionModelBinding?.model ?? null;

	ctx.sessionModelBinding = nextBinding;
	if (!nextMetadata) return;

	writeSessionMetadata(ctx.sessionKey, nextMetadata);
	logStructuredEvent({
		component: "astro-stream",
		event: "session_model_binding_refreshed_from_runtime",
		message: "Astro refreshed the cached session model binding from the runtime-reported response model.",
		data: {
			sessionKey: ctx.sessionKey,
			threadId: ctx.threadId,
			agentSessionId: ctx.agentSessionId,
			agentType: ctx.agentType,
			previousProvider,
			previousModel,
			nextProvider: nextBinding.provider,
			nextModel: nextBinding.model,
		},
	});
}

function readJsonFileObject(filePath: string): Record<string, unknown> | null {
	if (!existsSync(filePath)) return null;
	try {
		const parsed = JSON.parse(readFileSync(filePath, "utf8"));
		return isPlainObject(parsed) ? parsed : null;
	} catch {
		return null;
	}
}

function normalizeHistoryAnnotations(value: unknown): HistoryAnnotations | null {
	if (!isPlainObject(value) || value.version !== 1 || !Array.isArray(value.assistant_messages)) {
		return null;
	}

	const assistantMessages: HistoryAnnotation[] = [];
	for (const rawAnnotation of value.assistant_messages) {
		if (!isPlainObject(rawAnnotation) || rawAnnotation.had_reasoning !== true) continue;
		const assistantOrdinal = normalizeNumericId(rawAnnotation.assistant_ordinal);
		if (assistantOrdinal == null || assistantOrdinal <= 0) continue;
		const piEntryId =
			typeof rawAnnotation.pi_entry_id === "string" && rawAnnotation.pi_entry_id.trim()
				? rawAnnotation.pi_entry_id.trim()
				: null;
		const streamMessageId =
			typeof rawAnnotation.stream_message_id === "string" && rawAnnotation.stream_message_id.trim()
				? rawAnnotation.stream_message_id.trim()
				: null;
		const reasoningStartedAt =
			typeof rawAnnotation.reasoning_started_at === "string" && rawAnnotation.reasoning_started_at.trim()
				? rawAnnotation.reasoning_started_at.trim()
				: null;
		const reasoningCompletedAt =
			typeof rawAnnotation.reasoning_completed_at === "string" && rawAnnotation.reasoning_completed_at.trim()
				? rawAnnotation.reasoning_completed_at.trim()
				: null;
		assistantMessages.push({
			assistant_ordinal: assistantOrdinal,
			pi_entry_id: piEntryId,
			stream_message_id: streamMessageId,
			had_reasoning: true,
			reasoning_text_persisted: rawAnnotation.reasoning_text_persisted === true,
			reasoning_started_at: reasoningStartedAt,
			reasoning_completed_at: reasoningCompletedAt,
		});
	}

	return {
		version: 1,
		assistant_messages: assistantMessages,
	};
}

function updateSessionHistoryAnnotations(
	sessionKey: string,
	update: (annotations: HistoryAnnotations) => HistoryAnnotations,
) {
	const metadataPath = getSessionMetadataPath(sessionKey);
	const metadata = readJsonFileObject(metadataPath) ?? {};
	const currentAnnotations =
		normalizeHistoryAnnotations(metadata.history_annotations) ??
		({
			version: 1,
			assistant_messages: [],
		} satisfies HistoryAnnotations);
	const nextAnnotations = update(currentAnnotations);
	metadata.history_annotations = nextAnnotations;
	mkdirSync(sessionDir, { recursive: true });
	writeFileSync(metadataPath, JSON.stringify(metadata, null, 2));
}

function readCheckpointManifest(sessionKey: string): CheckpointManifest | null {
	const parsed = readJsonFileObject(getCheckpointManifestPath(sessionKey));
	if (!parsed) return null;
	const checkpointVersion = normalizeNumericId(parsed.checkpoint_version);
	const sessionId = typeof parsed.session_id === "string" && parsed.session_id.trim() ? parsed.session_id.trim() : null;
	const bundleHash =
		typeof parsed.bundle_hash === "string" && parsed.bundle_hash.trim() ? parsed.bundle_hash.trim() : "";
	const holderId =
		typeof parsed.lease_holder_id === "string" && parsed.lease_holder_id.trim()
			? parsed.lease_holder_id.trim()
			: null;
	const leaseToken =
		typeof parsed.lease_token === "string" && parsed.lease_token.trim() ? parsed.lease_token.trim() : null;
	const leaseExpiresAt =
		typeof parsed.lease_expires_at === "string" && parsed.lease_expires_at.trim()
			? parsed.lease_expires_at.trim()
			: null;
	const restoredAt =
		typeof parsed.restored_at === "string" && parsed.restored_at.trim()
			? parsed.restored_at.trim()
			: null;
	if (!sessionId || checkpointVersion == null || !holderId || !leaseToken || !leaseExpiresAt) return null;
	return {
		session_id: sessionId,
		checkpoint_version: checkpointVersion,
		restored_at: restoredAt ?? new Date().toISOString(),
		bundle_hash: bundleHash,
		lease_holder_id: holderId,
		lease_token: leaseToken,
		lease_expires_at: leaseExpiresAt,
	};
}

function writeCheckpointManifest(
	sessionKey: string,
		input: {
			checkpointVersion: number;
			bundleHash: string;
			holderId: string;
			leaseToken: string;
			leaseExpiresAt: string;
			restoredAt?: string;
		},
	) {
	mkdirSync(getManifestDir(), { recursive: true });
	const existingManifest = readCheckpointManifest(sessionKey);
	const manifest: CheckpointManifest = {
		session_id: sessionKey,
		checkpoint_version: input.checkpointVersion,
		restored_at: input.restoredAt ?? existingManifest?.restored_at ?? new Date().toISOString(),
		bundle_hash: input.bundleHash,
		lease_holder_id: input.holderId,
		lease_token: input.leaseToken,
		lease_expires_at: input.leaseExpiresAt,
	};
	writeFileSync(getCheckpointManifestPath(sessionKey), JSON.stringify(manifest, null, 2));
}

function readCheckpointLifecycleState(sessionKey: string): CheckpointLifecycleState | null {
	const parsed = readJsonFileObject(getCheckpointLifecyclePath(sessionKey));
	if (!parsed) return null;
	const state =
		parsed.state === "running" ||
		parsed.state === "finalizing_checkpoint" ||
		parsed.state === "idle"
			? parsed.state
			: null;
	const updatedAt =
		typeof parsed.updated_at === "string" && parsed.updated_at.trim() ? parsed.updated_at.trim() : null;
	if (!state || !updatedAt) return null;
	return {
		session_id:
			typeof parsed.session_id === "string" && parsed.session_id.trim()
				? parsed.session_id.trim()
				: sessionKey,
		state,
		updated_at: updatedAt,
		lease_holder_id:
			typeof parsed.lease_holder_id === "string" && parsed.lease_holder_id.trim()
				? parsed.lease_holder_id.trim()
				: null,
		lease_token:
			typeof parsed.lease_token === "string" && parsed.lease_token.trim()
				? parsed.lease_token.trim()
				: null,
		checkpoint_version: normalizeNumericId(parsed.checkpoint_version),
		bundle_hash:
			typeof parsed.bundle_hash === "string" && parsed.bundle_hash.trim()
				? parsed.bundle_hash.trim()
				: null,
		reason: typeof parsed.reason === "string" && parsed.reason.trim() ? parsed.reason.trim() : null,
		marker_file:
			typeof parsed.marker_file === "string" && parsed.marker_file.trim()
				? parsed.marker_file.trim()
				: null,
	};
}

function writeCheckpointLifecycleState(
	sessionKey: string,
	input: Omit<CheckpointLifecycleState, "session_id" | "updated_at"> & { updated_at?: string },
) {
	mkdirSync(getCheckpointLifecycleDir(), { recursive: true });
	const state: CheckpointLifecycleState = {
		session_id: sessionKey,
		updated_at: input.updated_at ?? new Date().toISOString(),
		state: input.state,
		lease_holder_id: input.lease_holder_id,
		lease_token: input.lease_token,
		checkpoint_version: input.checkpoint_version,
		bundle_hash: input.bundle_hash,
		reason: input.reason,
		marker_file: input.marker_file ?? null,
	};
	writeFileSync(getCheckpointLifecyclePath(sessionKey), JSON.stringify(state, null, 2));
}

function clearCheckpointLifecycleState(sessionKey: string) {
	rmSync(getCheckpointLifecyclePath(sessionKey), { force: true });
}

function resolveCheckpointHolderId(): string {
	const configured = process.env.ASTRO_CHECKPOINT_HOLDER_ID?.trim();
	if (configured) return configured;
	const podUid = process.env.POD_UID?.trim() || process.env.K8S_POD_UID?.trim();
	if (podUid) return `pod/${podUid}`;
	const hostname = process.env.HOSTNAME?.trim();
	if (hostname) return `pod/${hostname}`;
	return `process/${process.pid}`;
}

function resolveCheckpointLeaseTtlSeconds(): number {
	const parsed = Number.parseInt(process.env.ASTRO_CHECKPOINT_LEASE_TTL_SECONDS ?? "120", 10);
	return Number.isFinite(parsed) && parsed > 0 ? parsed : 120;
}

function resolveCheckpointFinalizeWaitMs(): number {
	const parsed = Number.parseInt(process.env.ASTRO_CHECKPOINT_FINALIZE_WAIT_MS ?? "15000", 10);
	return Number.isFinite(parsed) && parsed >= 0 ? parsed : 15000;
}

function checkpointLifecycleStateIsFresh(state: CheckpointLifecycleState): boolean {
	const updatedAt = Date.parse(state.updated_at);
	if (!Number.isFinite(updatedAt)) return false;
	const maxAgeMs = Math.max(resolveCheckpointLeaseTtlSeconds() * 1000 * 2, 300000);
	return updatedAt > Date.now() - maxAgeMs;
}

function checkpointFinalizationCanBeRecovered(
	sessionKey: string,
	state: CheckpointLifecycleState,
	waitMs: number,
): { ok: true; reason: string } | { ok: false } {
	const stateUpdatedAt = Date.parse(state.updated_at);
	const stateAgeMs = Number.isFinite(stateUpdatedAt) ? Date.now() - stateUpdatedAt : Number.POSITIVE_INFINITY;
	const markerMissing = state.marker_file
		? !existsSync(path.join(getCheckpointMarkerDir(), state.marker_file))
		: true;
	const manifest = readCheckpointManifest(sessionKey);

	if (!checkpointLifecycleStateIsFresh(state)) {
		return { ok: true, reason: "stale_lifecycle_state" };
	}
	if (manifest) {
		const sameLease = !state.lease_token || manifest.lease_token === state.lease_token;
		const leaseExpiresAt = Date.parse(manifest.lease_expires_at);
		if (sameLease && Number.isFinite(leaseExpiresAt) && leaseExpiresAt <= Date.now()) {
			return { ok: true, reason: "manifest_lease_expired" };
		}
		if (
			sameLease &&
			markerMissing &&
			state.checkpoint_version != null &&
			manifest.checkpoint_version > state.checkpoint_version &&
			stateAgeMs >= waitMs
		) {
			return { ok: true, reason: "manifest_advanced_past_finalizing_state" };
		}
	}
	if (markerMissing && stateAgeMs >= Math.max(waitMs * 2, 30000)) {
		return { ok: true, reason: "missing_marker_after_grace" };
	}

	return { ok: false };
}

function recoverCheckpointFinalizationIfDead(
	sessionKey: string,
	state: CheckpointLifecycleState,
	waitMs: number,
): boolean {
	const recovery = checkpointFinalizationCanBeRecovered(sessionKey, state, waitMs);
	if (recovery.ok === false) return false;
	clearCheckpointLifecycleState(sessionKey);
	logStructuredEvent({
		severity: "WARNING",
		component: "astro-stream",
		event: "checkpoint_finalization_recovered",
		message: "Astro cleared a dead checkpoint finalization state before launching Pi.",
		data: {
			sessionKey,
			reason: recovery.reason,
			finalizingState: state,
			manifest: readCheckpointManifest(sessionKey),
		},
	});
	return true;
}

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForCheckpointFinalization(sessionKey: string): Promise<
	| { ok: true }
	| { ok: false; state: CheckpointLifecycleState | null }
> {
	const waitMs = resolveCheckpointFinalizeWaitMs();
	const startedAt = Date.now();
	while (true) {
		const state = readCheckpointLifecycleState(sessionKey);
		if (!state || state.state !== "finalizing_checkpoint") {
			return { ok: true };
		}
		if (recoverCheckpointFinalizationIfDead(sessionKey, state, waitMs)) return { ok: true };
		if (Date.now() - startedAt >= waitMs) {
			if (recoverCheckpointFinalizationIfDead(sessionKey, state, waitMs)) return { ok: true };
			return { ok: false, state };
		}
		await sleep(250);
	}
}

function buildCheckpointFinalizingErrorEvent(
	state: CheckpointLifecycleState | null,
): Extract<StreamEvent, { type: "error" }> {
	const detail = "The previous turn is still finalizing its checkpoint. Retry the chat request shortly.";
	return {
		type: "error",
		error: `Checkpoint finalization pending: ${detail}`,
		error_source: "checkpoint",
		status: 409,
		error_code: "session_checkpoint_finalizing",
		error_detail: detail,
		field_errors: null,
		forensics: {
			backend_request_url: null,
			backend_response_text: null,
			backend_response_body: state,
			backend_checkpoint_version: state?.checkpoint_version ?? null,
			backend_bundle_hash: state?.bundle_hash ?? null,
		},
	};
}

function shouldRenewManifestLease(manifest: CheckpointManifest | null, holderId: string): manifest is CheckpointManifest {
	if (!manifest || manifest.lease_holder_id !== holderId) return false;
	const expiresAt = Date.parse(manifest.lease_expires_at);
	return Number.isFinite(expiresAt) && expiresAt > Date.now() + 5000;
}

function checkpointManifestIsCurrent(
	manifest: CheckpointManifest | null,
	lease: CheckpointLeaseResponse,
	sessionKey: string,
): boolean {
	return Boolean(
		manifest &&
			manifest.session_id === sessionKey &&
			manifest.checkpoint_version === lease.checkpoint_version &&
			manifest.bundle_hash === lease.bundle_hash &&
			existsSync(getSessionPath(sessionKey)) &&
			existsSync(getSessionMetadataPath(sessionKey)),
	);
}

function materializeCheckpointBundle(ctx: RequestContext, bundle: CheckpointBundle) {
	mkdirSync(sessionDir, { recursive: true });
	writeFileSync(getSessionPath(ctx.sessionKey), bundle.pi_session_jsonl);

	const existingMetadata = readJsonFileObject(getSessionMetadataPath(ctx.sessionKey)) ?? {};
	const metadata =
		isPlainObject(bundle.astro_metadata_json) && Object.keys(bundle.astro_metadata_json).length > 0
			? {
					...existingMetadata,
					...bundle.astro_metadata_json,
			  }
			: existingMetadata;
	if (Object.keys(metadata).length > 0) {
		writeFileSync(getSessionMetadataPath(ctx.sessionKey), JSON.stringify(metadata, null, 2));
	}

	const threadBinding =
		isPlainObject(bundle.thread_binding_json) && Object.keys(bundle.thread_binding_json).length > 0
			? bundle.thread_binding_json
			: {
					threadId: ctx.threadId,
					runtimeSessionId: ctx.sessionKey,
					updatedAt: new Date().toISOString(),
			  };
	const threadId =
		typeof threadBinding.threadId === "string" && threadBinding.threadId.trim()
			? threadBinding.threadId.trim()
			: ctx.threadId;
	writeFileSync(getThreadBindingPath(threadId), JSON.stringify(threadBinding, null, 2));

	const overridesPath = getSessionOverridesPath(ctx.sessionKey);
	if (isPlainObject(bundle.session_overrides_json)) {
		mkdirSync(path.dirname(overridesPath), { recursive: true });
		writeFileSync(overridesPath, JSON.stringify(bundle.session_overrides_json, null, 2));
	} else if (bundle.session_overrides_json === null) {
		rmSync(path.dirname(overridesPath), { recursive: true, force: true });
	}
}

function logPiSessionRepairs(input: {
	sessionKey: string;
	threadId: string;
	agentSessionId: number | null;
	phase: string;
	source: StreamErrorSource;
	repairs: PiSessionJsonlRepair[];
}) {
	if (input.repairs.length === 0) return;
	logStructuredEvent({
		severity: "WARNING",
		component: "astro-stream",
		event: "pi_session_history_repaired",
		message: "Astro repaired replayable Pi session history corruption before continuing.",
		data: {
			sessionKey: input.sessionKey,
			threadId: input.threadId,
			agentSessionId: input.agentSessionId,
			phase: input.phase,
			errorSource: input.source,
			repairCount: input.repairs.length,
			repairs: input.repairs.map((repair) =>
				repair.kind === "synthetic_tool_call"
					? {
							kind: repair.kind,
							orphanedEntryId: repair.orphanedEntryId,
							syntheticEntryId: repair.syntheticEntryId,
							toolCallId: repair.toolCallId,
							toolName: repair.toolName,
					  }
					: {
							kind: repair.kind,
							entryId: repair.entryId,
							missingParentId: repair.missingParentId,
					  },
			),
		},
	});
}

function normalizePiSessionJsonlForRuntime(input: {
	sessionKey: string;
	threadId: string;
	agentSessionId: number | null;
	phase: string;
	piSessionJsonl: string;
	source: StreamErrorSource;
}):
	| { ok: true; piSessionJsonl: string; repairs: PiSessionJsonlRepair[]; changed: boolean }
	| { ok: false; errorEvent: Extract<StreamEvent, { type: "error" }> } {
	try {
		const repaired = repairPiSessionJsonlCurrentBranch(input.piSessionJsonl);
		logPiSessionRepairs({
			sessionKey: input.sessionKey,
			threadId: input.threadId,
			agentSessionId: input.agentSessionId,
			phase: input.phase,
			source: input.source,
			repairs: repaired.repairs,
		});
		validatePiSessionJsonlCurrentBranch(repaired.piSessionJsonl);
		return {
			ok: true,
			piSessionJsonl: repaired.piSessionJsonl,
			repairs: repaired.repairs,
			changed: repaired.changed,
		};
	} catch (error) {
		const formatted = formatPiSessionValidationFailure(error);
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-stream",
			event: "pi_session_validation_failed",
			message: "Astro rejected a corrupt Pi session history branch.",
			data: {
				sessionKey: input.sessionKey,
				threadId: input.threadId,
				agentSessionId: input.agentSessionId,
				phase: input.phase,
				error: formatted.raw,
				errorDetail: formatted.detail,
				errorSource: input.source,
				corruptionKind: formatted.kind,
				corruptedEntryId: formatted.entryId,
				corruptedToolCallId: formatted.toolCallId,
			},
		});
		return {
			ok: false,
			errorEvent: buildCorruptPiSessionHistoryErrorEvent(error, input.source),
		};
	}
}

function stopCheckpointLeaseRenewal(ctx: RequestContext) {
	if (ctx.checkpointLease?.renewTimer) {
		clearInterval(ctx.checkpointLease.renewTimer);
		ctx.checkpointLease.renewTimer = null;
	}
}

function startCheckpointLeaseRenewal(ctx: RequestContext, client: SessionCheckpointClient, ttlSeconds: number) {
	stopCheckpointLeaseRenewal(ctx);
	if (!ctx.checkpointLease || ctx.agentSessionId == null) return;

	const renewIntervalMs = Math.max(5000, Math.floor((ttlSeconds * 1000) / 2));
	ctx.checkpointLease.renewTimer = setInterval(() => {
		const activeLease = ctx.checkpointLease;
		if (!activeLease || ctx.finished || ctx.agentSessionId == null) {
			stopCheckpointLeaseRenewal(ctx);
			return;
		}
		void client
			.renewLease({
				agentSessionId: ctx.agentSessionId,
				holderId: activeLease.holderId,
				leaseToken: activeLease.leaseToken,
				ttlSeconds,
				leasePurpose: "runtime_run",
			})
			.then((result) => {
				if (!ctx.checkpointLease || ctx.finished) return;
				if (result.ok === false) {
					const errorMessage = "error" in result ? result.error : "Checkpoint lease renewal failed.";
					logStructuredEvent({
						severity: "ERROR",
						component: "astro-stream",
						event: "checkpoint_lease_renew_failed",
						message: "Checkpoint lease renewal failed; stopping the active Pi process.",
						data: {
							sessionKey: ctx.sessionKey,
							agentSessionId: ctx.agentSessionId,
							...checkpointFailureDiagnostics(result),
							error: errorMessage,
						},
					});
					stopCheckpointLeaseRenewal(ctx);
					stopActivePiProcess(ctx);
					if (!ctx.finished) {
						writeChunk(
							ctx,
							buildBackendFailureErrorEvent(
								"Checkpoint lease renewal failed; the active session was stopped to prevent concurrent writes",
								result,
								"checkpoint",
							),
						);
						writeDone(ctx);
					}
					return;
				}
				ctx.checkpointLease.leaseExpiresAt = result.body.lease_expires_at;
				ctx.checkpointLease.checkpointVersion = result.body.checkpoint_version;
				ctx.checkpointLease.bundleHash = result.body.bundle_hash;
				updateActiveStreamSession(ctx, {
					checkpointVersion: result.body.checkpoint_version,
					bundleHash: result.body.bundle_hash,
				});
				writeCheckpointManifest(ctx.sessionKey, {
					checkpointVersion: result.body.checkpoint_version,
					bundleHash: result.body.bundle_hash,
					holderId: activeLease.holderId,
					leaseToken: activeLease.leaseToken,
					leaseExpiresAt: result.body.lease_expires_at,
				});
				if (result.body.cancel_requested === true) {
					beginActiveRunCancellation(ctx, {
						cancellationId: result.body.cancellation?.cancellation_id ?? null,
						reason: result.body.cancellation?.reason ?? "user_requested",
						message: result.body.cancellation?.message ?? null,
					});
				}
			})
			.catch((error) => {
				const message = error instanceof Error ? error.message : String(error);
				logStructuredEvent({
					severity: "ERROR",
					component: "astro-stream",
					event: "checkpoint_lease_renew_error",
					message: "Checkpoint lease renewal failed with an unexpected error.",
					data: {
						sessionKey: ctx.sessionKey,
						agentSessionId: ctx.agentSessionId,
						error: message,
					},
				});
				stopCheckpointLeaseRenewal(ctx);
				stopActivePiProcess(ctx);
				if (!ctx.finished) {
					writeChunk(ctx, {
						type: "error",
						error: "Checkpoint lease renewal failed; the active session was stopped to prevent concurrent writes.",
						error_source: "checkpoint",
					});
					writeDone(ctx);
				}
			});
	}, renewIntervalMs);
}

function extractCheckpointErrorCode(body: unknown): string | null {
	return isPlainObject(body) ? extractStringProperty(body, "error_code", "errorCode") : null;
}

type BackendFailureLike = {
	status?: number | null;
	statusCode?: number | null;
	error?: string | null;
	message?: string | null;
	body?: unknown;
	responseText?: string | null;
	url?: string | null;
	errorCode?: string | null;
	errorDetail?: string | null;
	fieldErrors?: unknown;
};

type StreamErrorForensics = NonNullable<Extract<StreamEvent, { type: "error" }>["forensics"]>;

function extractBackendFailureFields(result: BackendFailureLike): {
	status: number | null;
	errorCode: string | null;
	errorDetail: string | null;
	fieldErrors: unknown;
	explicitError: string | null;
} {
	const body = isPlainObject(result.body) ? result.body : null;
	const status = result.status ?? result.statusCode ?? null;
	const errorCode =
		result.errorCode ?? (body ? extractStringProperty(body, "error_code", "errorCode") : null);
	const errorDetail =
		result.errorDetail ??
		(body ? extractStringProperty(body, "error_detail", "errorDetail", "detail", "message", "error") : null);
	const fieldErrors =
		result.fieldErrors ?? (body ? (body.field_errors ?? body.fieldErrors) : undefined);
	const explicitError = errorDetail || errorCode || result.message || result.error || null;
	return { status, errorCode, errorDetail, fieldErrors, explicitError };
}

function buildBackendFailureForensics(result: BackendFailureLike): StreamErrorForensics {
	const body = isPlainObject(result.body) ? result.body : null;
	return {
		backend_request_url: result.url ?? null,
		backend_response_text: result.responseText ?? null,
		backend_response_body: result.body ?? null,
		backend_checkpoint_version: body?.checkpoint_version ?? null,
		backend_bundle_hash: body?.bundle_hash ?? null,
	};
}

function backendFailureDiagnostics(result: BackendFailureLike): Record<string, unknown> {
	const fields = extractBackendFailureFields(result);
	const forensics = buildBackendFailureForensics(result);
	return {
		status: fields.status,
		error: result.error,
		backend_request_url: forensics.backend_request_url,
		backend_response_body: forensics.backend_response_body,
		backend_response_text: forensics.backend_response_text,
		backend_error_code: fields.errorCode,
		backend_error_detail: fields.errorDetail,
		backend_field_errors: fields.fieldErrors ?? null,
		backend_checkpoint_version: forensics.backend_checkpoint_version,
		backend_bundle_hash: forensics.backend_bundle_hash,
	};
}

function checkpointFailureDiagnostics(
	result: Extract<SessionCheckpointClientResult<unknown>, { ok: false }>,
): Record<string, unknown> {
	return backendFailureDiagnostics(result);
}

function checkpointLeaseRenewCanFallbackToAcquire(
	result: Extract<SessionCheckpointClientResult<unknown>, { ok: false }>,
): boolean {
	const code = extractCheckpointErrorCode(result.body);
	return (
		code === "checkpoint_lease_missing" ||
		code === "checkpoint_lease_expired" ||
		code === "checkpoint_lease_token_mismatch"
	);
}

function buildBackendFailureErrorEvent(
	prefix: string,
	result: BackendFailureLike,
	errorSource: StreamErrorSource = "backend",
): Extract<StreamEvent, { type: "error" }> {
	const fields = extractBackendFailureFields(result);

	return {
		type: "error",
		error: fields.explicitError ? `${prefix}: ${fields.explicitError}` : prefix,
		error_source: errorSource,
		status: fields.status,
		error_code: fields.errorCode,
		error_detail: fields.errorDetail,
		field_errors: fields.fieldErrors ?? null,
		forensics: buildBackendFailureForensics(result),
	};
}

function checkpointLatestRouteMissing(
	result: Extract<SessionCheckpointClientResult<CheckpointLatestResponse>, { ok: false }>,
): boolean {
	const responseText = result.responseText ?? "";
	return (
		result.status === 404 &&
		responseText.includes("Page not found") &&
		responseText.includes("/checkpoint/latest/")
	);
}

async function fetchCheckpointForHistoryHydration(input: {
	agentSessionId: number;
	sessionKey: string;
}): Promise<SessionCheckpointClientResult<CheckpointLatestResponse>> {
	const client = new SessionCheckpointClient({
		env: process.env,
		log: (message) => console.log(`[astro-stream] ${message}`),
	});
	const latestCheckpoint = await client.latest({ agentSessionId: input.agentSessionId });
	if (latestCheckpoint.ok === true) {
		return latestCheckpoint;
	}
	if (!checkpointLatestRouteMissing(latestCheckpoint)) {
		return latestCheckpoint;
	}

	const holderId = `${resolveCheckpointHolderId()}/history`;
	const ttlSeconds = Math.max(30, Math.min(resolveCheckpointLeaseTtlSeconds(), 120));
	logStructuredEvent({
		severity: "WARNING",
		component: "astro-stream",
		event: "checkpoint_latest_missing_restore_fallback",
		message: "Backend checkpoint/latest route is missing; Astro is using lease + restore for history hydration.",
		data: {
			sessionId: input.sessionKey,
			agentSessionId: input.agentSessionId,
			latestUrl: latestCheckpoint.url,
		},
	});

	const leaseResult = await client.acquireLease({
		agentSessionId: input.agentSessionId,
		holderId,
		ttlSeconds,
		leasePurpose: "read_restore",
	});
	if (leaseResult.ok === false) {
		return {
			ok: false,
			status: leaseResult.status,
			error: leaseResult.error,
			body: leaseResult.body,
			responseText: leaseResult.responseText,
			url: leaseResult.url,
		};
	}

	let restoreResult: SessionCheckpointClientResult<CheckpointLatestResponse>;
	try {
		restoreResult = await client.restore({
			agentSessionId: input.agentSessionId,
			holderId,
			leaseToken: leaseResult.body.lease_token,
		});
	} finally {
		client
			.releaseLease({
				agentSessionId: input.agentSessionId,
				holderId,
				leaseToken: leaseResult.body.lease_token,
				reason: "history_hydration",
			})
			.then((releaseResult) => {
				if (releaseResult.ok === false) {
					logStructuredEvent({
						severity: "WARNING",
						component: "astro-stream",
						event: "checkpoint_history_restore_release_failed",
						message: "History hydration restore fallback could not release its checkpoint lease.",
						data: {
							sessionId: input.sessionKey,
							agentSessionId: input.agentSessionId,
							status: releaseResult.status,
							error: releaseResult.error,
							backendResponseText: releaseResult.responseText,
							backendResponseBody: releaseResult.body,
						},
					});
				}
			})
			.catch((error) => {
				logStructuredEvent({
					severity: "WARNING",
					component: "astro-stream",
					event: "checkpoint_history_restore_release_error",
					message: "History hydration restore fallback hit an unexpected error while releasing its lease.",
					data: {
						sessionId: input.sessionKey,
						agentSessionId: input.agentSessionId,
						error: error instanceof Error ? error.message : String(error),
					},
				});
			});
	}

	return restoreResult;
}

function buildSessionMetadataFromBackendCheckpoint(input: {
	sessionKey: string;
	agentSessionId: number;
	sessionPayload: Record<string, unknown>;
	checkpointBundle: CheckpointBundle;
	requestedThreadId: string | null;
}): SessionMetadata {
	const sessionMetadata = extractObjectPropertyRecord(input.sessionPayload, "session_metadata", "sessionMetadata");
	const bundleMetadata = isPlainObject(input.checkpointBundle.astro_metadata_json)
		? input.checkpointBundle.astro_metadata_json
		: {};
	const historyAnnotations = normalizeHistoryAnnotations(bundleMetadata.history_annotations);
	const agentRecord = extractObjectPropertyRecord(input.sessionPayload, "agent");
	const agentType = extractAgentTypeFromSessionPayload(input.sessionPayload, sessionMetadata);
	const agentId =
		extractBackendSessionAgentId(input.sessionPayload) ??
		extractNumericProperty(bundleMetadata, "agentId", "agent_id");
	const agentUniqueId =
		(agentRecord ? extractStringProperty(agentRecord, "agent_unique_id", "agentUniqueId") : null) ??
		extractStringProperty(bundleMetadata, "agentUniqueId", "agent_unique_id");
	const threadId =
		extractStringProperty(bundleMetadata, "threadId", "thread_id") ??
		extractBackendSessionThreadId(input.sessionPayload, sessionMetadata, input.requestedThreadId) ??
		input.sessionKey;
	const startedAt =
		extractStringProperty(bundleMetadata, "startedAt", "started_at") ??
		extractStringProperty(input.sessionPayload, "started_at", "startedAt") ??
		extractStringProperty(sessionMetadata ?? {}, "started_at", "startedAt");
	const a2a = extractCanonicalA2AEnvelope(bundleMetadata, sessionMetadata, input.sessionPayload);

	return {
		agentId,
		agentUniqueId,
		agentSessionId: input.agentSessionId,
		threadId,
		startedAt,
		agentType,
		projectId: normalizeProjectId(bundleMetadata.projectId ?? sessionMetadata?.project_id),
		cwd: normalizeProjectCwd(bundleMetadata.cwd ?? sessionMetadata?.project_cwd),
		repoRoot: normalizeRepoRoot(bundleMetadata.repoRoot ?? sessionMetadata?.project_repo_root),
		projectImageRef: normalizeProjectImageRef(
			bundleMetadata.projectImageRef ?? bundleMetadata.project_image_ref ?? sessionMetadata?.project_image_ref,
		),
		sessionModelBinding: normalizeSessionModelBinding(
			bundleMetadata.sessionModelBinding ?? sessionMetadata?.session_model_binding,
		),
		sessionConfigOverrides: normalizeSessionConfigOverrides(
			bundleMetadata.sessionConfigOverrides ?? sessionMetadata?.session_config_overrides,
		),
		...(a2a ? { a2a } : {}),
		...(historyAnnotations ? { history_annotations: historyAnnotations } : {}),
	};
}

function buildSessionMetadataFromRequestSessionPayload(input: {
	sessionKey: string;
	sessionPayload: Record<string, unknown>;
	requestedThreadId: string | null;
	fallbackAgentType: string;
	existingMetadata?: SessionMetadata | null;
}): SessionMetadata {
	const sessionMetadata = extractObjectPropertyRecord(input.sessionPayload, "session_metadata", "sessionMetadata");
	const agentRecord = extractObjectPropertyRecord(input.sessionPayload, "agent");
	const agentType =
		extractAgentTypeFromSessionPayload(input.sessionPayload, sessionMetadata) ??
		input.existingMetadata?.agentType ??
		input.fallbackAgentType;
	const agentId = extractBackendSessionAgentId(input.sessionPayload) ?? input.existingMetadata?.agentId ?? null;
	const agentUniqueId =
		(agentRecord ? extractStringProperty(agentRecord, "agent_unique_id", "agentUniqueId") : null) ??
		input.existingMetadata?.agentUniqueId ??
		null;
	const threadId =
		extractBackendSessionThreadId(input.sessionPayload, sessionMetadata, input.requestedThreadId) ??
		input.existingMetadata?.threadId ??
		input.sessionKey;
	const startedAt =
		extractStringProperty(input.sessionPayload, "started_at", "startedAt") ??
		extractStringProperty(sessionMetadata ?? {}, "started_at", "startedAt") ??
		input.existingMetadata?.startedAt ??
		null;
	const normalizedAgentSessionId =
		normalizeNumericId(
			extractNumericProperty(input.sessionPayload, "id", "agent_session_id", "agentSessionId") ?? input.sessionKey,
		) ?? input.existingMetadata?.agentSessionId ?? null;
	const a2a =
		extractCanonicalA2AEnvelope(sessionMetadata, input.sessionPayload) ??
		input.existingMetadata?.a2a ??
		null;

	return {
		agentId,
		agentUniqueId,
		agentSessionId: normalizedAgentSessionId,
		threadId,
		startedAt,
		agentType,
		projectId: normalizeProjectId(sessionMetadata?.project_id ?? input.existingMetadata?.projectId),
		cwd: normalizeProjectCwd(sessionMetadata?.project_cwd ?? input.existingMetadata?.cwd),
		repoRoot: normalizeRepoRoot(sessionMetadata?.project_repo_root ?? input.existingMetadata?.repoRoot),
		projectImageRef: normalizeProjectImageRef(
			sessionMetadata?.project_image_ref ?? input.existingMetadata?.projectImageRef,
		),
		sessionModelBinding: deriveSessionModelBindingFromSessionPayload({
			sessionPayload: input.sessionPayload,
			existingBinding: input.existingMetadata?.sessionModelBinding ?? null,
		}),
		sessionConfigOverrides: normalizeSessionConfigOverrides(
			sessionMetadata?.session_config_overrides ?? input.existingMetadata?.sessionConfigOverrides,
		),
		...(a2a ? { a2a } : {}),
	};
}

function buildSessionMetadataFromBackendSessionPayload(input: {
	sessionKey: string;
	sessionPayload: Record<string, unknown>;
	requestedThreadId: string | null;
	existingMetadata?: SessionMetadata | null;
}): SessionMetadata | null {
	const sessionMetadata = extractObjectPropertyRecord(input.sessionPayload, "session_metadata", "sessionMetadata");
	const agentRecord = extractObjectPropertyRecord(input.sessionPayload, "agent");
	const agentType =
		extractAgentTypeFromSessionPayload(input.sessionPayload, sessionMetadata) ??
		input.existingMetadata?.agentType ??
		null;
	if (!agentType) return null;

	const agentId = extractBackendSessionAgentId(input.sessionPayload) ?? input.existingMetadata?.agentId ?? null;
	const agentUniqueId =
		(agentRecord ? extractStringProperty(agentRecord, "agent_unique_id", "agentUniqueId") : null) ??
		input.existingMetadata?.agentUniqueId ??
		null;
	const threadId =
		extractBackendSessionThreadId(input.sessionPayload, sessionMetadata, input.requestedThreadId) ??
		input.existingMetadata?.threadId ??
		input.sessionKey;
	const startedAt =
		extractStringProperty(input.sessionPayload, "started_at", "startedAt") ??
		extractStringProperty(sessionMetadata ?? {}, "started_at", "startedAt") ??
		input.existingMetadata?.startedAt ??
		null;
	const normalizedAgentSessionId =
		normalizeNumericId(
			extractNumericProperty(input.sessionPayload, "id", "agent_session_id", "agentSessionId") ?? input.sessionKey,
		) ?? input.existingMetadata?.agentSessionId ?? null;
	const a2a =
		extractCanonicalA2AEnvelope(sessionMetadata, input.sessionPayload) ??
		input.existingMetadata?.a2a ??
		null;

	return {
		agentId,
		agentUniqueId,
		agentSessionId: normalizedAgentSessionId,
		threadId,
		startedAt,
		agentType,
		projectId: normalizeProjectId(sessionMetadata?.project_id ?? input.existingMetadata?.projectId),
		cwd: normalizeProjectCwd(sessionMetadata?.project_cwd ?? input.existingMetadata?.cwd),
		repoRoot: normalizeRepoRoot(sessionMetadata?.project_repo_root ?? input.existingMetadata?.repoRoot),
		projectImageRef: normalizeProjectImageRef(
			sessionMetadata?.project_image_ref ?? input.existingMetadata?.projectImageRef,
		),
		sessionModelBinding: deriveSessionModelBindingFromSessionPayload({
			sessionPayload: input.sessionPayload,
			existingBinding: input.existingMetadata?.sessionModelBinding ?? null,
		}),
		sessionConfigOverrides: normalizeSessionConfigOverrides(
			sessionMetadata?.session_config_overrides ?? input.existingMetadata?.sessionConfigOverrides,
		),
		...(a2a ? { a2a } : {}),
	};
}

async function hydrateLocalSessionFilesForRead(input: {
	sessionKey: string;
	requestedThreadId: string | null;
	reason: "session_model" | "session_config";
}): Promise<
	| { ok: true; metadata: SessionMetadata }
	| { ok: false; statusCode: number; error: string; message: string; errorDetail?: string | null }
> {
	const agentSessionId = normalizeNumericId(input.sessionKey);
	if (agentSessionId == null || !shouldRegisterAgents(process.env)) {
		return {
			ok: false,
			statusCode: 404,
			error: "session_not_found",
			message: "No local session found for the provided session id.",
		};
	}

	const fetched = await fetchBackendAgentSession({
		agentSessionId,
		env: process.env,
		log: (message) => console.log(`[astro-stream] ${message}`),
	});
	if (!fetched.ok) {
		return {
			ok: false,
			statusCode: fetched.notFound ? 404 : 502,
			error: fetched.notFound ? "session_not_found" : "session_hydration_failed",
			message:
				fetched.error ??
				(fetched.notFound
					? "The backend AgentSession was not found."
					: "Could not fetch backend session information for session hydration."),
		};
	}
	if (!isPlainObject(fetched.body)) {
		return {
			ok: false,
			statusCode: 502,
			error: "session_hydration_failed",
			message: "Backend AgentSession response was not a JSON object.",
		};
	}

	const checkpoint = await fetchCheckpointForHistoryHydration({
		agentSessionId: fetched.agentSessionId ?? agentSessionId,
		sessionKey: input.sessionKey,
	});
	if (checkpoint.ok === false) {
		logStructuredEvent({
			severity: checkpoint.status === 404 ? "WARNING" : "ERROR",
			component: "astro-stream",
			event: "read_endpoint_checkpoint_hydration_failed",
			message: "Astro could not hydrate local session files from the backend checkpoint for a read endpoint.",
			data: {
				sessionId: input.sessionKey,
				agentSessionId: fetched.agentSessionId ?? agentSessionId,
				reason: input.reason,
				status: checkpoint.status,
				error: checkpoint.error,
				backendResponseText: checkpoint.responseText,
				backendResponseBody: checkpoint.body,
			},
		});
		return {
			ok: false,
			statusCode: checkpoint.status === 404 ? 404 : 502,
			error: checkpoint.status === 404 ? "session_checkpoint_not_found" : "session_hydration_failed",
			message: checkpoint.error ?? "Could not hydrate session files from the backend checkpoint.",
		};
	}

	const bundle = checkpoint.body.bundle;
	if (!isPlainObject(bundle) || typeof bundle.pi_session_jsonl !== "string") {
		return {
			ok: false,
			statusCode: 502,
			error: "session_hydration_failed",
			message: "Backend checkpoint response did not include bundle.pi_session_jsonl.",
		};
	}

	const normalized = normalizePiSessionJsonlForRuntime({
		sessionKey: input.sessionKey,
		threadId: input.requestedThreadId ?? input.sessionKey,
		agentSessionId: fetched.agentSessionId ?? agentSessionId,
		phase: `read_hydration:${input.reason}`,
		piSessionJsonl: bundle.pi_session_jsonl,
		source: "checkpoint",
	});
	if (normalized.ok === false) {
		return {
			ok: false,
			statusCode: 409,
			error: "corrupt_session_history",
			message: normalized.errorEvent.error,
			errorDetail: normalized.errorEvent.error_detail ?? normalized.errorEvent.error,
		};
	}

	mkdirSync(sessionDir, { recursive: true });
	writeFileSync(getSessionPath(input.sessionKey), normalized.piSessionJsonl);
	const metadata = buildSessionMetadataFromBackendCheckpoint({
		sessionKey: input.sessionKey,
		agentSessionId: fetched.agentSessionId ?? agentSessionId,
		sessionPayload: fetched.body,
		checkpointBundle: bundle,
		requestedThreadId: input.requestedThreadId,
	});
	if (!metadata.agentType) {
		return {
			ok: false,
			statusCode: 502,
			error: "session_hydration_failed",
			message: "Backend AgentSession response did not include a usable agent_type identity.",
		};
	}
	writeSessionMetadata(input.sessionKey, metadata);
	const threadBinding = isPlainObject(bundle.thread_binding_json)
		? bundle.thread_binding_json
		: {
				threadId: metadata.threadId ?? input.sessionKey,
				runtimeSessionId: input.sessionKey,
				updatedAt: new Date().toISOString(),
		  };
	const threadId = extractStringProperty(threadBinding, "threadId", "thread_id") ?? metadata.threadId ?? input.sessionKey;
	const threadBindingUpdatedAt =
		extractStringProperty(threadBinding, "updatedAt", "updated_at") ??
		new Date().toISOString();
	const normalizedThreadBinding = {
		...threadBinding,
		threadId,
		thread_id: threadId,
		runtimeSessionId: input.sessionKey,
		runtime_session_id: input.sessionKey,
		updatedAt: threadBindingUpdatedAt,
		updated_at: threadBindingUpdatedAt,
	};
	writeFileSync(getThreadBindingPath(threadId), JSON.stringify(normalizedThreadBinding, null, 2));

	logStructuredEvent({
		severity: "INFO",
		component: "astro-stream",
		event: "read_endpoint_session_hydrated_from_checkpoint",
		message: "Astro hydrated local session files from backend checkpoint for a read endpoint.",
		data: {
			sessionId: input.sessionKey,
			agentSessionId: fetched.agentSessionId ?? agentSessionId,
			agentType: metadata.agentType,
			threadId,
			reason: input.reason,
			checkpointVersion: checkpoint.body.checkpoint_version,
			bundleHash: checkpoint.body.bundle_hash,
		},
	});
	return { ok: true, metadata };
}

async function prepareCheckpointBeforePiLaunch(
	ctx: RequestContext,
): Promise<{ ok: true } | { ok: false; errorEvent: Extract<StreamEvent, { type: "error" }> }> {
	if (ctx.agentSessionId == null || !shouldRegisterAgents(process.env)) return { ok: true };

	if (ctx.checkpointLease) {
		const leaseExpiresAt = Date.parse(ctx.checkpointLease.leaseExpiresAt);
		if (Number.isFinite(leaseExpiresAt) && leaseExpiresAt > Date.now() + 5000) {
			logStructuredEvent({
				severity: "INFO",
				component: "astro-stream",
				event: "checkpoint_lease_reused",
				message: "Astro reused an active checkpoint lease for this runtime step.",
				data: {
					sessionKey: ctx.sessionKey,
					threadId: ctx.threadId,
					agentSessionId: ctx.agentSessionId,
					holderId: ctx.checkpointLease.holderId,
					leaseExpiresAt: ctx.checkpointLease.leaseExpiresAt,
					checkpointVersion: ctx.checkpointLease.checkpointVersion,
					bundleHash: ctx.checkpointLease.bundleHash,
				},
			});
			return { ok: true };
		}
		stopCheckpointLeaseRenewal(ctx);
		ctx.checkpointLease = null;
	}

	const finalizationState = readCheckpointLifecycleState(ctx.sessionKey);
	if (finalizationState?.state === "finalizing_checkpoint") {
		clearCheckpointLifecycleState(ctx.sessionKey);
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "checkpoint_launch_ignored_local_finalizing_state",
			message: "Astro ignored a local checkpoint finalization state before runtime execution; backend lease acquisition is the authority.",
			data: {
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				agentSessionId: ctx.agentSessionId,
				finalizingState: finalizationState,
			},
		});
	}

	const client = new SessionCheckpointClient({
		env: process.env,
		log: (message) => console.log(`[astro-stream] ${message}`),
	});
	const holderId = resolveCheckpointHolderId();
	const ttlSeconds = resolveCheckpointLeaseTtlSeconds();
	const existingManifest = readCheckpointManifest(ctx.sessionKey);
	let leaseAction: "renew" | "acquire" | "renew_then_acquire" = shouldRenewManifestLease(
		existingManifest,
		holderId,
	)
		? "renew"
		: "acquire";
	let leaseResult: Awaited<ReturnType<SessionCheckpointClient["acquireLease"]>>;
	if (leaseAction === "renew") {
		leaseResult = await client.renewLease({
			agentSessionId: ctx.agentSessionId,
			holderId,
			leaseToken: existingManifest.lease_token,
			ttlSeconds,
			leasePurpose: "runtime_run",
		});
		if (leaseResult.ok === false && checkpointLeaseRenewCanFallbackToAcquire(leaseResult)) {
			logStructuredEvent({
				severity: "WARNING",
				component: "astro-stream",
				event: "checkpoint_lease_renew_stale_manifest",
				message: "Checkpoint lease renew failed for a stale local manifest; trying a fresh lease acquire.",
				data: {
					sessionKey: ctx.sessionKey,
					threadId: ctx.threadId,
					agentSessionId: ctx.agentSessionId,
					holderId,
					...checkpointFailureDiagnostics(leaseResult),
				},
			});
			leaseAction = "renew_then_acquire";
			leaseResult = await client.acquireLease({
				agentSessionId: ctx.agentSessionId,
				holderId,
				ttlSeconds,
				leasePurpose: "runtime_run",
			});
		}
	} else {
		leaseResult = await client.acquireLease({
			agentSessionId: ctx.agentSessionId,
			holderId,
			ttlSeconds,
			leasePurpose: "runtime_run",
		});
	}

	if (leaseResult.ok === false) {
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-stream",
			event: "checkpoint_lease_failed",
			message: "Checkpoint lease could not be acquired before runtime execution.",
			data: {
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				agentSessionId: ctx.agentSessionId,
				leaseAction,
				holderId,
				...checkpointFailureDiagnostics(leaseResult),
			},
		});
		return {
			ok: false,
			errorEvent: buildBackendFailureErrorEvent("Checkpoint lease failed", leaseResult, "checkpoint"),
		};
	}

	const lease = leaseResult.body;
	ctx.checkpointLease = {
		holderId,
		leaseToken: lease.lease_token,
		leaseExpiresAt: lease.lease_expires_at,
		checkpointVersion: lease.checkpoint_version,
		bundleHash: lease.bundle_hash,
		renewTimer: null,
	};
	updateActiveStreamSession(ctx, {
		checkpointVersion: lease.checkpoint_version,
		bundleHash: lease.bundle_hash,
	});
	writeCheckpointLifecycleState(ctx.sessionKey, {
		state: "running",
		lease_holder_id: holderId,
		lease_token: lease.lease_token,
		checkpoint_version: lease.checkpoint_version,
		bundle_hash: lease.bundle_hash,
		reason: "launch",
	});
	logStructuredEvent({
		severity: "INFO",
		component: "astro-stream",
		event: leaseAction === "renew" ? "checkpoint_lease_renewed" : "checkpoint_lease_acquired",
		message:
			leaseAction === "renew"
				? "Checkpoint lease renewed before runtime execution."
				: "Checkpoint lease acquired before runtime execution.",
		data: {
			sessionKey: ctx.sessionKey,
			threadId: ctx.threadId,
			agentSessionId: ctx.agentSessionId,
			holderId,
			leaseExpiresAt: lease.lease_expires_at,
			checkpointVersion: lease.checkpoint_version,
			bundleHash: lease.bundle_hash,
		},
	});

	if (!checkpointManifestIsCurrent(existingManifest, lease, ctx.sessionKey)) {
		logStructuredEvent({
			severity: "INFO",
			component: "astro-stream",
			event: "checkpoint_restore_started",
			message: "Restoring backend checkpoint bundle before runtime execution.",
			data: {
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				agentSessionId: ctx.agentSessionId,
				checkpointVersion: lease.checkpoint_version,
				bundleHash: lease.bundle_hash,
				manifestCheckpointVersion: existingManifest?.checkpoint_version ?? null,
				manifestBundleHash: existingManifest?.bundle_hash ?? null,
			},
		});
		const restoreResult = await client.restore({
			agentSessionId: ctx.agentSessionId,
			holderId,
			leaseToken: lease.lease_token,
		});
		if (restoreResult.ok === false) {
			logStructuredEvent({
				severity: "ERROR",
				component: "astro-stream",
				event: "checkpoint_restore_failed",
				message: "Backend checkpoint bundle could not be restored before runtime execution.",
				data: {
					sessionKey: ctx.sessionKey,
					threadId: ctx.threadId,
					agentSessionId: ctx.agentSessionId,
					holderId,
					...checkpointFailureDiagnostics(restoreResult),
				},
			});
			const releaseResult = await client.releaseLease({
				agentSessionId: ctx.agentSessionId,
				holderId,
				leaseToken: lease.lease_token,
				reason: "restore_failed",
			});
			ctx.checkpointLease = null;
			if (releaseResult.ok === false) {
				writeCheckpointLifecycleState(ctx.sessionKey, {
					state: "finalizing_checkpoint",
					lease_holder_id: holderId,
					lease_token: lease.lease_token,
					checkpoint_version: lease.checkpoint_version,
					bundle_hash: lease.bundle_hash,
					reason: "restore_failed_release_pending",
				});
				logStructuredEvent({
					severity: "WARNING",
					component: "astro-stream",
					event: "checkpoint_restore_failed_release_failed",
					message: "Astro could not release checkpoint lease after restore failure.",
					data: {
						sessionKey: ctx.sessionKey,
						threadId: ctx.threadId,
						agentSessionId: ctx.agentSessionId,
						holderId,
						...checkpointFailureDiagnostics(releaseResult),
					},
				});
			} else {
				clearCheckpointLifecycleState(ctx.sessionKey);
			}
			return {
				ok: false,
				errorEvent: buildBackendFailureErrorEvent("Checkpoint restore failed", restoreResult, "checkpoint"),
			};
		}
		const normalizedRestore = normalizePiSessionJsonlForRuntime({
			sessionKey: ctx.sessionKey,
			threadId: ctx.threadId,
			agentSessionId: ctx.agentSessionId,
			phase: "prelaunch_restore",
			piSessionJsonl: restoreResult.body.bundle.pi_session_jsonl,
			source: "checkpoint",
		});
		if (normalizedRestore.ok === false) {
			const releaseResult = await client.releaseLease({
				agentSessionId: ctx.agentSessionId,
				holderId,
				leaseToken: lease.lease_token,
				reason: "restore_invalid_history",
			});
			ctx.checkpointLease = null;
			if (releaseResult.ok === false) {
				writeCheckpointLifecycleState(ctx.sessionKey, {
					state: "finalizing_checkpoint",
					lease_holder_id: holderId,
					lease_token: lease.lease_token,
					checkpoint_version: lease.checkpoint_version,
					bundle_hash: lease.bundle_hash,
					reason: "restore_invalid_history_release_pending",
				});
				logStructuredEvent({
					severity: "WARNING",
					component: "astro-stream",
					event: "checkpoint_restore_invalid_history_release_failed",
					message: "Astro could not release checkpoint lease after rejecting a corrupt restored history bundle.",
					data: {
						sessionKey: ctx.sessionKey,
						threadId: ctx.threadId,
						agentSessionId: ctx.agentSessionId,
						holderId,
						...checkpointFailureDiagnostics(releaseResult),
					},
				});
			} else {
				clearCheckpointLifecycleState(ctx.sessionKey);
			}
			return {
				ok: false,
				errorEvent: normalizedRestore.errorEvent,
			};
		}
		materializeCheckpointBundle(ctx, {
			...restoreResult.body.bundle,
			pi_session_jsonl: normalizedRestore.piSessionJsonl,
		});
		checkpointRestoreCount += 1;
		logStructuredEvent({
			severity: "INFO",
			component: "astro-stream",
			event: "checkpoint_restore_completed",
			message: "Restored backend checkpoint bundle before runtime execution.",
			data: {
				sessionKey: ctx.sessionKey,
				agentSessionId: ctx.agentSessionId,
				checkpointVersion: restoreResult.body.checkpoint_version,
				bundleHash: restoreResult.body.bundle_hash,
				restoreCount: checkpointRestoreCount,
			},
		});
		ctx.checkpointLease.checkpointVersion = restoreResult.body.checkpoint_version;
		ctx.checkpointLease.bundleHash = restoreResult.body.bundle_hash;
		writeCheckpointManifest(ctx.sessionKey, {
			checkpointVersion: restoreResult.body.checkpoint_version,
			bundleHash: restoreResult.body.bundle_hash,
			holderId,
			leaseToken: lease.lease_token,
			leaseExpiresAt: lease.lease_expires_at,
			restoredAt: new Date().toISOString(),
		});
	} else {
		writeCheckpointManifest(ctx.sessionKey, {
			checkpointVersion: lease.checkpoint_version,
			bundleHash: lease.bundle_hash,
			holderId,
			leaseToken: lease.lease_token,
			leaseExpiresAt: lease.lease_expires_at,
		});
		logStructuredEvent({
			severity: "INFO",
			component: "astro-stream",
			event: "checkpoint_restore_skipped_current",
			message: "Local checkpoint manifest is current; backend restore was not needed before runtime execution.",
			data: {
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				agentSessionId: ctx.agentSessionId,
				checkpointVersion: lease.checkpoint_version,
				bundleHash: lease.bundle_hash,
			},
		});
	}

	let localPiSessionJsonl: string;
	try {
		localPiSessionJsonl = readFileSync(getSessionPath(ctx.sessionKey), "utf8");
	} catch (error) {
		const releaseResult = await client.releaseLease({
			agentSessionId: ctx.agentSessionId,
			holderId,
			leaseToken: lease.lease_token,
			reason: "prelaunch_session_read_failed",
		});
		ctx.checkpointLease = null;
		if (releaseResult.ok === false) {
			writeCheckpointLifecycleState(ctx.sessionKey, {
				state: "finalizing_checkpoint",
				lease_holder_id: holderId,
				lease_token: lease.lease_token,
				checkpoint_version: lease.checkpoint_version,
				bundle_hash: lease.bundle_hash,
				reason: "prelaunch_session_read_failed_release_pending",
			});
		} else {
			clearCheckpointLifecycleState(ctx.sessionKey);
		}
		return {
			ok: false,
			errorEvent: {
				type: "error",
				error: "Astro could not read the active Pi session history before execution.",
				error_source: "runtime",
				error_code: "session_history_read_failed",
				error_detail: error instanceof Error ? error.message : String(error),
			},
		};
	}

	const normalizedLocal = normalizePiSessionJsonlForRuntime({
		sessionKey: ctx.sessionKey,
		threadId: ctx.threadId,
		agentSessionId: ctx.agentSessionId,
		phase: "prelaunch_local",
		piSessionJsonl: localPiSessionJsonl,
		source: "runtime",
	});
	if (normalizedLocal.ok === false) {
		const releaseResult = await client.releaseLease({
			agentSessionId: ctx.agentSessionId,
			holderId,
			leaseToken: lease.lease_token,
			reason: "prelaunch_invalid_history",
		});
		ctx.checkpointLease = null;
		if (releaseResult.ok === false) {
			writeCheckpointLifecycleState(ctx.sessionKey, {
				state: "finalizing_checkpoint",
				lease_holder_id: holderId,
				lease_token: lease.lease_token,
				checkpoint_version: lease.checkpoint_version,
				bundle_hash: lease.bundle_hash,
				reason: "prelaunch_invalid_history_release_pending",
			});
			logStructuredEvent({
				severity: "WARNING",
				component: "astro-stream",
				event: "checkpoint_prelaunch_invalid_history_release_failed",
				message: "Astro could not release checkpoint lease after rejecting corrupt local session history.",
				data: {
					sessionKey: ctx.sessionKey,
					threadId: ctx.threadId,
					agentSessionId: ctx.agentSessionId,
					holderId,
					...checkpointFailureDiagnostics(releaseResult),
				},
			});
		} else {
			clearCheckpointLifecycleState(ctx.sessionKey);
		}
		return {
			ok: false,
			errorEvent: normalizedLocal.errorEvent,
		};
	}
	if (normalizedLocal.changed) {
		writeFileSync(getSessionPath(ctx.sessionKey), normalizedLocal.piSessionJsonl);
	}

	startCheckpointLeaseRenewal(ctx, client, ttlSeconds);
	return { ok: true };
}

function buildAgentSessionTerminalState(ctx: RequestContext, reason: "finish" | "error" | "cancel") {
	if (reason === "finish") {
		return {
			status: "completed",
			error_code: null,
			error_detail: null,
		};
	}
	if (reason === "cancel") {
		return {
			status: "canceled",
			error_code: "session_cancelled_by_user",
			error_detail: "Session canceled by user.",
		};
	}
	return {
		status: "error",
		error_code: ctx.terminalError?.errorCode ?? null,
		error_detail: ctx.terminalError?.errorDetail ?? "Runtime ended with an error.",
	};
}

function writeCheckpointMarker(ctx: RequestContext, reason: "finish" | "error" | "cancel") {
	if (!ctx.checkpointLease) {
		logStructuredEvent({
			severity: "INFO",
			component: "astro-stream",
			event: "checkpoint_marker_skipped_no_lease",
			message: "Checkpoint marker was skipped because this stream has no active checkpoint lease.",
			data: {
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				agentSessionId: ctx.agentSessionId,
				reason,
			},
		});
		return;
	}
	try {
		mkdirSync(getCheckpointMarkerDir(), { recursive: true });
		const checkpointLease = ctx.checkpointLease;
		const marker = {
			session_id: ctx.sessionKey,
			agent_session_id: ctx.agentSessionId,
			thread_id: ctx.threadId,
			reason,
			written_at: new Date().toISOString(),
			lease_holder_id: checkpointLease.holderId,
			lease_token: checkpointLease.leaseToken,
			checkpoint_version: checkpointLease.checkpointVersion,
			bundle_hash: checkpointLease.bundleHash,
			agent_session_terminal_state: buildAgentSessionTerminalState(ctx, reason),
		};
		const markerPath = path.join(
			getCheckpointMarkerDir(),
			`${ctx.sessionKey}.${Date.now()}.${reason}.marker.json`,
		);
		const markerTempPath = `${markerPath}.${process.pid}.tmp`;
		writeFileSync(markerTempPath, JSON.stringify(marker, null, 2));
		renameSync(markerTempPath, markerPath);
		writeCheckpointLifecycleState(ctx.sessionKey, {
			state: "finalizing_checkpoint",
			lease_holder_id: checkpointLease.holderId,
			lease_token: checkpointLease.leaseToken,
			checkpoint_version: checkpointLease.checkpointVersion,
			bundle_hash: checkpointLease.bundleHash,
			reason,
			marker_file: path.basename(markerPath),
		});
		logStructuredEvent({
			severity: "INFO",
			component: "astro-stream",
			event: "checkpoint_marker_written",
			message: "Checkpoint marker written for sidecar flush.",
			data: {
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				agentSessionId: ctx.agentSessionId,
				reason,
				markerFile: path.basename(markerPath),
				checkpointVersion: checkpointLease.checkpointVersion,
				bundleHash: checkpointLease.bundleHash,
			},
		});
	} catch (error) {
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-stream",
			event: "checkpoint_marker_write_failed",
			message: "Failed to write checkpoint marker.",
			data: {
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				reason,
				error: error instanceof Error ? error.message : String(error),
			},
		});
	}
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

function isNodeRuntimeWarningLine(line: string): boolean {
	const trimmed = line.trim();
	return /^\(node:\d+\)\s+[\w.]*Warning:/.test(trimmed);
}

function isNodeRuntimeWarningFollowupLine(line: string): boolean {
	const trimmed = line.trim();
	return (
		trimmed.startsWith("(Use `node --trace-warnings") ||
		trimmed.startsWith("Use `node --trace-warnings") ||
		/^\s+at\s+/.test(line)
	);
}

const streamErrorSources = new Set<StreamErrorSource>([
	"astro",
	"backend",
	"checkpoint",
	"client",
	"pi",
	"provider",
	"tool",
	"unknown",
]);

function normalizeStreamErrorSource(value: unknown): StreamErrorSource | null {
	if (typeof value !== "string") return null;
	const normalized = value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_");
	return streamErrorSources.has(normalized as StreamErrorSource)
		? (normalized as StreamErrorSource)
		: null;
}

function inferStreamErrorSource(chunk: Extract<StreamEvent, { type: "error" }>): StreamErrorSource {
	const explicit = normalizeStreamErrorSource(chunk.error_source);
	if (explicit) return explicit;

	const errorCode = typeof chunk.error_code === "string" ? chunk.error_code.toLowerCase() : "";
	if (errorCode.startsWith("client_")) return "client";
	if (errorCode.includes("provider")) return "provider";
	if (errorCode.startsWith("checkpoint_")) return "checkpoint";
	if (chunk.forensics?.backend_request_url || chunk.forensics?.backend_response_body) return "backend";
	return "astro";
}

function prefixStreamError(error: string, source: StreamErrorSource): string {
	const trimmed = error.trim() || "Unknown stream error.";
	if (/^\[[a-z][a-z0-9_-]*\]\s+/i.test(trimmed)) return trimmed;
	return `[${source}] ${trimmed}`;
}

function normalizeStreamErrorChunk(chunk: StreamEvent): StreamEvent {
	if (chunk.type !== "error") return chunk;
	const errorSource = inferStreamErrorSource(chunk);
	return {
		...chunk,
		error_source: errorSource,
		error: prefixStreamError(chunk.error, errorSource),
	};
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
	if (ctx.responseProvider && ctx.responseModel) {
		syncRuntimeReportedSessionModelBinding(ctx);
	}
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

function formatPiSessionValidationFailure(error: unknown): {
	message: string;
	detail: string;
	raw: string;
	kind: string;
	entryId: string | null;
	toolCallId: string | null;
} {
	const raw = error instanceof Error ? error.message : String(error);

	if (raw.startsWith("orphaned_pi_session_tool_result:")) {
		const [, entryId = "unknown", toolCallId = "unknown"] = raw.split(":", 3);
		return {
			message: "Corrupt session history blocks execution because a tool result references a missing tool call.",
			detail: `Tool result entry "${entryId}" references missing tool call "${toolCallId}".`,
			raw,
			kind: "orphaned_tool_result",
			entryId,
			toolCallId,
		};
	}

	if (raw.startsWith("duplicate_pi_session_tool_call_id:")) {
		const toolCallId = raw.slice("duplicate_pi_session_tool_call_id:".length) || "unknown";
		return {
			message: "Corrupt session history blocks execution because the same tool call id appears more than once.",
			detail: `Duplicate tool call id "${toolCallId}" was found on the active Pi session branch.`,
			raw,
			kind: "duplicate_tool_call_id",
			entryId: null,
			toolCallId,
		};
	}

	if (raw.startsWith("missing_pi_session_parent:")) {
		const missingParentId = raw.slice("missing_pi_session_parent:".length) || "unknown";
		return {
			message: "Corrupt session history blocks execution because the active branch references a missing parent entry.",
			detail: `The active Pi session branch references missing parent entry "${missingParentId}".`,
			raw,
			kind: "missing_parent",
			entryId: null,
			toolCallId: missingParentId,
		};
	}

	if (raw.startsWith("invalid_pi_session_tool_result_missing_tool_call_id:")) {
		const entryId = raw.slice("invalid_pi_session_tool_result_missing_tool_call_id:".length) || "unknown";
		return {
			message: "Corrupt session history blocks execution because a tool result is missing its tool call id.",
			detail: `Tool result entry "${entryId}" does not include a valid toolCallId.`,
			raw,
			kind: "missing_tool_result_tool_call_id",
			entryId,
			toolCallId: null,
		};
	}

	if (raw === "invalid_pi_session_tool_call_missing_id") {
		return {
			message: "Corrupt session history blocks execution because a tool call is missing its id.",
			detail: "An assistant tool-call entry on the active Pi session branch does not include a valid id.",
			raw,
			kind: "missing_tool_call_id",
			entryId: null,
			toolCallId: null,
		};
	}

	return {
		message: "Corrupt session history blocks execution because the active Pi session JSONL is invalid.",
		detail: raw,
		raw,
		kind: "invalid_session_jsonl",
		entryId: null,
		toolCallId: null,
	};
}

function buildCorruptPiSessionHistoryErrorEvent(
	error: unknown,
	source: StreamErrorSource,
): Extract<StreamEvent, { type: "error" }> {
	const formatted = formatPiSessionValidationFailure(error);
	return {
		type: "error",
		error: formatted.message,
		error_source: source,
		error_code: "corrupt_session_history",
		error_detail: formatted.detail,
	};
}

function extractAssistantErrorMessage(message: unknown): string | null {
	if (!message || typeof message !== "object") return null;
	return normalizeLogString((message as { errorMessage?: unknown }).errorMessage);
}

function buildProviderResponseErrorEvent(errorMessage: string | null): Extract<StreamEvent, { type: "error" }> {
	return {
		type: "error",
		error:
			errorMessage ??
			"The model provider returned an error response before Astro could complete the turn.",
		error_source: "provider",
		error_code: "provider_response_error",
		error_detail:
			errorMessage ??
			"The assistant stop reason was \"error\", but no provider errorMessage was included.",
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
	return `[astro-stream] OUT agent_type=${ctx.agentType} session=${ctx.sessionKey} thread=${ctx.threadId}${model ? ` model=${model}` : ""}`;
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
			console.log(
				`${prefix}: error source=${chunk.error_source ?? "unknown"} ${JSON.stringify(compactLogValue(chunk.error, 320))}`,
			);
			return;
	}
}

function shouldSuppressClientChunk(chunk: ReturnType<typeof attachAgentId>): boolean {
	return false;
}

function abortStreamOnPersistenceFailure(ctx: RequestContext, error: unknown) {
	const message = error instanceof Error ? error.message : String(error);
	console.error(
		`[astro-stream] conversation persistence failed agent_type=${ctx.agentType} session=${ctx.sessionKey} thread=${ctx.threadId}: ${message}`,
	);
	clearActiveStreamSession(ctx);
	ctx.finished = true;
	ctx.res.destroy(error instanceof Error ? error : new Error(message));
}

function writeChunkWithAgentId(ctx: RequestContext, chunk: StreamEvent, agentId: number | null) {
	if (ctx.finished) return;
	ctx.eventId += 1;
	const enrichedChunk = attachAgentId(normalizeStreamErrorChunk(chunk), agentId);
	if (enrichedChunk.type === "error") {
		ctx.terminalError = {
			errorCode: enrichedChunk.error_code ?? null,
			errorDetail: enrichedChunk.error_detail ?? enrichedChunk.error,
		};
	} else if (enrichedChunk.type === "finish") {
		ctx.terminalError = null;
	}
	try {
		ctx.conversationStore.recordStreamChunkSync(enrichedChunk);
	} catch (error) {
		abortStreamOnPersistenceFailure(ctx, error);
		return;
	}
	if (!shouldSuppressClientChunk(enrichedChunk) && ctx.clientAttached && !ctx.res.writableEnded && !ctx.res.destroyed) {
		const payload = serializeSse(ctx.eventId, enrichedChunk);
		try {
			ctx.res.write(payload);
		} catch (error) {
			ctx.clientAttached = false;
			updateActiveStreamSession(ctx, { clientAttached: false });
			logStructuredEvent({
				severity: "WARNING",
				component: "astro-stream",
				event: "stream_client_write_failed",
				message: "Astro could not write to the SSE response; the active Pi run will continue detached.",
				data: {
					sessionKey: ctx.sessionKey,
					threadId: ctx.threadId,
					agentSessionId: ctx.agentSessionId,
					error: error instanceof Error ? error.message : String(error),
				},
			});
		}
	}
	logReadableChunk(ctx, enrichedChunk);
	if (enrichedChunk.type === "finish") {
		finalizeOpenReasoningAnnotation(ctx);
		writeCheckpointMarker(ctx, "finish");
	} else if (enrichedChunk.type === "error") {
		finalizeOpenReasoningAnnotation(ctx);
		writeCheckpointMarker(
			ctx,
			enrichedChunk.error_code === "session_cancelled_by_user" ? "cancel" : "error",
		);
	}
}

function writeChunk(ctx: RequestContext, chunk: StreamEvent) {
	writeChunkWithAgentId(ctx, chunk, ctx.agentId);
}

function getProjectedAssistantOrdinal(ctx: RequestContext): number {
	const snapshot = ctx.conversationStore.getSnapshot();
	let assistantCount = 0;
	for (const message of snapshot.messages) {
		if (message.role === "assistant") assistantCount += 1;
	}
	if (snapshot.inProgressMessage?.role === "assistant") assistantCount += 1;
	return Math.max(assistantCount, 1);
}

function upsertReasoningAnnotation(
	annotations: HistoryAnnotations,
	annotation: HistoryAnnotation,
): HistoryAnnotations {
	const assistantMessages = [...annotations.assistant_messages];
	const existingIndex = assistantMessages.findIndex(
		(candidate) =>
			candidate.assistant_ordinal === annotation.assistant_ordinal &&
			(candidate.stream_message_id === annotation.stream_message_id ||
				!candidate.stream_message_id ||
				!annotation.stream_message_id),
	);
	if (existingIndex === -1) {
		assistantMessages.push(annotation);
		return {
			version: 1,
			assistant_messages: assistantMessages,
		};
	}

	const existing = assistantMessages[existingIndex];
	assistantMessages[existingIndex] = {
		...existing,
		...annotation,
		pi_entry_id: annotation.pi_entry_id ?? existing.pi_entry_id,
		stream_message_id: annotation.stream_message_id ?? existing.stream_message_id,
		reasoning_text_persisted: existing.reasoning_text_persisted || annotation.reasoning_text_persisted,
		reasoning_started_at: existing.reasoning_started_at ?? annotation.reasoning_started_at,
		reasoning_completed_at: annotation.reasoning_completed_at ?? existing.reasoning_completed_at,
	};
	return {
		version: 1,
		assistant_messages: assistantMessages,
	};
}

function logReasoningAnnotationPersistenceFailure(ctx: RequestContext, error: unknown) {
	logStructuredEvent({
		severity: "WARNING",
		component: "astro-stream",
		event: "history_reasoning_annotation_write_failed",
		message: "Astro observed reasoning but could not persist the history annotation into local metadata.",
		data: {
			sessionKey: ctx.sessionKey,
			threadId: ctx.threadId,
			agentSessionId: ctx.agentSessionId,
			error: error instanceof Error ? error.message : String(error),
		},
	});
}

function recordReasoningAnnotationStart(ctx: RequestContext) {
	const assistantOrdinal = getProjectedAssistantOrdinal(ctx);
	ctx.activeReasoningAnnotationOrdinal = assistantOrdinal;
	const startedAt = new Date().toISOString();
	try {
		updateSessionHistoryAnnotations(ctx.sessionKey, (annotations) =>
			upsertReasoningAnnotation(annotations, {
				assistant_ordinal: assistantOrdinal,
				pi_entry_id: null,
				stream_message_id: ctx.messageId,
				had_reasoning: true,
				reasoning_text_persisted: false,
				reasoning_started_at: startedAt,
				reasoning_completed_at: null,
			}),
		);
	} catch (error) {
		logReasoningAnnotationPersistenceFailure(ctx, error);
	}
}

function recordReasoningAnnotationEnd(ctx: RequestContext) {
	const assistantOrdinal = ctx.activeReasoningAnnotationOrdinal ?? getProjectedAssistantOrdinal(ctx);
	const completedAt = new Date().toISOString();
	try {
		updateSessionHistoryAnnotations(ctx.sessionKey, (annotations) =>
			upsertReasoningAnnotation(annotations, {
				assistant_ordinal: assistantOrdinal,
				pi_entry_id: null,
				stream_message_id: ctx.messageId,
				had_reasoning: true,
				reasoning_text_persisted: false,
				reasoning_started_at: null,
				reasoning_completed_at: completedAt,
			}),
		);
		ctx.activeReasoningAnnotationOrdinal = null;
	} catch (error) {
		logReasoningAnnotationPersistenceFailure(ctx, error);
	}
}

function finalizeOpenReasoningAnnotation(ctx: RequestContext) {
	if (ctx.activeReasoningAnnotationOrdinal == null) return;
	recordReasoningAnnotationEnd(ctx);
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
	stopCheckpointLeaseRenewal(ctx);
	clearActiveStreamSession(ctx);
	if (ctx.clientAttached && !ctx.res.writableEnded && !ctx.res.destroyed) {
		try {
			ctx.res.write("data: [DONE]\n\n");
			if (logTraffic) {
				console.log(`${getOutgoingLogPrefix(ctx)}: [DONE]`);
			}
			ctx.res.end();
		} catch (error) {
			ctx.clientAttached = false;
			logStructuredEvent({
				severity: "WARNING",
				component: "astro-stream",
				event: "stream_done_write_failed",
				message: "Astro could not write the SSE done marker because the client detached.",
				data: {
					sessionKey: ctx.sessionKey,
					threadId: ctx.threadId,
					agentSessionId: ctx.agentSessionId,
					error: error instanceof Error ? error.message : String(error),
				},
			});
		}
	} else if (logTraffic) {
		console.log(`${getOutgoingLogPrefix(ctx)}: [DONE detached]`);
	}
	ctx.finished = true;
}

function attachStreamAbortHandler(ctx: RequestContext) {
	ctx.res.once("close", () => {
		if (ctx.finished || ctx.res.writableEnded) return;
		ctx.clientAttached = false;
		updateActiveStreamSession(ctx, { clientAttached: false });
		logStructuredEvent({
			severity: "INFO",
			component: "astro-stream",
			event: "stream_client_detached",
			message: "Client disconnected before the stream completed; the active Pi run will continue.",
			data: {
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				agentSessionId: ctx.agentSessionId,
				hasCheckpointLease: Boolean(ctx.checkpointLease),
				hasPiProcess: Boolean(ctx.piProcess),
			},
		});
	});
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

function isPlainObject(value: any): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

const ASTRO_SESSION_METADATA_RESERVED_KEYS = new Set([
	"source",
	"agent_type",
	"agent_type",
	"created_by_user",
	"project_id",
	"project_cwd",
	"project_repo_root",
	"project_image_ref",
	"session_model_binding",
	"session_config_overrides",
]);

function sanitizeFrontendSessionMetadata(value: unknown): Record<string, unknown> {
	if (!isPlainObject(value)) return {};
	const sanitized: Record<string, unknown> = {};
	for (const [key, metadataValue] of Object.entries(value)) {
		if (ASTRO_SESSION_METADATA_RESERVED_KEYS.has(key)) continue;
		sanitized[key] = metadataValue;
	}
	return sanitized;
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

	lines.push("Latest user message:");
	lines.push(latestUserMessage);
	lines.push("Use the active session for prior conversation context when the same backend agent session is reused.");
	return lines.join("\n");
}

function stopActivePiProcess(ctx: RequestContext) {
	stopCheckpointLeaseRenewal(ctx);
	clearCancelKillTimer(ctx);
	if (!ctx.piProcess) return;
	try {
		ctx.piProcess.kill("SIGTERM");
	} catch {
		// ignore process termination failures
	}
	ctx.piProcess = null;
}

function clearCancelKillTimer(ctx: RequestContext) {
	if (!ctx.cancelKillTimer) return;
	clearTimeout(ctx.cancelKillTimer);
	ctx.cancelKillTimer = null;
}

function buildCancellationErrorEvent(): Extract<StreamEvent, { type: "error" }> {
	return {
		type: "error",
		error: "Session canceled by user.",
		error_source: "runtime",
		status: 499,
		error_code: "session_cancelled_by_user",
		error_detail: "Session canceled by user.",
		field_errors: null,
	};
}

function beginActiveRunCancellation(
	ctx: RequestContext,
	input: {
		cancellationId?: string | null;
		reason?: string | null;
		message?: string | null;
	},
) {
	if (ctx.finished) return;
	if (!ctx.cancellation) {
		ctx.cancellation = {
			requested: true,
			cancellationId: input.cancellationId ?? null,
			reason: input.reason ?? "user_requested",
			message: input.message ?? null,
		};
	} else {
		ctx.cancellation = {
			...ctx.cancellation,
			cancellationId: ctx.cancellation.cancellationId ?? input.cancellationId ?? null,
			reason: input.reason ?? ctx.cancellation.reason,
			message: input.message ?? ctx.cancellation.message,
		};
	}
	ctx.terminalError = {
		errorCode: "session_cancelled_by_user",
		errorDetail: "Session canceled by user.",
	};
	updateActiveStreamSession(ctx, {
		cancelling: true,
		cancellationId: ctx.cancellation.cancellationId,
	});
	logStructuredEvent({
		severity: "WARNING",
		component: "astro-stream",
		event: "session_cancellation_started",
		message: "Astro is cancelling the active Pi run for this session.",
		data: {
			sessionKey: ctx.sessionKey,
			threadId: ctx.threadId,
			agentSessionId: ctx.agentSessionId,
			cancellationId: ctx.cancellation.cancellationId,
			reason: ctx.cancellation.reason,
			hasPiProcess: Boolean(ctx.piProcess),
		},
	});
	const child = ctx.piProcess;
	if (!child) {
		writeChunk(ctx, buildCancellationErrorEvent());
		writeDone(ctx);
		return;
	}
	try {
		child.kill("SIGTERM");
	} catch {
		// ignore process termination failures
	}
	clearCancelKillTimer(ctx);
	ctx.cancelKillTimer = setTimeout(() => {
		if (ctx.piProcess !== child || ctx.finished) return;
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "session_cancellation_force_kill",
			message: "Pi did not exit after cancellation SIGTERM; sending SIGKILL.",
			data: {
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				agentSessionId: ctx.agentSessionId,
				cancellationId: ctx.cancellation?.cancellationId ?? null,
			},
		});
		try {
			child.kill("SIGKILL");
		} catch {
			// ignore process termination failures
		}
	}, sessionCancelGraceMs);
}


function handleAssistantDelta(ctx: RequestContext, evt: any) {
	updateResponseModelFromMessage(ctx, evt?.message ?? evt?.partial);
	switch (evt.type) {
		case "thinking_start": {
			ctx.reasoningCounter += 1;
			const id = `r${ctx.reasoningCounter}`;
			writeChunk(ctx, { type: "reasoning-start", id });
			recordReasoningAnnotationStart(ctx);
			return;
		}
		case "thinking_delta":
			if (typeof evt.delta === "string") {
				writeChunk(ctx, { type: "reasoning-delta", delta: evt.delta });
			}
			return;
		case "thinking_end":
			writeChunk(ctx, { type: "reasoning-end" });
			recordReasoningAnnotationEnd(ctx);
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
			const providerErrorMessage = extractAssistantErrorMessage(evt.message ?? evt.partial) ?? ctx.lastAssistantErrorMessage;
			if (finishReason === "error") {
				ctx.lastAssistantFinishReason = "error";
				ctx.lastAssistantErrorMessage = providerErrorMessage;
				ctx.lastAssistantUsage = mappedUsage;
				writeChunk(ctx, buildProviderResponseErrorEvent(providerErrorMessage));
				writeDone(ctx);
				return;
			}
			writeChunk(ctx, { type: "finish", finishReason, usage: mappedUsage });
			writeDone(ctx);
			return;
		}
		case "error": {
			const reason = evt.reason ?? "error";
			writeChunk(ctx, { type: "error", error: reason, error_source: "pi" });
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
	const errorMessage = extractAssistantErrorMessage(message);
	if (errorMessage) ctx.lastAssistantErrorMessage = errorMessage;

	const usage = mapUsageSummary((message as { usage?: unknown }).usage);
	if (usage) ctx.lastAssistantUsage = usage;

	if (ctx.piAssistantTextSeen) return;

	const text = extractTextParts((message as { content?: unknown }).content).trim();
	if (!text) return;

	emitAssistantText(ctx, text);
}

async function runPiPrompt(
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
	const piLaunchBaseEnv = {
		...process.env,
		...(options.envOverrides ?? {}),
	};
	let scopedPiAgentDir: string | null = null;
	let scopedProviderCredentialProvider: string | null = null;
	let activeProviderCredentialKey: string | null = null;
	let providerCredentialFlushTimer: ReturnType<typeof setInterval> | null = null;
	let providerCredentialFinalized = false;
	ctx.piAssistantTextSeen = false;
	ctx.lastAssistantFinishReason = null;
	ctx.lastAssistantUsage = undefined;
	if (boundModelArg || options.agentConfig?.model) {
		logStructuredEvent({
			component: "astro-stream",
			event: "pi_launch_model_ready",
			message: "Pi launch has a resolved model configuration.",
			data: {
				agentType: ctx.agentType,
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId ?? null,
				userId: ctx.userId,
				agentSessionId: ctx.agentSessionId,
				cwd: options.cwd,
				boundModelArg: boundModelArg ?? null,
				sessionModelBindingProvider: ctx.sessionModelBinding?.provider ?? null,
				sessionModelBindingModel: ctx.sessionModelBinding?.model ?? null,
				sessionModelBindingReasoningEffort:
					ctx.sessionModelBinding?.runConfig.reasoning_effort ?? null,
				agentConfigModel: options.agentConfig?.model ?? null,
			},
		});
	} else {
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "pi_launch_without_model",
			message:
				"Pi is launching without a bound model argument or agent-config model; execution may fail with no available models.",
			data: {
				agentType: ctx.agentType,
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId ?? null,
				userId: ctx.userId,
				agentSessionId: ctx.agentSessionId,
				cwd: options.cwd,
				sessionModelBindingPresent: Boolean(ctx.sessionModelBinding),
				agentConfigModel: options.agentConfig?.model ?? null,
			},
		});
	}

	try {
		const checkpointReady = await prepareCheckpointBeforePiLaunch(ctx);
		if (checkpointReady.ok === false) {
			writeChunk(ctx, checkpointReady.errorEvent);
			writeDone(ctx);
			return;
		}
	} catch (error) {
		writeChunk(ctx, {
			type: "error",
			error: error instanceof Error ? error.message : String(error),
			error_source: "checkpoint",
		});
		writeDone(ctx);
		return;
	}

	if (ctx.sessionModelBinding && resolveProviderDefinition(ctx.sessionModelBinding.provider)) {
		const hydratedProviderCredentials = await hydrateScopedProviderCredentials({
			createdByUser: ctx.userId,
			agentSessionId: ctx.agentSessionId,
			sessionKey: ctx.sessionKey,
			provider: ctx.sessionModelBinding.provider,
			holderId: `astro-pi-stream/${process.pid}/${ctx.sessionKey}`,
			sessionConfigOverrides: ctx.sessionConfigOverrides,
			env: piLaunchBaseEnv,
			log: (message) => console.log(`[astro-stream] ${message}`),
		});
		if (hydratedProviderCredentials.ok === false) {
			logStructuredEvent({
				severity: "ERROR",
				component: "astro-stream",
				event: "provider_credentials_hydrate_failed",
				message: "Astro could not hydrate scoped provider credentials before launching Pi.",
				data: {
					sessionId: ctx.sessionKey,
					agentSessionId: ctx.agentSessionId,
					provider: ctx.sessionModelBinding.provider,
					error: hydratedProviderCredentials.error,
					backendMessage: hydratedProviderCredentials.message,
				},
			});
			writeChunk(
				ctx,
				buildBackendFailureErrorEvent(
					"Provider credential hydrate failed",
					hydratedProviderCredentials,
					"provider",
				),
			);
			writeDone(ctx);
			return;
		}
		scopedPiAgentDir = hydratedProviderCredentials.value.scopedPiAgentDir;
		scopedProviderCredentialProvider = ctx.sessionModelBinding.provider;
		activeProviderCredentialKey = `${ctx.sessionKey}:${scopedProviderCredentialProvider}`;
		activeScopedProviderCredentials.set(activeProviderCredentialKey, {
			scopedPiAgentDir,
			createdByUser: ctx.userId,
			agentSessionId: ctx.agentSessionId,
			provider: scopedProviderCredentialProvider,
			sessionKey: ctx.sessionKey,
			env: {
				...piLaunchBaseEnv,
				PI_CODING_AGENT_DIR: scopedPiAgentDir,
			},
		});
		logStructuredEvent({
			component: "astro-stream",
			event: "provider_credentials_hydrated",
			message: "Astro hydrated scoped provider credentials before launching Pi.",
			data: {
				sessionId: ctx.sessionKey,
				agentSessionId: ctx.agentSessionId,
				provider: scopedProviderCredentialProvider,
				scopedPiAgentDir,
			},
		});
	} else {
		scopedPiAgentDir = ensureSessionScopedPiAgentDir({
			sessionKey: ctx.sessionKey,
			sessionConfigOverrides: ctx.sessionConfigOverrides,
			env: piLaunchBaseEnv,
		});
	}

	const flushProviderCredential = async (reason: string) => {
		if (!scopedPiAgentDir || !scopedProviderCredentialProvider) return;
		const flushed = await flushScopedProviderCredential({
			scopedPiAgentDir,
			createdByUser: ctx.userId,
			agentSessionId: ctx.agentSessionId,
			provider: scopedProviderCredentialProvider,
			reason,
			env: {
				...piLaunchBaseEnv,
				PI_CODING_AGENT_DIR: scopedPiAgentDir,
			},
			log: (message) => console.log(`[astro-stream] ${message}`),
		});
		if (flushed.ok === false) {
			logStructuredEvent({
				severity: "ERROR",
				component: "astro-stream",
				event: "provider_credentials_flush_failed",
				message: "Astro could not flush scoped provider credentials to backend.",
				data: {
					sessionId: ctx.sessionKey,
					agentSessionId: ctx.agentSessionId,
					provider: scopedProviderCredentialProvider,
					reason,
					error: flushed.error,
					backendMessage: flushed.message,
				},
			});
			return;
		}
		if (!flushed.value.noop) {
			logStructuredEvent({
				component: "astro-stream",
				event: "provider_credentials_flushed",
				message: "Astro flushed scoped provider credentials to backend.",
				data: {
					sessionId: ctx.sessionKey,
					agentSessionId: ctx.agentSessionId,
					provider: scopedProviderCredentialProvider,
					reason,
					version: flushed.value.version,
					credentialHash: flushed.value.credential_hash,
				},
			});
		}
	};

	const finalizeProviderCredentials = async (reason: string) => {
		if (providerCredentialFinalized) return;
		providerCredentialFinalized = true;
		if (providerCredentialFlushTimer) {
			clearInterval(providerCredentialFlushTimer);
			providerCredentialFlushTimer = null;
		}
		try {
			await flushProviderCredential(reason);
		} finally {
			if (activeProviderCredentialKey) {
				activeScopedProviderCredentials.delete(activeProviderCredentialKey);
				activeProviderCredentialKey = null;
			}
			cleanupScopedPiAgentDir(scopedProviderCredentialProvider ? scopedPiAgentDir : null);
		}
	};

	if (options.agentConfig) {
		if (boundModelArg) {
			args.push("--model", boundModelArg);
		} else if (options.agentConfig.model) {
			args.push("--model", options.agentConfig.model);
		}
		const builtInTools = options.agentConfig.tools?.filter((tool) => PI_BUILT_IN_TOOL_NAMES.has(tool)) ?? [];
		if (builtInTools.length) args.push("--tools", builtInTools.join(","));
		promptPath = writePromptToTempFile(options.agentConfig.promptName, options.agentConfig.systemPrompt);
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
			...(options.envOverrides ?? {}),
			...(scopedPiAgentDir ? { PI_CODING_AGENT_DIR: scopedPiAgentDir } : {}),
			PWD: options.cwd,
			ASTRO_TELEMETRY: "0",
			ASTRO_MAINSEQUENCE_USER_ID: ctx.userId,
			...(options.agentConfig
				? {
						ASTRO_SUBAGENT_CHILD: "1",
						ASTRO_ACTIVE_SPECIALIST: options.agentConfig.promptName,
				  }
				: {}),
			...(options.projectId ? { ASTRO_TARGET_PROJECT_ID: options.projectId } : {}),
		}),
		stdio: ["ignore", "pipe", "pipe"],
	});
	ctx.piProcess = child;
	if (scopedProviderCredentialProvider) {
		providerCredentialFlushTimer = setInterval(() => {
			void flushProviderCredential("oauth_refresh");
		}, providerCredentialFlushIntervalMs);
	}

	const stdout = createInterface({ input: child.stdout });
	stdout.on("line", (line) => {
		let parsed: any;
		try {
			parsed = JSON.parse(line);
		} catch {
			if (logTraffic) {
				console.log(
					`[astro-stream] NONJSON agent_type=${ctx.agentType} session=${ctx.sessionKey} thread=${ctx.threadId}: ${line}`,
				);
			}
			return;
		}

		if (ctx.finished) return;
		updateActiveStreamSession(ctx, { lastPiEventAt: new Date().toISOString() });

		if (parsed?.type === "message_start") {
			if (parsed.message?.role !== "assistant") return;
			ctx.piAssistantTextSeen = false;
			ctx.lastAssistantErrorMessage = null;
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
			const toolCallId = parsed.toolCallId;
			if (typeof toolCallId === "string") {
				writeChunk(ctx, { type: "tool-result", toolCallId, result: parsed.result });
			}
			return;
		}
	});

	const childStderrLines: string[] = [];
	const stderr = createInterface({ input: child.stderr });
	let nodeRuntimeWarningActive = false;
	stderr.on("line", (line) => {
		if (line.trim()) {
			childStderrLines.push(line);
			if (childStderrLines.length > 20) childStderrLines.shift();
		}
		if (logTraffic) {
			console.log(
				`[astro-stream] STDERR agent_type=${ctx.agentType} session=${ctx.sessionKey} thread=${ctx.threadId}: ${line}`,
			);
		}
		if (isNodeRuntimeWarningLine(line)) {
			nodeRuntimeWarningActive = true;
			recordRuntimeHealthIssue({
				source: "pi_child_stderr_warning",
				severity: "warning",
				error: new Error(line),
				context: {
					agentType: ctx.agentType,
					sessionKey: ctx.sessionKey,
					threadId: ctx.threadId,
				},
			});
			return;
		}
		if (nodeRuntimeWarningActive && isNodeRuntimeWarningFollowupLine(line)) {
			return;
		}
		nodeRuntimeWarningActive = false;
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "pi_child_stderr",
			message: "Pi child process wrote to stderr; treating it as diagnostic output unless the child exits unsuccessfully.",
			data: {
				agentType: ctx.agentType,
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
				line,
			},
		});
	});

	child.on("exit", (code, signal) => {
		ctx.piProcess = null;
		void (async () => {
			clearCancelKillTimer(ctx);
			cleanupPromptFile(promptPath);
			const failed = Boolean(signal || (typeof code === "number" && code !== 0));
			const terminalReason = ctx.cancellation?.requested
				? "shutdown"
				: failed
					? "stream_error"
					: "stream_finish";
			await finalizeProviderCredentials(terminalReason);
			if (ctx.finished) return;
			if (ctx.cancellation?.requested) {
				writeChunk(ctx, buildCancellationErrorEvent());
				writeDone(ctx);
				return;
			}
			if (failed) {
				const stderrSummary = childStderrLines.length
					? ` Recent stderr:\n${childStderrLines.join("\n")}`
					: "";
				const reason = signal
					? `Process exited with signal ${signal}.${stderrSummary}`
					: `Process exited with code ${code}.${stderrSummary}`;
				recordRuntimeHealthIssue({
					source: "pi_child_exit",
					severity: "error",
					error: new Error(reason),
					context: {
						agentType: ctx.agentType,
						sessionKey: ctx.sessionKey,
						threadId: ctx.threadId,
					},
				});
				writeChunk(ctx, { type: "error", error: reason, error_source: "pi" });
				writeDone(ctx);
				return;
			}

			if (ctx.lastAssistantFinishReason === "error") {
				writeChunk(ctx, buildProviderResponseErrorEvent(ctx.lastAssistantErrorMessage));
				writeDone(ctx);
				return;
			}

			writeChunk(ctx, {
				type: "finish",
				finishReason: ctx.lastAssistantFinishReason ?? "stop",
				usage: ctx.lastAssistantUsage,
			});
			writeDone(ctx);
		})();
	});

	child.on("error", (error) => {
		ctx.piProcess = null;
		clearCancelKillTimer(ctx);
		void finalizeProviderCredentials(ctx.cancellation?.requested ? "shutdown" : "stream_error");
		cleanupPromptFile(promptPath);
		recordRuntimeHealthIssue({
			source: "pi_child_error",
			severity: "error",
			error,
			context: {
				agentType: ctx.agentType,
				sessionKey: ctx.sessionKey,
				threadId: ctx.threadId,
			},
		});
		if (!ctx.finished) {
			if (ctx.cancellation?.requested) {
				writeChunk(ctx, buildCancellationErrorEvent());
			} else {
				writeChunk(ctx, { type: "error", error: error.message, error_source: "pi" });
			}
			writeDone(ctx);
		}
	});
}

const server = createServer((req, res) => {
	void handleStreamRequest(req, res).catch((error) => {
		const issue = recordRuntimeHealthIssue({
			source: "http_request",
			severity: "error",
			error,
			context: {
				method: req.method ?? null,
				url: req.url ?? null,
			},
		});
		console.error(`[astro-stream] request failed issue=${issue.id}: ${issue.message}`);
		if (!res.headersSent) {
			json(res, 500, {
				error: "internal_error",
				message: "The stream service encountered an unexpected error.",
				health_issue_id: issue.id,
			});
			return;
		}
		res.destroy(error instanceof Error ? error : undefined);
	});
});

let shutdownStarted = false;

async function handleShutdownSignal(signal: "SIGINT" | "SIGTERM") {
	if (shutdownStarted) return;
	shutdownStarted = true;
	try {
		await flushActiveScopedProviderCredentialsForShutdown(signal);
	} finally {
		mainsequenceCredentialExchangeLoop?.stop();
		const forcedExit = setTimeout(() => process.exit(0), 3000);
		forcedExit.unref();
		server.close(() => {
			process.exit(0);
		});
	}
}

process.once("SIGINT", () => {
	void handleShutdownSignal("SIGINT");
});

process.once("SIGTERM", () => {
	void handleShutdownSignal("SIGTERM");
});

async function handleStreamRequest(
	req: import("node:http").IncomingMessage,
	res: import("node:http").ServerResponse,
) {
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
		json(res, 200, buildRuntimeHealthSnapshot());
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/chat/get_available_models") {
		const userId = resolveUserIdFromRequest(req, url);
		const runtimeProfile = resolveRuntimeProfile();
		logStructuredEvent({
			component: "astro-stream",
			event: "available_models_request_started",
			message: "Available-model discovery request started.",
			data: {
				path: url.pathname,
				userId,
				runtimeProfile: serializeRuntimeProfile(runtimeProfile),
			},
		});
		if (!userId) {
			logStructuredEvent({
				severity: "WARNING",
				component: "astro-stream",
				event: "available_models_user_id_missing",
				message:
					"Available-model discovery request is missing user identity; auth-backed providers may be filtered out.",
				data: {
					path: url.pathname,
					acceptedSources: [
						"userId",
						"user_id",
						"created_by_user",
						"createdByUser",
						"x-mainsequence-user-id",
						"x-ms-user-id",
						"x-user-id",
						"authorization bearer jwt",
					],
				},
			});
		}
		try {
			const availableModels = await collectAvailableModels({
				env: process.env,
				userId,
			});
			const availableModelSummary = summarizeAvailableModelsForLog(availableModels);
			logStructuredEvent({
				component: "astro-stream",
				event: "available_models_request_succeeded",
				message: "Available-model discovery completed.",
				data: {
					path: url.pathname,
					userId,
					providerCount: availableModelSummary.providerCount,
					modelCount: availableModelSummary.modelCount,
					sourceSummaries: availableModelSummary.sourceSummaries,
				},
			});
			const piModelRegistrySource = availableModelSummary.piModelRegistrySource;
			if (
				piModelRegistrySource &&
				((piModelRegistrySource.totalModelCount ?? 0) > 0 &&
					(piModelRegistrySource.availableModelCount ?? piModelRegistrySource.count) === 0)
			) {
				logStructuredEvent({
					severity: "WARNING",
					component: "astro-stream",
					event: "available_models_pi_registry_filtered_out",
					message:
						"Pi model registry reported models, but none were exposed as available in this runtime.",
					data: {
						path: url.pathname,
						userId,
						piModelRegistrySource,
					},
				});
			}
			json(res, 200, availableModels);
		} catch (error) {
			const message =
				error instanceof Error ? error.message : "Unknown available-model discovery failure.";
			logStructuredEvent({
				severity: "ERROR",
				component: "astro-stream",
				event: "available_models_request_failed",
				message: "Available-model discovery failed.",
				data: {
					path: url.pathname,
					userId,
					error: message,
				},
			});
			json(res, 500, {
				error: "available_models_unavailable",
				message,
			});
		}
		return;
	}

	if (req.method === "GET" && url.pathname === "/api/models/catalog") {
		try {
			const modelCatalog = await collectModelCatalog({
				env: process.env,
				userId: resolveUserIdFromRequest(req, url),
			});
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
		const createdByUser = resolveUserIdFromRequest(req, url);
		if (!createdByUser) {
			logStructuredEvent({
				component: "astro-stream",
				event: "model_provider_user_id_missing",
				message: "Model-provider status request is missing user identity.",
				data: {
					path: url.pathname,
					acceptedSources: [
						"userId",
						"user_id",
						"created_by_user",
						"createdByUser",
						"x-mainsequence-user-id",
						"x-ms-user-id",
						"x-user-id",
						"authorization bearer jwt",
					],
				},
			});
			badRequest(
				res,
				"Missing or invalid userId for model-provider status. Pass userId, created_by_user, a supported user-id header, or a Bearer JWT with a user id claim.",
			);
			return;
		}
		const statuses = await listModelProviderAuthStatuses({
			createdByUser,
			env: process.env,
		});
		if (statuses.ok === false) {
			json(res, statuses.statusCode || 503, {
				error: statuses.error,
				message: statuses.message,
				backend_url: statuses.url ?? null,
				backend_status: statuses.statusCode ?? null,
				backend_response_text: statuses.responseText ?? null,
				backend_response_body: statuses.body ?? null,
			});
			return;
		}
		json(res, 200, {
			version: 1,
			providers: statuses.value,
			backendCredentialStatus: {
				ok: true,
			},
		});
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
		let body: Record<string, unknown>;
		try {
			const parsed = await parseJson(req);
			body = isPlainObject(parsed) ? parsed : {};
		} catch {
			badRequest(res, "Invalid JSON body.");
			return;
		}
		const createdByUser = resolveUserIdFromRequest(req, url, body);
		if (!createdByUser) {
			logStructuredEvent({
				component: "astro-stream",
				event: "model_provider_user_id_missing",
				message: "Model-provider auth action is missing user identity.",
				data: {
					provider: modelProviderAuthAction.provider,
					action: modelProviderAuthAction.action,
					path: url.pathname,
					acceptedSources: [
						"userId",
						"user_id",
						"created_by_user",
						"createdByUser",
						"x-mainsequence-user-id",
						"x-ms-user-id",
						"x-user-id",
						"authorization bearer jwt",
					],
				},
			});
			badRequest(
				res,
				"Missing or invalid userId for model-provider auth action. Pass userId, created_by_user, a supported user-id header, or a Bearer JWT with a user id claim.",
			);
			return;
		}
		const agentSessionId = resolveOptionalAgentSessionIdFromBodyOrSearch(body, url);
		const result =
			modelProviderAuthAction.action === "signin"
				? await startModelProviderSignIn(modelProviderAuthAction.provider, {
						createdByUser,
						agentSessionId,
						env: process.env,
				  })
				: await signOffModelProvider(modelProviderAuthAction.provider, {
						createdByUser,
						env: process.env,
				  });
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

		const activeCtx = getActiveStreamContext(sessionKey);
		if (activeCtx?.responseProvider && activeCtx.responseModel) {
			syncRuntimeReportedSessionModelBinding(activeCtx);
		}
		if (activeCtx?.sessionModelBinding) {
			json(res, 200, {
				sessionId: sessionKey,
				model: activeCtx.sessionModelBinding,
			});
			return;
		}

		let metadata = readSessionMetadata(sessionKey);
		if (!metadata) {
			const hydration = await hydrateLocalSessionFilesForRead({
				sessionKey,
				requestedThreadId: url.searchParams.get("thread_id") ?? url.searchParams.get("threadId"),
				reason: "session_model",
			});
				if (hydration.ok === false) {
					json(res, hydration.statusCode, {
						error: hydration.error,
						message: hydration.message,
						...(hydration.errorDetail ? { error_detail: hydration.errorDetail } : {}),
					});
					return;
				}
			metadata = hydration.metadata;
		}

		const insights = readSessionInsights({
			sessionDir,
			sessionKey,
			metadata,
		});
		const activeProvider = insights?.context.model.provider ?? null;
		const activeModel = insights?.context.model.model ?? null;
		if (activeProvider && activeModel && !doesSessionModelIdentityMatch(metadata.sessionModelBinding, activeProvider, activeModel)) {
			const refreshedBinding = rebindSessionModelBindingFromIdentity({
				existingBinding: metadata.sessionModelBinding,
				provider: activeProvider,
				model: activeModel,
				source: "session",
			});
			if (refreshedBinding) {
				metadata = {
					...metadata,
					sessionModelBinding: refreshedBinding,
				};
				writeSessionMetadata(sessionKey, metadata);
				logStructuredEvent({
					component: "astro-stream",
					event: "session_model_binding_repaired_from_session_history",
					message:
						"Astro repaired a stale cached session model binding from the persisted session runtime history.",
					data: {
						sessionKey,
						agentSessionId: metadata.agentSessionId,
						threadId: metadata.threadId,
						provider: refreshedBinding.provider,
						model: refreshedBinding.model,
					},
				});
			}
		}

		json(res, 200, {
			sessionId: sessionKey,
			model: metadata.sessionModelBinding,
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

		let metadata = readSessionMetadata(sessionKey);
		if (!metadata) {
			const requestedThreadId =
				typeof body?.thread_id === "string"
					? body.thread_id
					: typeof body?.threadId === "string"
						? body.threadId
						: null;
			const hydration = await hydrateLocalSessionFilesForRead({
				sessionKey,
				requestedThreadId,
				reason: "session_config",
			});
				if (hydration.ok === false) {
					json(res, hydration.statusCode, {
						error: hydration.error,
						message: hydration.message,
						...(hydration.errorDetail ? { error_detail: hydration.errorDetail } : {}),
					});
					return;
				}
			metadata = hydration.metadata;
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

	if (
		req.method === "POST" &&
		(url.pathname === "/api/chat/session/cancel" || url.pathname === "/api/a2a/cancel")
	) {
		let body: Record<string, unknown>;
		try {
			const parsed = await parseJson(req);
			body = isPlainObject(parsed) ? parsed : {};
		} catch {
			badRequest(res, "Invalid JSON body.");
			return;
		}

		const sessionKey = normalizeRuntimeSessionId(
			body.runtime_session_id ?? body.runtimeSessionId ?? body.sessionId,
		);
		if (!sessionKey) {
			badRequest(res, "Missing runtime_session_id.");
			return;
		}
		const agentSessionId = normalizeNumericId(sessionKey);
		if (agentSessionId == null) {
			json(res, 400, {
				error: "invalid_runtime_session_id",
				message: "runtime_session_id must be the backend AgentSession id.",
			});
			return;
		}
		const activeCtx = getActiveStreamContext(sessionKey, agentSessionId);
		const requestedByHolderId = activeCtx?.checkpointLease?.holderId ?? resolveCheckpointHolderId();
		const cancelMessage =
			typeof body.message === "string" && body.message.trim() ? body.message.trim() : null;
		const client = new SessionCheckpointClient({
			env: process.env,
			log: (message) => console.log(`[astro-stream] ${message}`),
		});
		const cancelResult = await client.requestRuntimeCancel({
			agentSessionId,
			requestedByHolderId,
			reason: "user_requested",
			message: cancelMessage,
		});
		if (cancelResult.ok === false) {
			logStructuredEvent({
				severity: "ERROR",
				component: "astro-stream",
				event: "session_cancel_request_failed",
				message: "Backend rejected or failed the runtime cancellation request.",
				data: {
					sessionKey,
					agentSessionId,
					requestedByHolderId,
					status: cancelResult.status,
					error: cancelResult.error,
					backendResponseText: cancelResult.responseText,
					backendResponseBody: cancelResult.body,
				},
			});
			json(res, cancelResult.status ?? 502, {
				ok: false,
				error: "session_cancel_request_failed",
				message: cancelResult.error,
				backend_status: cancelResult.status,
				backend_response_text: cancelResult.responseText,
				backend_response_body: cancelResult.body,
			});
			return;
		}

		if (activeCtx && cancelResult.body.cancel_state === "requested") {
			beginActiveRunCancellation(activeCtx, {
				cancellationId: cancelResult.body.cancellation_id,
				reason: "user_requested",
				message: cancelMessage,
			});
		}

		const state = activeCtx && cancelResult.body.cancel_state === "requested"
			? "cancelling"
			: cancelResult.body.cancel_state === "requested"
				? "cancel_requested"
				: "not_running";

		json(res, 200, {
			ok: true,
			session_id: sessionKey,
			agent_session_id: agentSessionId,
			state,
			working: cancelResult.body.working,
			cancellation_id: cancelResult.body.cancellation_id,
			message:
				state === "cancelling"
					? "Cancellation requested and the active local runtime is stopping."
					: state === "cancel_requested"
						? "Cancellation requested. The active runtime holder will stop the session."
						: "Session is not currently running.",
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

	const isHumanChatRequest = url.pathname === "/api/chat";
	const isA2AChatRequest = url.pathname === "/api/a2a/chat";

	if (req.method !== "POST" || (!isHumanChatRequest && !isA2AChatRequest)) {
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

	if (isA2AChatRequest) {
		const normalizedA2ARequest = normalizeA2AChatRequestBody(isPlainObject(body) ? body : {});
		if (normalizedA2ARequest.ok === false) {
			json(res, normalizedA2ARequest.statusCode, {
				error: normalizedA2ARequest.error,
				message: normalizedA2ARequest.message,
			});
			return;
		}
		body = normalizedA2ARequest.body;
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

	const runtimeProfile = resolveRuntimeProfile();
	const runtimeProfileValidation = validateRuntimeProfile(runtimeProfile);
	if (runtimeProfileValidation.ok === false) {
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-stream",
			event: "runtime_profile_invalid",
			message: "Astro rejected a request because the runtime profile configuration is invalid.",
			data: {
				path: url.pathname,
				runtimeProfile: serializeRuntimeProfile(runtimeProfile),
				error: runtimeProfileValidation.error,
				statusCode: runtimeProfileValidation.statusCode,
			},
		});
		json(res, runtimeProfileValidation.statusCode, {
			error: runtimeProfileValidation.error,
			message: runtimeProfileValidation.message,
			runtime_profile: serializeRuntimeProfile(runtimeProfile),
		});
		return;
	}
	logStructuredEvent({
		component: "astro-stream",
		event: "request_runtime_profile_resolved",
		message: "Astro resolved the runtime profile for the request.",
		data: {
			path: url.pathname,
			runtimeProfile: serializeRuntimeProfile(runtimeProfile),
		},
	});

	if (!(await ensureRequestCliAuth(res, runtimeProfile)).ok) {
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
	const a2aContext = extractObjectPropertyRecord(context, "a2a") ?? {};
	const a2aCaller = extractObjectPropertyRecord(a2aContext, "caller") ?? {};
	const requestA2AEnvelope = buildRequestA2AEnvelope({
		enabled: isA2AChatRequest || a2aContext.enabled === true,
		body: isPlainObject(body) ? body : {},
		a2aContext,
		caller: a2aCaller,
	});

	const fixedAgentType = runtimeProfile.fixedAgentType;
	const rawRequestedAgentType = normalizeAgentType(body.agentType);
	if (fixedAgentType && rawRequestedAgentType && rawRequestedAgentType !== fixedAgentType) {
		json(res, 409, {
			error: "fixed_agent_type_mismatch",
			message: `This runtime is pinned to agentType "${fixedAgentType}".`,
		});
		return;
	}
	const requestedAgentType = rawRequestedAgentType ?? fixedAgentType;
	if (!requestedAgentType) {
		badRequest(res, "Missing agentType.");
		return;
	}
	if (!ALLOWED_AGENT_TYPES.has(requestedAgentType)) {
		json(res, 400, { error: "unknown_agent_type", message: `Unknown agent type "${requestedAgentType}".` });
		return;
	}
	const agentType = requestedAgentType;

	const userId = resolveUserId(body.userId);
	if (!userId) {
		badRequest(res, "Missing or invalid userId.");
		return;
	}
	const requestedNewChat = body.newChat === true;
	if (body.newChat !== undefined && typeof body.newChat !== "boolean") {
		badRequest(res, "`newChat` must be a boolean.");
		return;
	}
	const explicitRuntimeSessionId = normalizeRuntimeSessionId(
		(body.runtime_session_id as unknown) ?? (body.runtimeSessionId as unknown),
	);
	const runtimeSessionId = explicitRuntimeSessionId;
	if (!runtimeSessionId) {
		json(res, 400, {
			error: "missing_runtime_session_id",
			message: "runtime_session_id is required for real chat and A2A execution requests.",
		});
		return;
	}
	if (normalizeNumericId(runtimeSessionId) == null) {
		json(res, 400, {
			error: "invalid_runtime_session_id",
			message: "runtime_session_id must be the backend AgentSession id.",
		});
		return;
	}
	const registrationRequired = shouldRegisterAgents(process.env);
	if (!registrationRequired) {
		json(res, 503, {
			error: "agent_registration_disabled",
			message: "BUILD_AGENTS_IN_BACKEND must be enabled for backend-owned session attach.",
		});
		return;
	}
	if (requestedNewChat) {
		logStructuredEvent({
			severity: "INFO",
			component: "astro-stream",
			event: "request_new_chat_ignored_backend_session_required",
			message:
				"Astro ignored `newChat` because backend-owned session attach now requires an explicit runtime_session_id.",
			data: {
				agentType,
				userId,
				runtimeSessionId,
				requestedThreadId,
			},
		});
	}
	let hydratedBackendSession: HydratedBackendSession | null = null;
	const requestSessionPayload = extractRequestSessionPayload(body);
	let localSessionExists = sessionExists(runtimeSessionId);
	let localSessionMetadata = readSessionMetadata(runtimeSessionId);
	if (requestSessionPayload) {
		const authorityValidation = validateRequestSessionPayloadAuthority({
			sessionPayload: requestSessionPayload,
			runtimeSessionId,
			agentType,
		});
		if (authorityValidation.ok === false) {
			logStructuredEvent({
				severity: "WARNING",
				component: "astro-stream",
				event: "request_session_payload_mismatch",
				message:
					"Astro rejected a request-carried session serializer that did not match the target backend session authority.",
				data: {
					agentType,
					userId,
					runtimeSessionId,
					...authorityValidation.details,
				},
			});
			json(res, authorityValidation.statusCode, {
				error: authorityValidation.error,
				message: authorityValidation.message,
				...authorityValidation.details,
			});
			return;
		}
		const requestSessionMetadata = buildSessionMetadataFromRequestSessionPayload({
			sessionKey: runtimeSessionId,
			sessionPayload: requestSessionPayload,
			requestedThreadId,
			fallbackAgentType: agentType,
			existingMetadata: localSessionMetadata,
		});
		const effectiveRequestSessionMetadata =
			requestA2AEnvelope != null
				? {
						...requestSessionMetadata,
						a2a: mergeA2AEnvelopes(requestSessionMetadata.a2a ?? null, requestA2AEnvelope),
				  }
				: requestSessionMetadata;
		writeSessionMetadata(runtimeSessionId, effectiveRequestSessionMetadata);
		if (effectiveRequestSessionMetadata.threadId) {
			writeThreadBinding({
				threadId: effectiveRequestSessionMetadata.threadId,
				runtimeSessionId,
				updatedAt: new Date().toISOString(),
			});
		}
		localSessionExists = true;
		localSessionMetadata = effectiveRequestSessionMetadata;
		logStructuredEvent({
			component: "astro-stream",
			event: "request_session_metadata_attached",
			message: "Astro materialized local session metadata from the request-carried session serializer.",
			data: {
				runtimeSessionId,
				agentType: effectiveRequestSessionMetadata.agentType,
				threadId: effectiveRequestSessionMetadata.threadId,
				agentId: effectiveRequestSessionMetadata.agentId,
				agentSessionId: effectiveRequestSessionMetadata.agentSessionId,
				effectiveProvider: effectiveRequestSessionMetadata.sessionModelBinding?.provider ?? null,
				effectiveModel: effectiveRequestSessionMetadata.sessionModelBinding?.model ?? null,
			},
		});
	}
	if (!localSessionExists || !localSessionMetadata) {
		logStructuredEvent({
			component: "astro-stream",
			event: "backend_checkpoint_hydration_local_session_incomplete",
			message: "Local session files were missing or incomplete, so Astro is hydrating from backend checkpoint state.",
			data: {
				runtimeSessionId,
				agentType,
				userId,
				requestedThreadId,
			},
		});
		const checkpointHydration = await hydrateLocalSessionFilesForRead({
			sessionKey: runtimeSessionId,
			requestedThreadId,
			reason: "session_config",
		});
		if (checkpointHydration.ok === false) {
			if (!localSessionMetadata) {
				const hydrationResult = await attachHydratedBackendSession({
					runtimeSessionId,
					userId,
					requestedThreadId,
					existingMetadata: localSessionMetadata,
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
				writeSessionMetadata(runtimeSessionId, hydratedBackendSession.metadata);
				if (hydratedBackendSession.metadata.threadId) {
					writeThreadBinding({
						threadId: hydratedBackendSession.metadata.threadId,
						runtimeSessionId,
						updatedAt: new Date().toISOString(),
					});
				}
				logStructuredEvent({
					component: "astro-stream",
					event: "backend_session_hydration_attached",
					message: "Astro attached hydrated backend session metadata to the current request.",
					data: {
						runtimeSessionId,
						agentType: hydratedBackendSession.metadata.agentType,
						threadId: hydratedBackendSession.metadata.threadId,
						agentId: hydratedBackendSession.agentId,
						agentSessionId: hydratedBackendSession.metadata.agentSessionId,
					},
				});
			} else {
				json(res, checkpointHydration.statusCode, {
					error: checkpointHydration.error,
					message: checkpointHydration.message,
					...(checkpointHydration.errorDetail ? { error_detail: checkpointHydration.errorDetail } : {}),
				});
				return;
			}
		} else {
			logStructuredEvent({
				component: "astro-stream",
				event: "backend_checkpoint_hydration_attached",
				message: "Astro hydrated local session files from backend checkpoint state for resume.",
				data: {
					runtimeSessionId,
					threadId: checkpointHydration.metadata.threadId,
					agentType: checkpointHydration.metadata.agentType,
					agentId: checkpointHydration.metadata.agentId,
					agentSessionId: checkpointHydration.metadata.agentSessionId,
				},
			});
		}
	}
	let existingSessionMetadata = readSessionMetadata(runtimeSessionId);
	if (!existingSessionMetadata && hydratedBackendSession) {
		existingSessionMetadata = hydratedBackendSession.metadata;
	}
	const fixedWorkerRequiresBackendSessionAuthority = runtimeProfile.kind === "project_worker";
	if (
		!existingSessionMetadata ||
		!existingSessionMetadata.sessionModelBinding ||
		existingSessionMetadata.agentId == null ||
		fixedWorkerRequiresBackendSessionAuthority
	) {
		const hydrationResult = await attachHydratedBackendSession({
			runtimeSessionId,
			userId,
			requestedThreadId,
			existingMetadata: existingSessionMetadata,
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
		writeSessionMetadata(runtimeSessionId, hydratedBackendSession.metadata);
		if (hydratedBackendSession.metadata.threadId) {
			writeThreadBinding({
				threadId: hydratedBackendSession.metadata.threadId,
				runtimeSessionId,
				updatedAt: new Date().toISOString(),
			});
		}
		existingSessionMetadata = hydratedBackendSession.metadata;
		logStructuredEvent({
			component: "astro-stream",
			event: "backend_session_metadata_refreshed_before_launch",
			message: "Astro refreshed local session metadata from backend authority before Pi launch.",
			data: {
				runtimeSessionId,
				agentType: hydratedBackendSession.metadata.agentType,
				threadId: hydratedBackendSession.metadata.threadId,
				agentId: hydratedBackendSession.agentId,
				agentSessionId: hydratedBackendSession.metadata.agentSessionId,
				effectiveProvider: hydratedBackendSession.metadata.sessionModelBinding?.provider ?? null,
				effectiveModel: hydratedBackendSession.metadata.sessionModelBinding?.model ?? null,
			},
		});
	}
	if (!existingSessionMetadata) {
		json(res, 409, {
			error: "session_hydration_failed",
			message: "Astro could not hydrate session metadata for the provided runtime_session_id.",
		});
		return;
	}
	if (requestA2AEnvelope) {
		existingSessionMetadata = {
			...existingSessionMetadata,
			a2a: mergeA2AEnvelopes(existingSessionMetadata.a2a ?? null, requestA2AEnvelope),
		};
		writeSessionMetadata(runtimeSessionId, existingSessionMetadata);
	}
	if (existingSessionMetadata?.agentType) {
		if (existingSessionMetadata.agentType !== agentType) {
			json(res, 409, {
				error: "session_mismatch",
				message: "runtime_session_id does not match the requested agentType.",
			});
			return;
		}
	}

	if (body.model !== undefined) {
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "request_model_ignored_session_first",
			message:
				"Astro ignored the message-level `model` field because session-first model authority now comes from the request-carried session serializer, stored session metadata, or backend session authority.",
			data: {
				agentType,
				userId,
				threadId: existingSessionMetadata?.threadId ?? requestedThreadId ?? null,
				runtimeSessionId,
			},
		});
	}
	if (body.runConfig !== undefined) {
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "request_run_config_ignored_session_first",
			message:
				"Astro ignored the message-level `runConfig` field because session-first model policy is resolved from the target backend session.",
			data: {
				agentType,
				userId,
				threadId: existingSessionMetadata?.threadId ?? requestedThreadId ?? null,
				runtimeSessionId,
			},
		});
	}

	const requestSessionModelBinding = requestSessionPayload
		? deriveSessionModelBindingFromSessionPayload({
				sessionPayload: requestSessionPayload,
				existingBinding: existingSessionMetadata?.sessionModelBinding ?? null,
		  })
		: null;
	const sessionModelBinding =
		fixedWorkerRequiresBackendSessionAuthority
			? existingSessionMetadata?.sessionModelBinding ?? requestSessionModelBinding ?? null
			: requestSessionModelBinding ?? existingSessionMetadata?.sessionModelBinding ?? null;
	let requestModelSource = "none";
	if (hydratedBackendSession) {
		requestModelSource = "backend_session_authority";
	} else if (fixedWorkerRequiresBackendSessionAuthority && existingSessionMetadata?.sessionModelBinding) {
		requestModelSource = "target_session_metadata";
	} else if (requestSessionModelBinding) {
		requestModelSource = "request_session_serializer";
	} else if (existingSessionMetadata?.sessionModelBinding) {
		requestModelSource = "session_metadata";
	}
	const sessionModelBindingLogData = {
		agentType,
		userId,
		threadId: existingSessionMetadata?.threadId ?? requestedThreadId ?? null,
		runtimeSessionId,
		newChat: false,
		requestModelSource,
		requestSessionAttached: Boolean(requestSessionPayload),
		existingSessionHadModelBinding: Boolean(existingSessionMetadata?.sessionModelBinding),
		hydratedBackendSessionAttached: Boolean(hydratedBackendSession),
		effectiveProvider: sessionModelBinding?.provider ?? null,
		effectiveModel: sessionModelBinding?.model ?? null,
		effectiveReasoningEffort: sessionModelBinding?.runConfig.reasoning_effort ?? null,
	};
	if (sessionModelBinding) {
		logStructuredEvent({
			component: "astro-stream",
			event: "session_model_binding_resolved",
			message: "Astro resolved a session model binding for the request.",
			data: sessionModelBindingLogData,
		});
	} else {
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "session_model_binding_missing",
			message:
				"No session model binding is available for this request after backend authority was consulted.",
				data: sessionModelBindingLogData,
			});
		json(res, 409, {
			error: "session_model_binding_missing",
			message:
				"Astro could not resolve a model binding from the backend-owned session. Provide backend session model metadata or update the backend session before retrying.",
		});
		return;
	}

	const projectAttachment = resolveProjectAttachment({
		agentType,
		body,
		existingSessionMetadata,
		runtimeProfile,
	});
	if (projectAttachment.ok === false) {
		json(res, projectAttachment.statusCode, {
			error: projectAttachment.error,
			message: projectAttachment.message,
		});
		return;
	}

	const projectId = projectAttachment.projectId;
	const agentCwd = projectAttachment.cwd;
	logStructuredEvent({
		component: "astro-stream",
		event: "request_project_attachment_resolved",
		message: "Astro resolved project attachment for the request.",
		data: {
			agentType,
			userId,
			runtimeSessionId,
			runtimeProfile: serializeRuntimeProfile(runtimeProfile),
			attached: projectAttachment.attached,
			projectId,
			cwd: agentCwd,
			repoRoot: projectAttachment.repoRoot,
			projectImageRef: projectAttachment.projectImageRef,
			requestedProjectId: projectAttachment.requestedProjectId,
			requestedCwd: projectAttachment.requestedCwd,
			fixedProjectId: projectAttachment.fixedProjectId,
			fixedProjectCwd: projectAttachment.fixedProjectCwd,
		},
	});
	const projectImageRef = projectAttachment.projectImageRef;
	const agentConfig: AgentConfig | null = null;

	const system = typeof body.system === "string" ? body.system : undefined;

	const threadId = existingSessionMetadata?.threadId ?? requestedThreadId ?? runtimeSessionId;
	let agentId: number | null = null;
	let agentUniqueId: string | null = null;
	const explicitAgentId = extractRequestedAgentId(isPlainObject(body) ? body : {});
	const explicitAgentUniqueId = extractRequestedAgentUniqueId(isPlainObject(body) ? body : {});
	if (hydratedBackendSession) {
		agentId = hydratedBackendSession.agentId;
		agentUniqueId = hydratedBackendSession.agentUniqueId;
	} else {
		agentId = existingSessionMetadata?.agentId ?? explicitAgentId;
		agentUniqueId = existingSessionMetadata?.agentUniqueId ?? explicitAgentUniqueId;
	}

	if (agentId == null) {
		json(res, 409, {
			error: "missing_agent_id",
			message: "Astro could not resolve the backend Agent id for the provided session.",
		});
		return;
	}

	let sessionKey: string;
	let agentSessionId: number | null = null;
	let startedAt: string | null = null;
	const responseAgentType = existingSessionMetadata?.agentType ?? agentType;
	const responseThreadId = threadId;
	const effectiveA2AEnvelope =
		existingSessionMetadata?.a2a || requestA2AEnvelope
			? mergeA2AEnvelopes(existingSessionMetadata?.a2a ?? null, {
					...(requestA2AEnvelope ?? existingSessionMetadata?.a2a ?? {
						version: 1,
						enabled: true,
						userOrigin: "agent",
						callerAgentType: null,
						callerMetadata: null,
						responseFormat: null,
						handleUniqueId: null,
						callerAgentSessionId: null,
						targetAgentSessionId: null,
						targetAgentId: null,
					}),
					targetAgentSessionId:
						requestA2AEnvelope?.targetAgentSessionId ??
						existingSessionMetadata?.a2a?.targetAgentSessionId ??
						agentSessionId ??
						normalizeNumericId(runtimeSessionId) ??
						null,
					targetAgentId:
						requestA2AEnvelope?.targetAgentId ??
						existingSessionMetadata?.a2a?.targetAgentId ??
						agentId ??
						null,
			  })
			: null;
	const persistedCwd = projectAttachment.attached ? agentCwd : null;
	const frozenRepoRoot = projectAttachment.repoRoot;
	if (
		existingSessionMetadata?.agentUniqueId &&
		agentUniqueId &&
		existingSessionMetadata.agentUniqueId !== agentUniqueId
	) {
		json(res, 409, {
			error: "session_mismatch",
			message: "runtime_session_id does not match the active agent.",
		});
		return;
	}
	agentSessionId =
		existingSessionMetadata?.agentSessionId ?? normalizeNumericId(runtimeSessionId) ?? null;
	startedAt = existingSessionMetadata?.startedAt ?? null;
	sessionKey = runtimeSessionId;
	writeSessionMetadata(sessionKey, {
		agentId,
		agentUniqueId,
		agentSessionId,
		threadId,
		startedAt,
		agentType: responseAgentType,
		projectId,
		cwd: persistedCwd,
		repoRoot: frozenRepoRoot,
		projectImageRef,
		sessionModelBinding,
		sessionConfigOverrides: existingSessionMetadata?.sessionConfigOverrides ?? null,
		...(effectiveA2AEnvelope ? { a2a: effectiveA2AEnvelope } : {}),
	});

	const activeRun = getActiveStreamSession(sessionKey, agentSessionId);
	if (activeRun) {
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-stream",
			event: "session_run_already_active",
			message: "Astro rejected a duplicate chat launch for a session with an active Pi run.",
			data: {
				sessionKey,
				threadId: activeRun.threadId,
				agentSessionId: activeRun.agentSessionId,
				agentType: activeRun.agentType,
				messageId: activeRun.messageId,
				startedAt: activeRun.startedAt,
				clientAttached: activeRun.clientAttached,
			},
		});
		json(res, 409, {
			type: "error",
			error: "This session already has an active assistant run.",
			error_source: "runtime",
			status: 409,
			error_code: "session_run_already_active",
			error_detail: "Wait for the active run to finish, then retry.",
			sessionId: sessionKey,
			threadId: activeRun.threadId,
			agentSessionId: activeRun.agentSessionId,
			messageId: activeRun.messageId,
			startedAt: activeRun.startedAt,
			clientAttached: activeRun.clientAttached,
		});
		return;
	}

	writeThreadBinding({
		threadId: responseThreadId,
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
			`[astro-stream] SESSION agent_type=${responseAgentType} session=${sessionKey} thread=${responseThreadId} agent_id=${agentId} agent_session_id=${agentSessionId ?? "n/a"}${selectedModelForLog ? ` model=${selectedModelForLog}` : ""}`,
		);
	}

	let conversationStore: ConversationStore;
	try {
		conversationStore = createConversationStore({
			sessionDir,
			sessionKey,
			threadId: responseThreadId,
			agentType: responseAgentType,
			agentId,
			agentSessionId,
			startedAt,
			...(effectiveA2AEnvelope ? { a2a: effectiveA2AEnvelope } : {}),
		});
		conversationStore.recordUserMessageSync({
			text: latestUserMessage,
			...(effectiveA2AEnvelope
				? { provenance: a2aEnvelopeToUserProvenance(effectiveA2AEnvelope) }
				: {}),
		});
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
		"X-Thread-Id": responseThreadId,
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
		threadId: responseThreadId,
		sessionKey,
		agentId,
		agentUniqueId,
		agentSessionId,
		agentType: responseAgentType,
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
		activeReasoningAnnotationOrdinal: null,
		runtimeToolCounter: 0,
		toolCallIds: new Map(),
		piProcess: null,
		cancelKillTimer: null,
		cancellation: null,
		clientAttached: true,
		finished: false,
		terminalError: null,
		system,
		uiContext: context,
		uiTools: tools,
		sessionModelBinding,
		sessionConfigOverrides: existingSessionMetadata?.sessionConfigOverrides ?? null,
		responseProvider: null,
		responseModel: null,
		piAssistantTextSeen: false,
		lastAssistantFinishReason: null,
		lastAssistantErrorMessage: null,
		lastAssistantUsage: undefined,
		checkpointLease: null,
	};
	markActiveStreamSession(ctx);
	attachStreamAbortHandler(ctx);

	writeChunk(ctx, { type: "start", messageId });

	const prompt = buildPrompt(system, latestUserMessage, context, tools);

	if (projectAttachment.attached) {
		writeSessionMetadata(sessionKey, {
			agentId,
			agentUniqueId,
			agentSessionId,
			threadId,
			startedAt,
			agentType: responseAgentType,
			projectId,
			cwd: persistedCwd,
			repoRoot: frozenRepoRoot,
			projectImageRef,
			sessionModelBinding,
			sessionConfigOverrides: existingSessionMetadata?.sessionConfigOverrides ?? null,
			...(effectiveA2AEnvelope ? { a2a: effectiveA2AEnvelope } : {}),
		});
	}

	void runPiPrompt(prompt, ctx, {
		cwd: agentCwd ?? repoRoot,
		projectId,
		agentConfig,
	}).catch((error) => {
		if (!ctx.finished) {
			writeChunk(ctx, {
				type: "error",
				error: error instanceof Error ? error.message : String(error),
				error_source: "pi",
			});
			writeDone(ctx);
		}
	});
}

server.on("clientError", (error, socket) => {
	const remoteSocket = socket as typeof socket & {
		remoteAddress?: string;
		remotePort?: number;
	};
	const issue = recordRuntimeHealthIssue({
		source: "http_client_error",
		severity: "warning",
		error,
		context: {
			remoteAddress: remoteSocket.remoteAddress ?? null,
			remotePort: remoteSocket.remotePort ?? null,
		},
	});
	if (logTraffic) {
		console.log(`[astro-stream] client error issue=${issue.id}: ${issue.message}`);
	}
	if (socket.writable) {
		socket.end("HTTP/1.1 400 Bad Request\r\n\r\n");
	}
});

server.on("error", (error) => {
	const issue = recordRuntimeHealthIssue({
		source: "http_server",
		severity: "fatal",
		error,
		context: { host, port },
	});
	console.error(`[astro-stream] server error issue=${issue.id}: ${issue.message}`);
});

const startupRuntimeProfile = resolveRuntimeProfile();
const startupRuntimeProfileValidation = validateRuntimeProfile(startupRuntimeProfile);
const startupRuntimeProfileValidationDetails: Record<string, unknown> = {};
if (startupRuntimeProfileValidation.ok === false) {
	startupRuntimeProfileValidationDetails.error = startupRuntimeProfileValidation.error;
	startupRuntimeProfileValidationDetails.statusCode = startupRuntimeProfileValidation.statusCode;
	startupRuntimeProfileValidationDetails.message = startupRuntimeProfileValidation.message;
}
logStructuredEvent({
	component: "astro-stream",
	event: "runtime_profile_resolved",
	message: "Astro resolved the runtime profile for this process.",
	data: {
		runtimeProfile: serializeRuntimeProfile(startupRuntimeProfile),
		validationOk: startupRuntimeProfileValidation.ok,
		...startupRuntimeProfileValidationDetails,
	},
});
if (startupRuntimeProfileValidation.ok === false) {
	recordRuntimeHealthIssue({
		source: "runtime_profile",
		severity: "error",
		error: new Error(startupRuntimeProfileValidation.message),
		context: {
			runtimeProfile: serializeRuntimeProfile(startupRuntimeProfile),
			error: startupRuntimeProfileValidation.error,
		},
	});
}

server.listen(port, host, () => {
	console.log(`[astro-stream] Listening on http://${host}:${port}`);
	console.log("[astro-stream] POST /api/chat or POST /api/a2a/chat to start a data-stream response");
});
