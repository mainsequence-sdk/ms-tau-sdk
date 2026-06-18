#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_PORT = process.env.ASTRO_STREAM_PORT || "8787";
const DEFAULT_BASE_URL = process.env.ASTRO_A2A_BASE_URL || `http://127.0.0.1:${DEFAULT_PORT}`;
const DEFAULT_REPAIR_ATTEMPTS = 3;
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ASTRO_MAINSEQUENCE_CONFIG_DIR = path.join(REPO_ROOT, ".astro", "mainsequence-config");

const PROMPTS = {
	capabilities:
		"Return a JSON dictionary describing what this Astro agent can do. Use exactly these top-level keys: summary, capabilities, limits, recommended_next_steps. capabilities must be an array of short strings. limits must be an array of short strings. recommended_next_steps must be an array of short strings.",
	dictionary:
		"Return a JSON dictionary only. Use exactly these top-level keys: ok, request, answer, example. ok must be true. request must summarize this request. answer must explain that the response is a dictionary. example must be a nested dictionary with two string keys.",
};

function usage() {
	return [
		"Usage:",
		"  node scripts/debug_a2a_endpoint.mjs <capabilities|dictionary> --agent-session-uid <uid> [--bearer-token <token>|--user-uid <uid>]",
		"",
		"This sends exactly one public A2A request to POST /api/a2a/v1/message:send.",
		"It does not call /api/chat, /api/a2a/chat, or any session runtime chat route.",
		"",
		"Environment fallbacks:",
		"  ASTRO_A2A_BASE_URL, ASTRO_STREAM_PORT, ASTRO_A2A_AGENT_SESSION_UID, ASTRO_A2A_BEARER_TOKEN, ASTRO_A2A_USER_UID",
		"  MAINSEQUENCE_RUNTIME_CREDENTIAL_ID, MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET, MAINSEQUENCE_BACKEND, MAINSEQUENCE_PROJECTS_BASE",
		"",
		"Auth resolution:",
		"  1. --bearer-token / ASTRO_A2A_BEARER_TOKEN",
		"  2. MAINSEQUENCE_RUNTIME_CREDENTIAL_ID / MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET exchange from .env",
		"  3. Main Sequence SDK get_authorization_headers() with Astro local CLI auth",
		"  4. --user-uid / ASTRO_A2A_USER_UID local debug fallback",
	].join("\n");
}

function parseArgs(argv) {
	const positional = [];
	const flags = {};
	for (let index = 0; index < argv.length; index += 1) {
		const value = argv[index];
		if (!value.startsWith("--")) {
			positional.push(value);
			continue;
		}
		const key = value.slice(2);
		const next = argv[index + 1];
		if (next == null || next.startsWith("--")) {
			flags[key] = true;
			continue;
		}
		flags[key] = next;
		index += 1;
	}
	return { positional, flags };
}

function stringFlag(flags, key, fallback = "") {
	const value = flags[key];
	if (typeof value === "string" && value.trim()) return value.trim();
	return fallback;
}

function requireValue(value, message) {
	if (value) return value;
	throw new Error(message);
}

function buildUrl(baseUrl, path) {
	return `${baseUrl.replace(/\/+$/, "")}${path}`;
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

function redactRuntimeCredentialText(text) {
	let redacted = text;
	for (const key of ["MAINSEQUENCE_RUNTIME_CREDENTIAL_ID", "MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET"]) {
		const value = process.env[key]?.trim();
		if (!value) continue;
		redacted = redacted.split(value).join(`[redacted:${key}]`);
	}
	return redacted;
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
	if (!hasRuntimeCredentialEnv()) {
		return {
			headers: null,
			source: "runtime_credential",
			error: "runtime credential env is incomplete.",
		};
	}

	let response;
	try {
		response = await fetch(`${resolveBackendUrl()}/orm/api/pods/runtime-credentials/token/`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				credential_id: process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_ID,
				credential_secret: process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET,
			}),
		});
	} catch (error) {
		return {
			headers: null,
			source: "runtime_credential",
			error: `runtime credential exchange could not reach backend: ${error instanceof Error ? error.message : String(error)}`,
		};
	}
	const raw = await response.text();
	let body = null;
	try {
		body = raw ? JSON.parse(raw) : null;
	} catch {
		body = null;
	}
	if (!response.ok) {
		const detail =
			body?.detail ||
			body?.message ||
			body?.error?.message ||
			redactRuntimeCredentialText(raw).replace(/\s+/g, " ").trim() ||
			response.statusText;
		return {
			headers: null,
			source: "runtime_credential",
			error: `runtime credential exchange failed with HTTP ${response.status}: ${detail}`,
		};
	}

	const headers = extractRuntimeCredentialHeaders(body);
	if (!headers) {
		return {
			headers: null,
			source: "runtime_credential",
			error: "runtime credential exchange did not return an access token/header.",
		};
	}
	return {
		headers,
		source: "runtime_credential",
		error: null,
	};
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
	if (typeof auth?.username === "string" && auth.username.trim()) {
		env.MAINSEQUENCE_USERNAME = env.MAINSEQUENCE_USERNAME || auth.username.trim();
	}
	if (env.MAINSEQUENCE_ACCESS_TOKEN || env.MAINSEQUENCE_REFRESH_TOKEN) {
		env.MAINSEQUENCE_AUTH_MODE = env.MAINSEQUENCE_AUTH_MODE || "jwt";
	}

	// Keep SDK logging/checkpoint scratch files inside this repo during local debug.
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
	if (result.error) {
		return {
			headers: null,
			source: "sdk",
			error: result.error.message,
		};
	}
	if (result.status !== 0) {
		const message =
			result.stderr.trim().split(/\r?\n/).at(-1) ||
			result.stdout.trim().split(/\r?\n/).at(-1) ||
			`SDK auth helper exited with code ${result.status ?? 1}.`;
		return {
			headers: null,
			source: "sdk",
			error: message,
		};
	}
	const lastJsonLine = result.stdout
		.split(/\r?\n/)
		.map((line) => line.trim())
		.filter(Boolean)
		.at(-1);
	if (!lastJsonLine) {
		return {
			headers: null,
			source: "sdk",
			error: "SDK auth helper returned no JSON output.",
		};
	}
	try {
		return {
			headers: normalizeHeaderRecord(JSON.parse(lastJsonLine)),
			source: "sdk",
			error: null,
		};
	} catch {
		return {
			headers: null,
			source: "sdk",
			error: "SDK auth helper returned invalid JSON.",
		};
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
			return {
				headers: {
					...headers,
					...runtimeCredentialAuth.headers,
				},
				authSource: runtimeCredentialAuth.source,
				authError: null,
			};
		}
		const sdkAuth = resolveSdkAuthHeaders();
		if (sdkAuth.headers) {
			return {
				headers: {
					...headers,
					...sdkAuth.headers,
				},
				authSource: sdkAuth.source,
				authError: runtimeCredentialAuth.error,
			};
		}
		if (userUid) {
			headers["X-Mainsequence-User-Uid"] = userUid;
			return { headers, authSource: "debug_user_uid", authError: runtimeCredentialAuth.error };
		}
		return { headers, authSource: "none", authError: runtimeCredentialAuth.error };
	}
	const sdkAuth = resolveSdkAuthHeaders();
	if (sdkAuth.headers) {
		return {
			headers: {
				...headers,
				...sdkAuth.headers,
			},
			authSource: sdkAuth.source,
			authError: null,
		};
	}
	if (userUid) {
		headers["X-Mainsequence-User-Uid"] = userUid;
		return { headers, authSource: "debug_user_uid", authError: sdkAuth.error };
	}
	return { headers, authSource: "none", authError: sdkAuth.error };
}

function redactHeaders(headers) {
	const redacted = { ...headers };
	for (const key of Object.keys(redacted)) {
		if (key.toLowerCase() === "authorization") redacted[key] = "Bearer <redacted>";
	}
	return {
		...redacted,
	};
}

function extractA2AResponseJson(body) {
	const parts = body?.message?.parts;
	if (!Array.isArray(parts)) return null;
	for (const part of parts) {
		if (part && typeof part === "object" && part.data && typeof part.data === "object") {
			return part.data;
		}
		if (part && typeof part === "object" && typeof part.text === "string") {
			try {
				const parsed = JSON.parse(part.text);
				if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
			} catch {
				// This part is text, not JSON.
			}
		}
	}
	return null;
}

function print(label, value) {
	console.log(`\n=== ${label} ===`);
	console.log(JSON.stringify(value, null, 2));
}

async function main() {
	loadEnvFile();
	const { positional, flags } = parseArgs(process.argv.slice(2));
	if (flags.help) {
		console.log(usage());
		return;
	}

	const promptKey = positional[0] || "capabilities";
	const prompt = PROMPTS[promptKey];
	if (!prompt) throw new Error(`Unknown prompt "${promptKey}". Use: ${Object.keys(PROMPTS).join(", ")}`);

	const baseUrl = stringFlag(flags, "base-url", DEFAULT_BASE_URL);
	const agentSessionUid = requireValue(
		stringFlag(flags, "agent-session-uid", process.env.ASTRO_A2A_AGENT_SESSION_UID || ""),
		"Missing --agent-session-uid or ASTRO_A2A_AGENT_SESSION_UID.",
	);
	const bearerToken = stringFlag(
		flags,
		"bearer-token",
		process.env.ASTRO_A2A_BEARER_TOKEN || process.env.MAINSEQUENCE_ACCESS_TOKEN || "",
	);
	const userUid = stringFlag(flags, "user-uid", process.env.ASTRO_A2A_USER_UID || "");
	const url = buildUrl(baseUrl, "/api/a2a/v1/message:send");
	const { headers, authSource, authError } = await buildRequestHeaders({ bearerToken, userUid });
	if (authSource === "none") {
		throw new Error(
			[
				"Could not resolve auth for the A2A debug request.",
				authError ? `Auth error: ${authError}` : "Auth error: unavailable.",
				"Ensure MAINSEQUENCE_RUNTIME_CREDENTIAL_ID / MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET / MAINSEQUENCE_BACKEND are set in `.env`, or set ASTRO_A2A_BEARER_TOKEN / ASTRO_A2A_USER_UID explicitly.",
			].join(" "),
		);
	}
	const payload = {
		message: {
			messageId: `msg-debug-${Date.now()}`,
			role: "ROLE_USER",
			contextId: agentSessionUid,
			parts: [{ text: prompt }],
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
				jsonRepairAttempts: DEFAULT_REPAIR_ATTEMPTS,
			},
		},
	};

	print("request", {
		method: "POST",
		url,
		authSource,
		headers: redactHeaders(headers),
		body: payload,
	});

	const response = await fetch(url, {
		method: "POST",
		headers,
		body: JSON.stringify(payload),
	});
	const raw = await response.text();
	let body = null;
	try {
		body = raw ? JSON.parse(raw) : null;
	} catch {
		body = null;
	}
	const json = extractA2AResponseJson(body);

	print("response", {
		ok: response.ok,
		status: response.status,
		statusText: response.statusText,
		body,
		json,
		raw: body ? undefined : raw,
	});

	if (!response.ok) throw new Error(`Endpoint request failed with HTTP ${response.status}.`);
	if (!json || typeof json !== "object" || Array.isArray(json)) {
		throw new Error("Endpoint response did not contain a JSON dictionary/object message part.");
	}
}

main().catch((error) => {
	console.error(error instanceof Error ? error.message : String(error));
	process.exitCode = 1;
});
