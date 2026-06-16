export type A2ARuntimeOptions = {
	turnTimeoutMs: number;
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizePositiveMilliseconds(value: unknown): number | null {
	if (value == null) return null;
	if (typeof value === "number" && Number.isFinite(value)) {
		if (value <= 0) return 0;
		return Math.trunc(value);
	}
	if (typeof value === "string" && value.trim()) {
		const parsed = Number(value.trim());
		if (!Number.isFinite(parsed)) return null;
		if (parsed <= 0) return 0;
		return Math.trunc(parsed);
	}
	return null;
}

function normalizePositiveSeconds(value: unknown): number | null {
	if (value == null) return null;
	if (typeof value === "number" && Number.isFinite(value)) {
		if (value <= 0) return 0;
		return Math.trunc(value * 1000);
	}
	if (typeof value === "string" && value.trim()) {
		const parsed = Number(value.trim());
		if (!Number.isFinite(parsed)) return null;
		if (parsed <= 0) return 0;
		return Math.trunc(parsed * 1000);
	}
	return null;
}

function firstNormalized(...values: Array<unknown>): number | null {
	for (const value of values) {
		if (value == null) continue;
		const normalized = normalizePositiveMilliseconds(value);
		if (normalized != null) return normalized;
	}
	return null;
}

function firstNormalizedSeconds(...values: Array<unknown>): number | null {
	for (const value of values) {
		if (value == null) continue;
		const normalized = normalizePositiveSeconds(value);
		if (normalized != null) return normalized;
	}
	return null;
}

export function normalizeA2ARuntimeOptions(input: {
	body?: Record<string, unknown> | null;
	a2aContext?: Record<string, unknown> | null;
	context?: Record<string, unknown> | null;
}): A2ARuntimeOptions {
	const body = isPlainObject(input.body) ? input.body : {};
	const a2aContext = isPlainObject(input.a2aContext) ? input.a2aContext : {};
	const context = isPlainObject(input.context) ? input.context : {};
	const turnTimeoutMs =
		firstNormalized(
			body.runtime_turn_timeout_ms,
			body.runtimeTurnTimeoutMs,
			body.a2a_turn_timeout_ms,
			body.a2aTurnTimeoutMs,
			a2aContext.runtime_turn_timeout_ms,
			a2aContext.runtimeTurnTimeoutMs,
			a2aContext.a2a_turn_timeout_ms,
			a2aContext.a2aTurnTimeoutMs,
			context.runtime_turn_timeout_ms,
			context.runtimeTurnTimeoutMs,
			context.a2a_turn_timeout_ms,
			context.a2aTurnTimeoutMs,
		) ??
		firstNormalizedSeconds(
			body.runtime_turn_timeout_seconds,
			body.runtimeTurnTimeoutSeconds,
			body.a2a_turn_timeout_seconds,
			body.a2aTurnTimeoutSeconds,
			a2aContext.runtime_turn_timeout_seconds,
			a2aContext.runtimeTurnTimeoutSeconds,
			a2aContext.a2a_turn_timeout_seconds,
			a2aContext.a2aTurnTimeoutSeconds,
			context.runtime_turn_timeout_seconds,
			context.runtimeTurnTimeoutSeconds,
			context.a2a_turn_timeout_seconds,
			context.a2aTurnTimeoutSeconds,
			) ??
			0;
	return {
		turnTimeoutMs,
	};
}
