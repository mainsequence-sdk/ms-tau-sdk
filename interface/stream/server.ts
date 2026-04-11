import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { attachAgentId, serializeSse, type StreamEvent } from "./protocol.js";
import {
	buildAgentUniqueId,
	registerMainsequenceAgent,
	resolveMainsequenceUserId,
	startBackendAgentSession,
	shouldRegisterAgents,
} from "../../pi/extensions/shared/agent-registration.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const MAINSEQUENCE_REFRESH_INTERVAL_ENV = "MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS";
const MAINSEQUENCE_REQUIRED_ENV = [
	"MAINSEQUENCE_ACCESS_TOKEN",
	"MAINSEQUENCE_REFRESH_TOKEN",
	"MAINSEQUENCE_BACKEND",
	"MAINSEQUENCE_PROJECTS_BASE",
];
const ALLOWED_AGENTS = new Set(["astro-orchestrator"]);

function loadEnvFile(root: string) {
	const envPath = path.join(root, ".env");
	if (!existsSync(envPath)) return;
	const contents = readFileSync(envPath, "utf8");
	for (const rawLine of contents.split(/\r?\n/)) {
		const line = rawLine.trim();
		if (!line || line.startsWith("#")) continue;
		const equalsIndex = line.indexOf("=");
		if (equalsIndex === -1) continue;
		const key = line.slice(0, equalsIndex).trim();
		if (!key) continue;
		let value = line.slice(equalsIndex + 1).trim();
		if (
			(value.startsWith("\"") && value.endsWith("\"")) ||
			(value.startsWith("'") && value.endsWith("'"))
		) {
			value = value.slice(1, -1);
		}
		if (process.env[key] === undefined) {
			process.env[key] = value;
		}
	}
}

function getRefreshIntervalMs(): number | null {
	const raw = process.env[MAINSEQUENCE_REFRESH_INTERVAL_ENV];
	if (!raw) return null;
	const seconds = Number.parseInt(raw, 10);
	if (!Number.isFinite(seconds) || seconds <= 0) {
		console.error(`[astro] ${MAINSEQUENCE_REFRESH_INTERVAL_ENV} must be a positive integer (seconds).`);
		process.exit(1);
	}
	return seconds * 1000;
}

function ensureMainsequenceEnv() {
	const missing = MAINSEQUENCE_REQUIRED_ENV.filter((key) => !process.env[key]);
	if (missing.length) {
		console.error(`[astro] Missing required env for token refresh: ${missing.join(", ")}`);
		process.exit(1);
	}
}

function startMainsequenceRefreshLoop() {
	const intervalMs = getRefreshIntervalMs();
	if (!intervalMs) return;
	ensureMainsequenceEnv();

	let refreshInFlight = false;
	const runLogin = (reason: string) => {
		if (refreshInFlight) return;
		refreshInFlight = true;
		const args = [
			"login",
			"--access-token",
			process.env.MAINSEQUENCE_ACCESS_TOKEN,
			"--refresh-token",
			process.env.MAINSEQUENCE_REFRESH_TOKEN,
			"--backend",
			process.env.MAINSEQUENCE_BACKEND,
			"--projects-base",
			process.env.MAINSEQUENCE_PROJECTS_BASE,
		];
		const child = spawn("mainsequence", args, {
			stdio: "inherit",
			shell: false,
			env: process.env,
		});

		child.on("exit", (code) => {
			refreshInFlight = false;
			if (typeof code === "number" && code !== 0) {
				console.error(`[astro] mainsequence login failed (${reason}) with code ${code}.`);
			}
		});

		child.on("error", (error: NodeJS.ErrnoException) => {
			refreshInFlight = false;
			if (error.code === "ENOENT") {
				console.error("[astro] Missing required command: mainsequence");
				return;
			}
			console.error(`[astro] mainsequence login error (${reason}): ${error.message}`);
		});
	};

	console.log(
		`[astro] Starting Main Sequence token refresh loop every ${Math.floor(intervalMs / 1000)}s...`,
	);
	runLogin("startup");
	setInterval(() => runLogin("interval"), intervalMs);
}

loadEnvFile(repoRoot);
startMainsequenceRefreshLoop();

const host = process.env.ASTRO_STREAM_HOST ?? "0.0.0.0";
const port = Number(process.env.ASTRO_STREAM_PORT ?? "8787");
const corsOrigin = process.env.ASTRO_STREAM_CORS_ORIGIN ?? "*";
const logTraffic = process.env.ASTRO_STREAM_LOG_TRAFFIC !== "0";
const sessionDir =
	process.env.ASTRO_STREAM_SESSION_DIR ?? path.join(repoRoot, ".astro", "stream-sessions");

type RequestContext = {
	res: import("node:http").ServerResponse;
	messageId: string;
	threadId: string;
	sessionKey: string;
	agentId: number | null;
	agentSessionId: number | null;
	eventId: number;
	textCounter: number;
	reasoningCounter: number;
	toolCallIds: Map<number, { toolCallId: string; toolName: string }>;
	finished: boolean;
};

type SessionMetadata = {
	agentId: number | null;
	agentUniqueId: string | null;
	agentSessionId: number | null;
	threadId: string | null;
	startedAt: string | null;
};

function json(res: import("node:http").ServerResponse, statusCode: number, body: unknown) {
	res.writeHead(statusCode, {
		"Content-Type": "application/json",
		"Access-Control-Allow-Origin": corsOrigin,
	});
	res.end(JSON.stringify(body));
}

function notFound(res: import("node:http").ServerResponse) {
	json(res, 404, { error: "not_found" });
}

function badRequest(res: import("node:http").ServerResponse, message: string) {
	json(res, 400, { error: "bad_request", message });
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

function resolveUserId(value: unknown): string | null {
	return resolveMainsequenceUserId({ userId: value, env: process.env });
}

function getNextSessionIndex(agentUniqueId: string): number {
	mkdirSync(sessionDir, { recursive: true });
	const prefix = `${agentUniqueId}__session_`;
	let maxIndex = 0;
	for (const entry of readdirSync(sessionDir)) {
		if (!entry.startsWith(prefix)) continue;
		const match = entry.match(/__session_(\d+)\./);
		if (!match) continue;
		const parsed = Number.parseInt(match[1], 10);
		if (Number.isFinite(parsed)) {
			maxIndex = Math.max(maxIndex, parsed);
		}
	}
	return maxIndex + 1;
}

function buildSessionKey(agentUniqueId: string, index: number): string {
	return sanitizeSessionKey(`${agentUniqueId}__session_${index}`);
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
		return {
			agentId: Number.isFinite(normalizedAgentId as number) ? (normalizedAgentId as number) : null,
			agentUniqueId: normalizedAgentUniqueId,
			agentSessionId: Number.isFinite(normalizedAgentSessionId as number)
				? (normalizedAgentSessionId as number)
				: null,
			threadId: normalizedThreadId,
			startedAt: normalizedStartedAt,
		};
	} catch {
		return null;
	}
}

function writeSessionMetadata(sessionKey: string, metadata: SessionMetadata) {
	mkdirSync(sessionDir, { recursive: true });
	writeFileSync(getSessionMetadataPath(sessionKey), JSON.stringify(metadata, null, 2));
}

function writeChunk(ctx: RequestContext, chunk: StreamEvent) {
	if (ctx.finished) return;
	ctx.eventId += 1;
	const payload = serializeSse(ctx.eventId, attachAgentId(chunk, ctx.agentId));
	ctx.res.write(payload);
	if (logTraffic) {
		console.log(
			`[astro-stream] OUT ${ctx.threadId}: ${JSON.stringify(attachAgentId(chunk, ctx.agentId))}`,
		);
	}
}

function writeDone(ctx: RequestContext) {
	if (ctx.finished) return;
	ctx.res.write("data: [DONE]\n\n");
	if (logTraffic) {
		console.log(`[astro-stream] OUT ${ctx.threadId}: [DONE]`);
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

function handleAssistantDelta(ctx: RequestContext, evt: any) {
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
			ctx.textCounter += 1;
			const id = `t${ctx.textCounter}`;
			writeChunk(ctx, { type: "text-start", id });
			return;
		}
		case "text_delta":
			if (typeof evt.delta === "string") {
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
			const usage = evt.message?.usage ?? evt.partial?.usage;
			const mappedUsage = usage
				? {
						inputTokens: usage.input ?? usage.inputTokens,
						outputTokens: usage.output ?? usage.outputTokens,
				  }
				: undefined;
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

function runPiPrompt(prompt: string, ctx: RequestContext) {
	mkdirSync(sessionDir, { recursive: true });
	const sessionPath = getSessionPath(ctx.sessionKey);
	const args = ["--mode", "json", "--session", sessionPath, prompt];
	const child = spawn("pi", args, {
		cwd: repoRoot,
		env: {
			...process.env,
			ASTRO_TELEMETRY: "0",
		},
		stdio: ["ignore", "pipe", "pipe"],
	});

	const stdout = createInterface({ input: child.stdout });
	stdout.on("line", (line) => {
		let parsed: any;
		try {
			parsed = JSON.parse(line);
		} catch {
			if (logTraffic) {
				console.log(`[astro-stream] NONJSON ${ctx.threadId}: ${line}`);
			}
			return;
		}

		if (parsed?.type === "message_update") {
			if (parsed.message?.role !== "assistant") return;
			const evt = parsed.assistantMessageEvent;
			if (!evt || typeof evt.type !== "string") return;
			handleAssistantDelta(ctx, evt);
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

	const stderr = createInterface({ input: child.stderr });
	stderr.on("line", (line) => {
		if (logTraffic) {
			console.log(`[astro-stream] STDERR ${ctx.threadId}: ${line}`);
		}
		if (!ctx.finished) {
			writeChunk(ctx, { type: "error", error: line });
			writeDone(ctx);
		}
	});

	child.on("exit", (code, signal) => {
		if (ctx.finished) return;
		if (signal || (typeof code === "number" && code !== 0)) {
			const reason = signal ? `Process exited with signal ${signal}.` : `Process exited with code ${code}.`;
			writeChunk(ctx, { type: "error", error: reason });
			writeDone(ctx);
			return;
		}

		writeChunk(ctx, { type: "finish", finishReason: "stop" });
		writeDone(ctx);
	});
}

const server = createServer(async (req, res) => {
	const url = new URL(req.url ?? "/", `http://${req.headers.host ?? host}`);

	if (req.method === "OPTIONS") {
		res.writeHead(204, {
			"Access-Control-Allow-Origin": corsOrigin,
			"Access-Control-Allow-Methods": "GET,POST,OPTIONS",
			"Access-Control-Allow-Headers": "Content-Type, Authorization, Last-Event-ID",
		});
		res.end();
		return;
	}

	if (req.method === "GET" && url.pathname === "/health") {
		json(res, 200, { ok: true });
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

	if (logTraffic) {
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

	const agentName = normalizeAgentName(body.agentName);
	if (!agentName) {
		badRequest(res, "Missing agentName.");
		return;
	}
	if (!ALLOWED_AGENTS.has(agentName)) {
		json(res, 400, { error: "unknown_agent", message: `Unknown agent "${agentName}".` });
		return;
	}

	const userId = resolveUserId(body.userId);
	if (!userId) {
		badRequest(res, "Missing or invalid userId.");
		return;
	}

	const system = typeof body.system === "string" ? body.system : undefined;
	const prompt = buildPrompt(system, latestUserMessage, context, tools);
	const newChat = body.newChat === true;
	if (body.newChat !== undefined && typeof body.newChat !== "boolean") {
		badRequest(res, "`newChat` must be a boolean.");
		return;
	}
	const threadId = typeof body.threadId === "string" && body.threadId.trim() ? body.threadId : randomUUID();
	const registrationRequired = shouldRegisterAgents(process.env);
	if (!registrationRequired) {
		json(res, 503, {
			error: "agent_registration_disabled",
			message: "BUILD_AGENTS_IN_BACKEND must be enabled for session creation.",
		});
		return;
	}

	const registration = await registerMainsequenceAgent({
		agentName,
		agentRole: agentName === "astro-orchestrator" ? "orchestrator" : "specialist",
		cwd: repoRoot,
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

	const agentId = registration.agentId;
	const agentUniqueId =
		registration.agentUniqueId ?? buildAgentUniqueId({ agentName, userId });

	let sessionKey: string;
	let agentSessionId: number | null = null;
	let startedAt: string | null = null;

	if (newChat) {
		const sessionIndex = getNextSessionIndex(agentUniqueId);
		sessionKey = buildSessionKey(agentUniqueId, sessionIndex);
		startedAt = new Date().toISOString();
		const sessionMetadata = isPlainObject(body.sessionMetadata) ? body.sessionMetadata : {};
		const runtimeConfig = {
			temperature: 0.35,
			top_p: 0.9,
			max_output_tokens: 4000,
			reasoning_effort: "medium",
		};

		const payload: Record<string, unknown> = {
			status: "running",
			started_at: startedAt,
			ended_at: null,
			created_by_user: userId,
			llm_provider: "openai",
			llm_model: "gpt-5.4",
			engine_name: "astro_router_v1",
			runtime_config_snapshot: runtimeConfig,
			error_detail: "",
			external_session_id:
				typeof body.external_session_id === "string" ? body.external_session_id : "",
			runtime_session_id: sessionKey,
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
				...sessionMetadata,
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
		writeSessionMetadata(sessionKey, {
			agentId,
			agentUniqueId,
			agentSessionId,
			threadId,
			startedAt,
		});
	} else {
		const runtimeSessionId = normalizeRuntimeSessionId(
			(body.runtime_session_id as unknown) ?? (body.runtimeSessionId as unknown),
		);
		if (!runtimeSessionId) {
			json(res, 400, {
				error: "missing_runtime_session_id",
				message: "runtime_session_id is required when newChat is false.",
			});
			return;
		}
		if (!runtimeSessionId.startsWith(`${agentUniqueId}__session_`)) {
			json(res, 409, {
				error: "session_mismatch",
				message: "runtime_session_id does not match the active agent.",
			});
			return;
		}
		if (!sessionExists(runtimeSessionId)) {
			json(res, 409, {
				error: "session_not_found",
				message: "No local session found for the provided runtime_session_id.",
			});
			return;
		}
		const existing = readSessionMetadata(runtimeSessionId);
		agentSessionId = existing?.agentSessionId ?? null;
		startedAt = existing?.startedAt ?? null;
		sessionKey = runtimeSessionId;
	}

	res.writeHead(200, {
		"Content-Type": "text/event-stream",
		"Cache-Control": "no-cache",
		Connection: "keep-alive",
		"Access-Control-Allow-Origin": corsOrigin,
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
		agentSessionId,
		eventId: 0,
		textCounter: 0,
		reasoningCounter: 0,
		toolCallIds: new Map(),
		finished: false,
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
	runPiPrompt(prompt, ctx);
});

server.listen(port, host, () => {
	console.log(`[astro-stream] Listening on http://${host}:${port}`);
	console.log("[astro-stream] POST /api/chat to start a data-stream response");
});
