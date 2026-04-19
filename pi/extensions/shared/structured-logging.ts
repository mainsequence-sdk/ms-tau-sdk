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
	data?: Record<string, unknown>;
};

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

export function logStructuredEvent(input: StructuredLogInput) {
	const payload: StructuredLogPayload = {
		severity: input.severity ?? "INFO",
		time: new Date().toISOString(),
		component: input.component,
		event: input.event,
		message: input.message,
		...(input.data ? { data: removeUndefinedDeep(input.data) as Record<string, unknown> } : {}),
	};

	try {
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
