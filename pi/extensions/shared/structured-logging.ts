import { createHash } from "node:crypto";

export type StructuredLogSeverity = "DEBUG" | "INFO" | "WARNING" | "ERROR";

export type StructuredLogInput = {
	severity?: StructuredLogSeverity;
	component: string;
	event: string;
	message: string;
	data?: Record<string, unknown>;
};

export type StructuredLogPayload = {
	severity: StructuredLogSeverity;
	time: string;
	component: string;
	event: string;
	message: string;
	request_id?: string;
	trace_id?: string;
	session_id?: string;
	agent_session_uid?: string;
	agent_type?: string;
	route?: string;
	method?: string;
	status_code?: number;
	duration_ms?: number;
	phase?: string;
	outcome?: string;
	error_code?: string;
	error_type?: string;
	error_message?: string;
	stack_hash?: string;
	data?: Record<string, unknown>;
};

type LogSinkMode = "off" | "json" | "pretty";

const STRUCTURED_LOG_SESSION_ID_KEYS = [
	"session_id",
	"sessionId",
	"sessionUid",
	"session_uid",
	"sessionKey",
	"runtime_session_uid",
	"runtimeSessionUid",
	"agent_session_uid",
	"agentSessionUid",
] as const;

const PROMOTED_FIELD_KEYS = {
	request_id: ["request_id", "requestId"],
	trace_id: ["trace_id", "traceId"],
	agent_session_uid: ["agent_session_uid", "agentSessionUid", "agentSessionId", "agent_session_id"],
	agent_type: ["agent_type", "agentType"],
	route: ["route"],
	method: ["method"],
	status_code: ["status_code", "statusCode"],
	duration_ms: ["duration_ms", "durationMs"],
	phase: ["phase"],
	outcome: ["outcome"],
	error_code: ["error_code", "errorCode", "code"],
	error_type: ["error_type", "errorType"],
	error_message: ["error_message", "errorMessage", "error"],
	stack_hash: ["stack_hash", "stackHash"],
} as const;

const HUMAN_FIELD_KEYS = [
	["req", ["request_id"]],
	["session", ["session_id"]],
	["agent_session", ["agent_session_uid"]],
	["agent", ["agent_type"]],
	["method", ["method"]],
	["route", ["route"]],
	["status", ["status_code"]],
	["phase", ["phase"]],
	["outcome", ["outcome"]],
	["error", ["error_code"]],
	["type", ["error_type"]],
	["chunk", ["chunk_type", "chunkType"]],
	["tool", ["tool_name", "toolName"]],
	["bytes", ["bytes"]],
	["state", ["state", "runner_state"]],
	["runner", ["runner", "runnerState", "runner_state"]],
	["cache", ["cacheReason", "cache_reason"]],
] as const;

const LOG_LEVEL_WEIGHT: Record<StructuredLogSeverity, number> = {
	DEBUG: 10,
	INFO: 20,
	WARNING: 30,
	ERROR: 40,
};

function isTruthyEnvValue(value: string | undefined): boolean {
	if (!value) return false;
	switch (value.trim().toLowerCase()) {
		case "1":
		case "true":
		case "yes":
		case "on":
		case "debug":
			return true;
		default:
			return false;
	}
}

function normalizeSeverity(value: unknown): StructuredLogSeverity {
	if (typeof value !== "string") return "INFO";
	const upper = value.trim().toUpperCase();
	if (upper === "DEBUG" || upper === "INFO" || upper === "WARNING" || upper === "ERROR") return upper;
	if (upper === "WARN") return "WARNING";
	return "INFO";
}

function normalizeLogLevel(env: NodeJS.ProcessEnv = process.env): StructuredLogSeverity {
	return normalizeSeverity(env.ASTRO_LOG_LEVEL ?? "INFO");
}

function shouldUseGkeDefaults(env: NodeJS.ProcessEnv): boolean {
	return Boolean(env.K_SERVICE || env.K_REVISION || env.K_CONFIGURATION || env.KUBERNETES_SERVICE_HOST);
}

function resolveMachineSink(env: NodeJS.ProcessEnv = process.env): LogSinkMode {
	const configured = (env.ASTRO_LOG_MACHINE_SINK ?? "").trim().toLowerCase();
	if (configured === "off" || configured === "0" || configured === "false") return "off";
	if (configured === "json" || configured === "stdout" || configured === "stderr") return "json";

	const legacyFormat = (env.ASTRO_LOG_FORMAT ?? "").trim().toLowerCase();
	if (legacyFormat === "json") return "json";
	if (legacyFormat === "pretty") return "off";

	return shouldUseGkeDefaults(env) ? "json" : "off";
}

function resolveHumanSink(env: NodeJS.ProcessEnv = process.env): LogSinkMode {
	const configured = (env.ASTRO_LOG_HUMAN_SINK ?? "").trim().toLowerCase();
	if (configured === "off" || configured === "0" || configured === "false") return "off";
	if (configured === "pretty" || configured === "human" || configured === "stdout" || configured === "stderr") {
		return "pretty";
	}

	const legacyFormat = (env.ASTRO_LOG_FORMAT ?? "").trim().toLowerCase();
	if (legacyFormat === "pretty") return "pretty";
	if (legacyFormat === "json") return "off";

	return shouldUseGkeDefaults(env) ? "off" : "pretty";
}

function normalizeInteger(value: string | undefined, fallback: number): number {
	const parsed = Number(value);
	return Number.isFinite(parsed) && parsed >= 0 ? Math.trunc(parsed) : fallback;
}

function removeUndefinedDeep(value: unknown): unknown {
	if (Array.isArray(value)) {
		return value.map((entry) => removeUndefinedDeep(entry));
	}
	if (value && typeof value === "object") {
		const cleanedEntries = Object.entries(value as Record<string, unknown>)
			.filter(([, entry]) => entry !== undefined)
			.map(([key, entry]) => [key, removeUndefinedDeep(entry)] as const);
		return Object.fromEntries(cleanedEntries);
	}
	return value;
}

function hashString(value: string): string {
	return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function normalizeErrorValue(value: Error, env: NodeJS.ProcessEnv): Record<string, unknown> {
	const stackMode = (env.ASTRO_LOG_STACK_MODE ?? "summary").trim().toLowerCase();
	const stack = value.stack ?? "";
	return {
		name: value.name,
		message: value.message,
		...(stack
			? stackMode === "full"
				? { stack }
				: { stack_hash: hashString(stack) }
			: {}),
	};
}

function sanitizeLogValue(
	value: unknown,
	env: NodeJS.ProcessEnv,
	depth = 0,
): unknown {
	if (value instanceof Error) return normalizeErrorValue(value, env);
	if (value === undefined) return undefined;
	if (value === null) return null;
	if (typeof value === "bigint") return value.toString();
	if (typeof value === "number" || typeof value === "boolean") return value;

	const payloadsEnabled = isTruthyEnvValue(env.ASTRO_LOG_PAYLOADS);
	const maxStringBytes = normalizeInteger(env.ASTRO_LOG_MAX_FIELD_BYTES, 2048);
	const maxArrayItems = normalizeInteger(env.ASTRO_LOG_MAX_ARRAY_ITEMS, 20);
	const maxDepth = normalizeInteger(env.ASTRO_LOG_MAX_DEPTH, 6);

	if (typeof value === "string") {
		const bytes = Buffer.byteLength(value, "utf8");
		if (payloadsEnabled || bytes <= maxStringBytes) return value;
		const preview = value.slice(0, maxStringBytes).replace(/\s+/g, " ").trimEnd();
		return {
			truncated: true,
			bytes,
			preview,
			hash: hashString(value),
		};
	}

	if (Array.isArray(value)) {
		if (depth >= maxDepth) {
			return { truncated: true, items: value.length, reason: "max_depth" };
		}
		const entries = payloadsEnabled ? value : value.slice(0, maxArrayItems);
		const sanitized = entries.map((entry) => sanitizeLogValue(entry, env, depth + 1));
		if (payloadsEnabled || value.length <= maxArrayItems) return sanitized;
		return {
			truncated: true,
			items: value.length,
			preview: sanitized,
		};
	}

	if (typeof value === "object") {
		if (depth >= maxDepth) {
			return { truncated: true, reason: "max_depth" };
		}
		const output: Record<string, unknown> = {};
		for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
			const sanitized = sanitizeLogValue(entry, env, depth + 1);
			if (sanitized !== undefined) output[key] = sanitized;
		}
		return output;
	}

	return String(value);
}

function normalizeStructuredLogSessionIdValue(value: unknown): string | null {
	if (typeof value === "string" && value.trim()) return value.trim();
	if (typeof value === "number" && Number.isFinite(value)) return String(Math.trunc(value));
	return null;
}

function getRecordValue(record: Record<string, unknown> | undefined | null, keys: readonly string[]): unknown {
	if (!record) return undefined;
	for (const key of keys) {
		const value = record[key];
		if (value !== undefined && value !== null && value !== "") return value;
	}
	return undefined;
}

export function resolveStructuredLogSessionId(
	record: Record<string, unknown> | undefined | null,
): string | null {
	if (!record) return null;
	for (const key of STRUCTURED_LOG_SESSION_ID_KEYS) {
		const resolved = normalizeStructuredLogSessionIdValue(record[key]);
		if (resolved) return resolved;
	}
	return null;
}

export function normalizeStructuredLogRecord(
	record: Record<string, unknown> | undefined,
	env: NodeJS.ProcessEnv = process.env,
): Record<string, unknown> | undefined {
	if (!record) return undefined;
	const cleaned = removeUndefinedDeep(record);
	if (!cleaned || typeof cleaned !== "object" || Array.isArray(cleaned)) return undefined;
	const sanitized = sanitizeLogValue(cleaned, env);
	if (!sanitized || typeof sanitized !== "object" || Array.isArray(sanitized)) return undefined;
	const normalized = sanitized as Record<string, unknown>;
	const sessionId = resolveStructuredLogSessionId(normalized);
	if (!sessionId) return normalized;
	if (normalized.session_id === sessionId) return normalized;
	return {
		...normalized,
		session_id: sessionId,
	};
}

function promoteStringField(
	output: StructuredLogPayload,
	data: Record<string, unknown> | undefined,
	outputKey: keyof StructuredLogPayload,
	keys: readonly string[],
) {
	const value = getRecordValue(data, keys);
	if (typeof value === "string" && value.trim()) {
		(output as Record<string, unknown>)[outputKey] = value.trim();
		return;
	}
	if (typeof value === "number" && Number.isFinite(value)) {
		(output as Record<string, unknown>)[outputKey] = String(value);
	}
}

function promoteNumberField(
	output: StructuredLogPayload,
	data: Record<string, unknown> | undefined,
	outputKey: keyof StructuredLogPayload,
	keys: readonly string[],
) {
	const value = getRecordValue(data, keys);
	if (typeof value === "number" && Number.isFinite(value)) {
		(output as Record<string, unknown>)[outputKey] = value;
		return;
	}
	if (typeof value === "string" && value.trim()) {
		const parsed = Number(value);
		if (Number.isFinite(parsed)) {
			(output as Record<string, unknown>)[outputKey] = parsed;
		}
	}
}

export function buildStructuredLogPayload(
	input: StructuredLogInput,
	env: NodeJS.ProcessEnv = process.env,
	now = new Date(),
): StructuredLogPayload {
	const normalizedData = normalizeStructuredLogRecord(input.data, env);
	const sessionId = resolveStructuredLogSessionId(normalizedData);
	const payload: StructuredLogPayload = {
		severity: input.severity ?? "INFO",
		time: now.toISOString(),
		component: input.component,
		event: input.event,
		message: input.message,
		...(sessionId ? { session_id: sessionId } : {}),
		...(normalizedData ? { data: normalizedData } : {}),
	};

	promoteStringField(payload, normalizedData, "request_id", PROMOTED_FIELD_KEYS.request_id);
	promoteStringField(payload, normalizedData, "trace_id", PROMOTED_FIELD_KEYS.trace_id);
	promoteStringField(payload, normalizedData, "agent_session_uid", PROMOTED_FIELD_KEYS.agent_session_uid);
	promoteStringField(payload, normalizedData, "agent_type", PROMOTED_FIELD_KEYS.agent_type);
	promoteStringField(payload, normalizedData, "route", PROMOTED_FIELD_KEYS.route);
	promoteStringField(payload, normalizedData, "method", PROMOTED_FIELD_KEYS.method);
	promoteStringField(payload, normalizedData, "phase", PROMOTED_FIELD_KEYS.phase);
	promoteStringField(payload, normalizedData, "outcome", PROMOTED_FIELD_KEYS.outcome);
	promoteStringField(payload, normalizedData, "error_code", PROMOTED_FIELD_KEYS.error_code);
	promoteStringField(payload, normalizedData, "error_type", PROMOTED_FIELD_KEYS.error_type);
	promoteStringField(payload, normalizedData, "error_message", PROMOTED_FIELD_KEYS.error_message);
	promoteStringField(payload, normalizedData, "stack_hash", PROMOTED_FIELD_KEYS.stack_hash);
	promoteNumberField(payload, normalizedData, "status_code", PROMOTED_FIELD_KEYS.status_code);
	promoteNumberField(payload, normalizedData, "duration_ms", PROMOTED_FIELD_KEYS.duration_ms);

	return payload;
}

export function shouldEmitStructuredLog(
	input: Pick<StructuredLogInput, "severity" | "component" | "event">,
	env: NodeJS.ProcessEnv = process.env,
): boolean {
	const severity = input.severity ?? "INFO";
	const configuredLevel = normalizeLogLevel(env);
	if (LOG_LEVEL_WEIGHT[severity] < LOG_LEVEL_WEIGHT[configuredLevel]) return false;
	if (severity !== "INFO") return true;
	if (isTruthyEnvValue(env.ASTRO_LOG_CHECKPOINT_INFO)) return true;
	if (input.component === "astro-checkpoint-sidecar") return false;
	return !input.event.includes("checkpoint");
}

function formatMachinePayload(payload: StructuredLogPayload): Record<string, unknown> {
	const { data, ...topLevel } = payload;
	return {
		...topLevel,
		...(data ? { data } : {}),
	};
}

export function renderMachineLogLine(payload: StructuredLogPayload): string {
	return JSON.stringify(formatMachinePayload(payload));
}

function shortId(value: unknown): string | null {
	if (typeof value !== "string" || !value.trim()) return null;
	const trimmed = value.trim();
	if (trimmed.length <= 14) return trimmed;
	if (/^[0-9a-f-]{32,}$/i.test(trimmed)) return `${trimmed.slice(0, 6)}...${trimmed.slice(-4)}`;
	return trimmed.length <= 32 ? trimmed : `${trimmed.slice(0, 24)}...`;
}

function compactHumanValue(value: unknown): string | null {
	if (value === undefined || value === null || value === "") return null;
	if (typeof value === "number" || typeof value === "boolean") return String(value);
	if (typeof value === "string") {
		const shortened = shortId(value) ?? value;
		return /\s/.test(shortened) ? JSON.stringify(shortened) : shortened;
	}
	if (typeof value === "object") {
		const record = value as Record<string, unknown>;
		if (record.truncated === true) {
			const bytes = compactHumanValue(record.bytes);
			const hash = compactHumanValue(record.hash);
			return bytes ? `truncated:${bytes}b${hash ? `:${hash}` : ""}` : "truncated";
		}
		if (typeof record.preview === "string") {
			return JSON.stringify(record.preview.length > 48 ? `${record.preview.slice(0, 45)}...` : record.preview);
		}
	}
	const stringified = JSON.stringify(value);
	return stringified.length > 64 ? `${stringified.slice(0, 61)}...` : stringified;
}

function findHumanFieldValue(payload: StructuredLogPayload, keys: readonly string[]): unknown {
	const payloadRecord = payload as Record<string, unknown>;
	for (const key of keys) {
		const direct = payloadRecord[key];
		if (direct !== undefined && direct !== null && direct !== "") return direct;
	}
	for (const key of keys) {
		const value = payload.data?.[key];
		if (value !== undefined && value !== null && value !== "") return value;
	}
	return undefined;
}

export function renderHumanLogLine(payload: StructuredLogPayload): string {
	const time = new Date(payload.time);
	const timeText = Number.isFinite(time.getTime())
		? time.toISOString().slice(11, 23)
		: payload.time;
	const severity = payload.severity.padEnd(5);
	const fields: string[] = [];
	for (const [label, keys] of HUMAN_FIELD_KEYS) {
		const rendered = compactHumanValue(findHumanFieldValue(payload, keys));
		if (rendered) fields.push(`${label}=${rendered}`);
	}
	if (typeof payload.duration_ms === "number" && !fields.some((field) => field.endsWith("ms"))) {
		fields.push(`${Math.round(payload.duration_ms)}ms`);
	}
	if (payload.error_message && !fields.some((field) => field.startsWith("message="))) {
		const rendered = compactHumanValue(payload.error_message);
		if (rendered) fields.push(`message=${rendered}`);
	}
	if (fields.length === 0 && payload.message) {
		const rendered = compactHumanValue(payload.message);
		if (rendered) fields.push(rendered);
	}
	return `${timeText} ${severity} ${payload.event}${fields.length ? ` ${fields.join(" ")}` : ""}`;
}

function writeLine(target: "stdout" | "stderr", line: string) {
	const stream = target === "stderr" ? process.stderr : process.stdout;
	stream.write(`${line}\n`);
}

export function logStructuredEvent(input: StructuredLogInput) {
	try {
		if (!shouldEmitStructuredLog(input)) return;
		const payload = buildStructuredLogPayload(input);
		const machineSink = resolveMachineSink();
		const humanSink = resolveHumanSink();

		if (machineSink === "json") {
			writeLine("stdout", renderMachineLogLine(payload));
		}
		if (humanSink === "pretty") {
			writeLine(payload.severity === "ERROR" || payload.severity === "WARNING" ? "stderr" : "stdout", renderHumanLogLine(payload));
		}
	} catch {
		// Do not let structured logging break the main runtime path.
	}
}
