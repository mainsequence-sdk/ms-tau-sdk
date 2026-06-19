#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const ASTRO_MAINSEQUENCE_CONFIG_DIR = path.join(REPO_ROOT, ".astro", "mainsequence-config");
const DEFAULT_PORT = process.env.ASTRO_STREAM_PORT || "8787";
const DEFAULT_BASE_URL = process.env.ASTRO_A2A_BASE_URL || `http://127.0.0.1:${DEFAULT_PORT}`;
const DEFAULT_AGENT_SESSION_UID =
	process.env.ASTRO_A2A_AGENT_SESSION_UID || "0b2701a1-e777-4cfe-8437-b94025f00069";

const DEFAULT_TURN_COUNT = 10;
const DEFAULT_CANCEL_AFTER_MS = 500;
const DEFAULT_TIMEOUT_TURN_MS = 1000;
const DEFAULT_TURN_PATTERN = [
	"cancel",
	"success",
	"timeout",
	"success",
	"cancel",
	"success",
	"success",
	"timeout",
	"success",
	"success",
];

function usage() {
	return [
		"Usage:",
		"  node tools/a2a/stress-live.mjs --agent-session-uid <uid> [--base-url http://127.0.0.1:8787]",
		"",
		"Runs a live 10-turn A2A workflow against the running Astro endpoint.",
		"Every turn prints request first, then the response/cancel/timeout result.",
		"Every turn uses the same message.contextId so it shows same-session reuse:",
		"  A: cancel after --cancel-after-ms",
		"  B: wait for full response",
		"  C: client timeout after --timeout-turn-ms",
		"  D: wait for full response",
		"  E: cancel after --cancel-after-ms",
		"  F: wait for full response",
		"  G: wait for full response",
		"  H: client timeout after --timeout-turn-ms",
		"  I: wait for full response",
		"  J: wait for full response",
		"",
		"Useful flags:",
		"  --turns <n>               default 10",
		"  --cancel-after-ms <ms>    default 500",
		"  --timeout-turn-ms <ms>    default 1000",
		"  --timeout-ms <ms>         hard safety timeout per request, default 240000",
		"",
		"Environment fallbacks:",
		"  ASTRO_A2A_BASE_URL, ASTRO_STREAM_PORT, ASTRO_A2A_AGENT_SESSION_UID, ASTRO_A2A_BEARER_TOKEN, ASTRO_A2A_USER_UID",
		"  MAINSEQUENCE_RUNTIME_CREDENTIAL_ID, MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET, MAINSEQUENCE_BACKEND",
	].join("\n");
}

function parseArgs(argv) {
	const flags = {};
	for (let index = 0; index < argv.length; index += 1) {
		const value = argv[index];
		if (!value.startsWith("--")) continue;
		const key = value.slice(2);
		const next = argv[index + 1];
		if (next == null || next.startsWith("--")) {
			flags[key] = true;
			continue;
		}
		flags[key] = next;
		index += 1;
	}
	return flags;
}

function stringFlag(flags, key, fallback = "") {
	const value = flags[key];
	if (typeof value === "string" && value.trim()) return value.trim();
	return fallback;
}

function numberFlag(flags, key, fallback) {
	const value = Number(flags[key] ?? "");
	return Number.isFinite(value) && value > 0 ? Math.trunc(value) : fallback;
}

function requireValue(value, message) {
	if (value) return value;
	throw new Error(message);
}

function buildUrl(baseUrl, pathValue) {
	return `${baseUrl.replace(/\/+$/, "")}${pathValue}`;
}

function loadEnvFile() {
	const envPath = path.join(REPO_ROOT, ".env");
	if (!existsSync(envPath)) return;
	const contents = readFileSync(envPath, "utf8");
	for (const rawLine of contents.split(/\r?\n/)) {
		const line = rawLine.trim();
		if (!line || line.startsWith("#")) continue;
		const equalsIndex = line.indexOf("=");
		if (equalsIndex === -1) continue;
		const key = line.slice(0, equalsIndex).trim();
		if (!key || process.env[key] !== undefined) continue;
		let value = line.slice(equalsIndex + 1).trim();
		if (
			(value.startsWith("\"") && value.endsWith("\"")) ||
			(value.startsWith("'") && value.endsWith("'"))
		) {
			value = value.slice(1, -1);
		}
		process.env[key] = value;
	}
}

function readJsonObject(filePath) {
	try {
		if (!existsSync(filePath)) return null;
		const parsed = JSON.parse(readFileSync(filePath, "utf8"));
		return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
	} catch {
		return null;
	}
}

function normalizeBearerToken(value) {
	if (!value) return "";
	return value.replace(/^Bearer\s+/i, "").trim();
}

function resolveBackendUrl() {
	const config = readJsonObject(path.join(ASTRO_MAINSEQUENCE_CONFIG_DIR, "config.json"));
	const raw =
		process.env.MAINSEQUENCE_BACKEND ||
		process.env.MAIN_SEQUENCE_BACKEND_URL ||
		process.env.MAINSEQUENCE_ENDPOINT ||
		process.env.TDAG_ENDPOINT ||
		(typeof config?.backend_url === "string" ? config.backend_url : "") ||
		"https://api.main-sequence.app";
	return raw.replace(/\/+$/, "");
}

function hasRuntimeCredentialEnv() {
	return Boolean(
		process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_ID?.trim() &&
			process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET?.trim(),
	);
}

function normalizeHeaderRecord(value) {
	if (!value || typeof value !== "object" || Array.isArray(value)) return null;
	const headers = {};
	for (const [key, rawValue] of Object.entries(value)) {
		if (!key.trim() || typeof rawValue !== "string" || !rawValue.trim()) continue;
		headers[key] = rawValue;
	}
	return Object.keys(headers).length ? headers : null;
}

function extractRuntimeCredentialHeaders(body) {
	const explicitHeaders = normalizeHeaderRecord(body?.headers) || normalizeHeaderRecord(body?.auth_headers);
	if (explicitHeaders?.Authorization || explicitHeaders?.authorization) return explicitHeaders;

	const access =
		body?.access ||
		body?.access_token ||
		body?.token ||
		body?.jwt ||
		body?.data?.access ||
		body?.data?.access_token ||
		body?.data?.token ||
		body?.data?.jwt;
	const normalizedAccess = normalizeBearerToken(typeof access === "string" ? access : "");
	return normalizedAccess ? { Authorization: `Bearer ${normalizedAccess}` } : null;
}

async function resolveRuntimeCredentialAuthHeaders() {
	if (!hasRuntimeCredentialEnv()) return { headers: null, source: "runtime_credential", error: "missing env" };
	try {
		const response = await fetch(`${resolveBackendUrl()}/orm/api/pods/runtime-credentials/token/`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				credential_id: process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_ID,
				credential_secret: process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET,
			}),
		});
		const raw = await response.text();
		const body = raw ? JSON.parse(raw) : null;
		if (!response.ok) {
			return {
				headers: null,
				source: "runtime_credential",
				error: body?.detail || body?.message || response.statusText,
			};
		}
		const headers = extractRuntimeCredentialHeaders(body);
		return headers
			? { headers, source: "runtime_credential", error: null }
			: { headers: null, source: "runtime_credential", error: "no token returned" };
	} catch (error) {
		return {
			headers: null,
			source: "runtime_credential",
			error: error instanceof Error ? error.message : String(error),
		};
	}
}

function findExecutable(command) {
	const pathEnv = process.env.PATH || "";
	for (const entry of pathEnv.split(path.delimiter)) {
		if (!entry) continue;
		const candidate = path.join(entry, command);
		try {
			const stat = statSync(candidate);
			if (stat.isFile()) return candidate;
		} catch {
			// Not in this PATH entry.
		}
	}
	return null;
}

function resolveMainsequenceCliPython() {
	const configured = process.env.MAINSEQUENCE_SDK_PYTHON?.trim();
	if (configured) return configured;
	const cliPath = findExecutable("mainsequence");
	if (!cliPath) return null;
	try {
		const firstLine = readFileSync(cliPath, "utf8").split(/\r?\n/, 1)[0] || "";
		if (!firstLine.startsWith("#!")) return null;
		const executable = firstLine.slice(2).trim().split(/\s+/, 1)[0];
		return executable || null;
	} catch {
		return null;
	}
}

function buildSdkAuthEnv() {
	const env = { ...process.env };
	const config = readJsonObject(path.join(ASTRO_MAINSEQUENCE_CONFIG_DIR, "config.json"));
	const auth = readJsonObject(path.join(ASTRO_MAINSEQUENCE_CONFIG_DIR, "auth.json"));
	const backendUrl = resolveBackendUrl();
	if (backendUrl) {
		env.MAINSEQUENCE_ENDPOINT = env.MAINSEQUENCE_ENDPOINT || backendUrl;
		env.TDAG_ENDPOINT = env.TDAG_ENDPOINT || backendUrl;
	}
	if (typeof auth?.access === "string" && auth.access.trim()) {
		env.MAINSEQUENCE_ACCESS_TOKEN = env.MAINSEQUENCE_ACCESS_TOKEN || auth.access.trim();
	}
	if (typeof auth?.refresh === "string" && auth.refresh.trim()) {
		env.MAINSEQUENCE_REFRESH_TOKEN = env.MAINSEQUENCE_REFRESH_TOKEN || auth.refresh.trim();
	}
	if (env.MAINSEQUENCE_ACCESS_TOKEN || env.MAINSEQUENCE_REFRESH_TOKEN) {
		env.MAINSEQUENCE_AUTH_MODE = env.MAINSEQUENCE_AUTH_MODE || "jwt";
	}
	env.TDAG_ROOT_PATH = env.TDAG_ROOT_PATH || path.join(REPO_ROOT, ".astro", "tdag-debug");
	return env;
}

function resolveSdkAuthHeaders() {
	const pythonExecutable = resolveMainsequenceCliPython() || "python3";
	const script = [
		"import json",
		"import mainsequence",
		"mainsequence.prime_runtime_env()",
		"from mainsequence.client.utils import get_authorization_headers",
		"print(json.dumps(dict(get_authorization_headers())))",
	].join("; ");
	const result = spawnSync(pythonExecutable, ["-c", script], {
		stdio: ["ignore", "pipe", "pipe"],
		env: buildSdkAuthEnv(),
		encoding: "utf8",
	});
	if (result.status !== 0 || result.error) {
		return {
			headers: null,
			source: "sdk",
			error: result.error?.message || result.stderr.trim().split(/\r?\n/).at(-1) || "SDK auth failed",
		};
	}
	const lastJsonLine = result.stdout
		.split(/\r?\n/)
		.map((line) => line.trim())
		.filter(Boolean)
		.at(-1);
	if (!lastJsonLine) return { headers: null, source: "sdk", error: "SDK auth returned no JSON" };
	try {
		return {
			headers: normalizeHeaderRecord(JSON.parse(lastJsonLine)),
			source: "sdk",
			error: null,
		};
	} catch {
		return { headers: null, source: "sdk", error: "SDK auth returned invalid JSON" };
	}
}

async function buildRequestHeaders({ bearerToken, userUid }) {
	const headers = {
		"Content-Type": "application/a2a+json",
		Accept: "application/a2a+json",
	};
	const normalizedBearerToken = normalizeBearerToken(bearerToken);
	if (normalizedBearerToken) {
		headers.Authorization = `Bearer ${normalizedBearerToken}`;
		return { headers, authSource: "manual_bearer", authError: null };
	}
	if (hasRuntimeCredentialEnv()) {
		const runtimeCredentialAuth = await resolveRuntimeCredentialAuthHeaders();
		if (runtimeCredentialAuth.headers) {
			return { headers: { ...headers, ...runtimeCredentialAuth.headers }, authSource: runtimeCredentialAuth.source };
		}
	}
	const sdkAuth = resolveSdkAuthHeaders();
	if (sdkAuth.headers) return { headers: { ...headers, ...sdkAuth.headers }, authSource: sdkAuth.source };
	if (userUid) {
		headers["X-Mainsequence-User-Uid"] = userUid;
		return { headers, authSource: "debug_user_uid" };
	}
	return { headers, authSource: "none", authError: sdkAuth.error };
}

function redactHeaders(headers) {
	const redacted = { ...headers };
	for (const key of Object.keys(redacted)) {
		if (key.toLowerCase() === "authorization") redacted[key] = "Bearer <redacted>";
	}
	return redacted;
}

function print(label, value) {
	console.log(`\n=== ${label} ===`);
	console.log(JSON.stringify(value, null, 2));
}

function printWorkflowTurn(turn, totalTurns) {
	console.log(`\n--- workflow turn ${turn.index}/${totalTurns}: ${turn.label} ${turn.mode} ---`);
}

function turnLabel(index) {
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
	return alphabet[index] || `T${index + 1}`;
}

function buildDefaultPrompt(label) {
	return [
		"Return JSON only.",
		"Do not use tools.",
		"Use exactly these keys: ok, request, answer.",
		`Set ok to true, request to "${label}", and answer to "${label} completed".`,
	].join(" ");
}

function buildTurnPlan({ turnCount, cancelAfterMs, timeoutTurnMs }) {
	return Array.from({ length: turnCount }, (_, index) => {
		const label = turnLabel(index);
		const mode = DEFAULT_TURN_PATTERN[index % DEFAULT_TURN_PATTERN.length];
		return {
			index: index + 1,
			label,
			mode,
			abortAfterMs: mode === "timeout" ? timeoutTurnMs : mode === "cancel" ? cancelAfterMs : null,
		};
	});
}

function buildPayload({ messageId, contextId, text }) {
	return {
		message: {
			messageId,
			role: "ROLE_USER",
			contextId,
			parts: [{ text }],
		},
		configuration: {
			acceptedOutputModes: ["application/json"],
			historyLength: 0,
			returnImmediately: false,
		},
		metadata: {
			"https://mainsequence.ai/a2a/extensions/output-contract/v1": {
				response_format: {
					type: "dictionary",
					strict: true,
				},
				jsonRepairAttempts: 3,
			},
		},
	};
}

function parseResponseBody(raw) {
	try {
		return raw ? JSON.parse(raw) : null;
	} catch {
		return null;
	}
}

function extractA2AAnswer(body) {
	const messageParts = Array.isArray(body?.message?.parts) ? body.message.parts : [];
	for (const part of messageParts) {
		if (typeof part?.text === "string" && part.text.trim()) return part.text.trim();
		if (part?.data !== undefined) return part.data;
	}

	const artifacts = Array.isArray(body?.task?.artifacts) ? body.task.artifacts : [];
	for (const artifact of artifacts) {
		const artifactParts = Array.isArray(artifact?.parts) ? artifact.parts : [];
		for (const part of artifactParts) {
			if (typeof part?.text === "string" && part.text.trim()) return part.text.trim();
			if (part?.data !== undefined) return part.data;
		}
	}

	return null;
}

async function sendA2A({ label, url, headers, payload, timeoutMs }) {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(new Error(`${label}_timeout`)), timeoutMs);
	const startedAt = Date.now();
	try {
		print(`${label} request -> POST /api/a2a/v1/message:send`, {
			method: "POST",
			url,
			headers: redactHeaders(headers),
			body: payload,
		});
		const response = await fetch(url, {
			method: "POST",
			headers,
			body: JSON.stringify(payload),
			signal: controller.signal,
		});
		const raw = await response.text();
		const body = parseResponseBody(raw);
		const llmAnswer = extractA2AAnswer(body);
		const result = {
			label,
			mode: "success",
			ok: response.ok,
			status: response.status,
			statusText: response.statusText,
			durationMs: Date.now() - startedAt,
			llmAnswer,
			body,
			raw: body ? undefined : raw,
		};
		print(`${label} response <- HTTP ${response.status}`, result);
		print(`${label} LLM answer`, llmAnswer);
		return result;
	} finally {
		clearTimeout(timer);
	}
}

async function sendAndAbort({ label, mode, url, headers, payload, abortAfterMs, timeoutMs }) {
	const controller = new AbortController();
	const abortReason = mode === "timeout" ? `live_${label.toLowerCase()}_client_timeout` : `live_${label.toLowerCase()}_client_cancel`;
	const abortTimer = setTimeout(() => controller.abort(new Error(abortReason)), abortAfterMs);
	const timeoutTimer = setTimeout(() => controller.abort(new Error(`live_${label.toLowerCase()}_hard_timeout`)), timeoutMs);
	const startedAt = Date.now();
	print(`${label} request -> POST /api/a2a/v1/message:send`, {
		method: "POST",
		url,
		headers: redactHeaders(headers),
		mode,
		abortAfterMs,
		body: payload,
	});
	try {
		const response = await fetch(url, {
			method: "POST",
			headers,
			body: JSON.stringify(payload),
			signal: controller.signal,
		});
		const raw = await response.text();
		const body = parseResponseBody(raw);
		const result = {
			label,
			mode,
			aborted: false,
			ok: response.ok,
			status: response.status,
			statusText: response.statusText,
			durationMs: Date.now() - startedAt,
			body,
			raw: body ? undefined : raw,
		};
		print(`${label} response before ${mode} window <- HTTP ${response.status}`, result);
		return result;
	} catch (error) {
		const result = {
			label,
			mode,
			aborted: controller.signal.aborted,
			cancelled: mode === "cancel" && controller.signal.aborted,
			timedOut: mode === "timeout" && controller.signal.aborted,
			errorName: error instanceof Error ? error.name : null,
			errorMessage: error instanceof Error ? error.message : String(error),
			durationMs: Date.now() - startedAt,
		};
		print(`${label} client ${mode}`, result);
		return result;
	} finally {
		clearTimeout(abortTimer);
		clearTimeout(timeoutTimer);
	}
}

async function main() {
	loadEnvFile();
	const flags = parseArgs(process.argv.slice(2));
	if (flags.help) {
		console.log(usage());
		return;
	}

	const baseUrl = stringFlag(flags, "base-url", DEFAULT_BASE_URL);
	const agentSessionUid = requireValue(
		stringFlag(flags, "agent-session-uid", DEFAULT_AGENT_SESSION_UID),
		"Missing --agent-session-uid or ASTRO_A2A_AGENT_SESSION_UID.",
	);
	const bearerToken = stringFlag(
		flags,
		"bearer-token",
		process.env.ASTRO_A2A_BEARER_TOKEN || process.env.MAINSEQUENCE_ACCESS_TOKEN || "",
	);
	const userUid = stringFlag(flags, "user-uid", process.env.ASTRO_A2A_USER_UID || "");
	const turnCount = numberFlag(flags, "turns", DEFAULT_TURN_COUNT);
	const cancelAfterMs = numberFlag(flags, "cancel-after-ms", DEFAULT_CANCEL_AFTER_MS);
	const timeoutTurnMs = numberFlag(flags, "timeout-turn-ms", DEFAULT_TIMEOUT_TURN_MS);
	const timeoutMs = numberFlag(flags, "timeout-ms", 240000);
	const url = buildUrl(baseUrl, "/api/a2a/v1/message:send");
	const { headers, authSource, authError } = await buildRequestHeaders({ bearerToken, userUid });
	if (authSource === "none") {
		throw new Error(`Could not resolve auth for live A2A stress request: ${authError || "unknown auth error"}`);
	}
	const plan = buildTurnPlan({ turnCount, cancelAfterMs, timeoutTurnMs });

	print("workflow config", {
		baseUrl,
		url,
		agentSessionUid,
		authSource,
		turnCount,
		cancelAfterMs,
		timeoutTurnMs,
		timeoutMs,
		plan,
	});

	const stamp = Date.now();
	const results = [];
	for (const turn of plan) {
		printWorkflowTurn(turn, plan.length);
		const promptFlag = `${turn.label.toLowerCase()}-prompt`;
		const payload = buildPayload({
			messageId: `msg-live-${turn.label.toLowerCase()}-${stamp}-${turn.index}`,
			contextId: agentSessionUid,
			text: stringFlag(flags, promptFlag, buildDefaultPrompt(turn.label)),
		});

		if (turn.mode === "success") {
			const result = await sendA2A({ label: turn.label, url, headers, payload, timeoutMs });
			results.push(result);
			if (!result.ok) throw new Error(`Request ${turn.label} failed with HTTP ${result.status}.`);
			continue;
		}

		const result = await sendAndAbort({
			label: turn.label,
			mode: turn.mode,
			url,
			headers,
			payload,
			abortAfterMs: turn.abortAfterMs,
			timeoutMs,
		});
		results.push(result);
		if (!result.aborted) {
			throw new Error(
				`Request ${turn.label} completed before the ${turn.mode} timer fired; workflow did not exercise ${turn.mode}.`,
			);
		}
	}

	print("workflow summary", {
		totalTurns: results.length,
		cancelledTurns: results.filter((result) => result.cancelled).length,
		timedOutTurns: results.filter((result) => result.timedOut).length,
		successTurns: results.filter((result) => result.mode === "success" && result.ok).length,
		results: results.map((result) => ({
			label: result.label,
			mode: result.mode,
			status: result.status ?? null,
			aborted: result.aborted ?? false,
			cancelled: result.cancelled ?? false,
			timedOut: result.timedOut ?? false,
			durationMs: result.durationMs,
			errorMessage: result.errorMessage,
		})),
	});
}

main().catch((error) => {
	console.error(error instanceof Error ? error.message : String(error));
	process.exitCode = 1;
});
