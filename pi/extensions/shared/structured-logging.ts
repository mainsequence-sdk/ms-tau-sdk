export type StructuredLogSeverity = "DEBUG" | "INFO" | "WARNING" | "ERROR";

export type StructuredLogInput = {
	severity?: StructuredLogSeverity;
	component: string;
	event: string;
	message: string;
	data?: Record<string, unknown>;
};

type StructuredLogPayload = {
	severity: StructuredLogSeverity;
	time: string;
	component: string;
	event: string;
	message: string;
	session_id?: string;
	data?: Record<string, unknown>;
};

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

export function shouldEmitStructuredLog(
	input: Pick<StructuredLogInput, "severity" | "component" | "event">,
	env: NodeJS.ProcessEnv = process.env,
): boolean {
	const severity = input.severity ?? "INFO";
	if (severity !== "INFO") return true;
	if (isTruthyEnvValue(env.ASTRO_LOG_CHECKPOINT_INFO)) return true;
	if (input.component === "astro-checkpoint-sidecar") return false;
	return !input.event.includes("checkpoint");
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

function normalizeStructuredLogSessionIdValue(value: unknown): string | null {
	if (typeof value === "string" && value.trim()) return value.trim();
	if (typeof value === "number" && Number.isFinite(value)) return String(Math.trunc(value));
	return null;
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
): Record<string, unknown> | undefined {
	if (!record) return undefined;
	const cleaned = removeUndefinedDeep(record);
	if (!cleaned || typeof cleaned !== "object" || Array.isArray(cleaned)) return undefined;
	const normalized = cleaned as Record<string, unknown>;
	const sessionId = resolveStructuredLogSessionId(normalized);
	if (!sessionId) return normalized;
	if (normalized.session_id === sessionId) return normalized;
	return {
		...normalized,
		session_id: sessionId,
	};
}

export function logStructuredEvent(input: StructuredLogInput) {
	const normalizedData = normalizeStructuredLogRecord(input.data);
	const sessionId = resolveStructuredLogSessionId(normalizedData);
	const payload: StructuredLogPayload = {
		severity: input.severity ?? "INFO",
		time: new Date().toISOString(),
		component: input.component,
		event: input.event,
		message: input.message,
		...(sessionId ? { session_id: sessionId } : {}),
		...(normalizedData ? { data: normalizedData } : {}),
	};

	try {
		if (!shouldEmitStructuredLog(payload)) return;
		const serialized = JSON.stringify(payload);
		if (payload.severity === "ERROR" || payload.severity === "WARNING") {
			console.error(serialized);
			return;
		}
		console.log(serialized);
	} catch {
		// Do not let structured logging break the main runtime path.
	}
}
