import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { serializeSse, type StreamChunk } from "./protocol.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const MAINSEQUENCE_REFRESH_INTERVAL_ENV = "MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS";
const MAINSEQUENCE_REQUIRED_ENV = [
	"MAINSEQUENCE_ACCESS_TOKEN",
	"MAINSEQUENCE_REFRESH_TOKEN",
	"MAINSEQUENCE_BACKEND",
	"MAINSEQUENCE_PROJECTS_BASE",
];

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
	eventId: number;
	textCounter: number;
	reasoningCounter: number;
	toolCallIds: Map<number, { toolCallId: string; toolName: string }>;
	finished: boolean;
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

function writeChunk(ctx: RequestContext, chunk: StreamChunk) {
	if (ctx.finished) return;
	ctx.eventId += 1;
	const payload = serializeSse(ctx.eventId, chunk);
	ctx.res.write(payload);
	if (logTraffic) {
		console.log(`[astro-stream] OUT ${ctx.threadId}: ${JSON.stringify(chunk)}`);
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

function buildPrompt(system: string | undefined, messages: any[]): string {
	const lines: string[] = [];
	if (system) lines.push(`System: ${system}`);

	lines.push("Conversation history:");
	for (const msg of messages) {
		if (!msg || typeof msg !== "object") continue;
		const role = msg.role ?? "unknown";
		const content = extractText(msg.content);
		if (!content) continue;
		lines.push(`${role}: ${content}`);
	}

	lines.push("Respond to the latest user message above.");
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
	const sessionPath = path.join(sessionDir, `${ctx.threadId}.jsonl`);
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

	const system = typeof body.system === "string" ? body.system : undefined;
	const prompt = buildPrompt(system, messages);
	const threadId = typeof body.threadId === "string" && body.threadId.trim() ? body.threadId : randomUUID();

	res.writeHead(200, {
		"Content-Type": "text/event-stream",
		"Cache-Control": "no-cache",
		Connection: "keep-alive",
		"Access-Control-Allow-Origin": corsOrigin,
		"X-Thread-Id": threadId,
		"X-Stream-Protocol": "ui-message-stream",
		Protocol: "ui-message-stream",
	});

	res.write("retry: 1000\n\n");

	const messageId = `msg_${Date.now().toString(36)}_${Math.floor(Math.random() * 1e6).toString(36)}`;
	const ctx: RequestContext = {
		res,
		messageId,
		threadId,
		eventId: 0,
		textCounter: 0,
		reasoningCounter: 0,
		toolCallIds: new Map(),
		finished: false,
	};

	writeChunk(ctx, { type: "start", messageId });
	runPiPrompt(prompt, ctx);
});

server.listen(port, host, () => {
	console.log(`[astro-stream] Listening on http://${host}:${port}`);
	console.log("[astro-stream] POST /api/chat to start a data-stream response");
});
