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

export function buildA2ASystemInstruction(options: {
	callerAgentType: string;
	responseFormat: string | Record<string, unknown> | null;
	callerMetadata?: Record<string, unknown> | null;
}): string {
	const lines = [
		"This request arrived through Astro's A2A channel.",
		"Load and follow the injected `a2a_communication` skill.",
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

	return lines.join("\n");
}
