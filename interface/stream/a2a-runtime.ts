export function resolveCurrentAgentType(env: NodeJS.ProcessEnv = process.env): string {
	const fixed = env.ASTRO_FIXED_AGENT_TYPE?.trim();
	if (fixed) return fixed;
	const activeSpecialist = env.ASTRO_ACTIVE_SPECIALIST?.trim();
	if (activeSpecialist) return activeSpecialist;
	return "astro-orchestrator";
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function normalizeA2AResponseFormat(
	value: unknown,
): string | Record<string, unknown> | null {
	if (typeof value === "string" && value.trim()) return value.trim();
	if (isPlainObject(value)) return value;
	return null;
}

function normalizeJsonMode(value: unknown): "none" | "json" | "json_object" | "json_schema" {
	if (typeof value !== "string") return "none";
	const normalized = value.trim().toLowerCase();
	if (normalized === "json" || normalized === "application/json") return "json";
	if (normalized === "json_object") return "json_object";
	if (normalized === "json_schema") return "json_schema";
	return "none";
}

function strictJsonModeForResponseFormat(
	responseFormat: string | Record<string, unknown> | null,
): "none" | "json" | "json_object" | "json_schema" {
	if (typeof responseFormat === "string") return normalizeJsonMode(responseFormat);
	if (!isPlainObject(responseFormat) || responseFormat.strict !== true) return "none";
	let jsonMode = normalizeJsonMode(responseFormat.type);
	if (jsonMode === "none") jsonMode = normalizeJsonMode(responseFormat.format);
	return jsonMode;
}

export function buildA2ASystemInstruction(options: {
	callerAgentType: string;
	responseFormat: string | Record<string, unknown> | null;
	callerMetadata?: Record<string, unknown> | null;
}): string {
	const lines = [
		"This request arrived through Astro's A2A channel.",
		"Follow any A2A instructions supplied by the active Pi packages or runtime context.",
		`Caller agentType: ${options.callerAgentType}`,
	];

	if (options.callerMetadata && Object.keys(options.callerMetadata).length > 0) {
		lines.push(`Caller metadata: ${JSON.stringify(options.callerMetadata)}`);
	}

	if (typeof options.responseFormat === "string" && options.responseFormat.trim()) {
		lines.push(`Required response format: ${options.responseFormat.trim()}`);
	} else if (options.responseFormat && typeof options.responseFormat === "object") {
		lines.push(`Required response format: ${JSON.stringify(options.responseFormat)}`);
	} else {
		lines.push("Required response format: none specified.");
	}

	const strictJsonMode = strictJsonModeForResponseFormat(options.responseFormat);
	if (strictJsonMode !== "none") {
		lines.push("Strict JSON response required.");
		lines.push("Return exactly one valid JSON value and nothing else.");
		lines.push("Do not include prose, markdown fences, explanations, or comments.");
		if (strictJsonMode === "json_object" || strictJsonMode === "json_schema") {
			lines.push("The top-level JSON value must be an object.");
		}
	}

	return lines.join("\n");
}
