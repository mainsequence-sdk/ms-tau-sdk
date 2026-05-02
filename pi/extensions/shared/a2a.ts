import * as fs from "node:fs";
import * as path from "node:path";

export const A2A_DEV_PROJECT_ENV = "A2A_DEV_PROJECT";
export const A2A_DEV_PROJECT_MOUNTED_PATH_ENV = "A2A_DEV_PROJECT_MOUNTED_PATH";
export const A2A_DEV_BASE_URL_ENV = "A2A_DEV_BASE_URL";
export const A2A_DEV_EXECUTOR_SERVICE_URL = "http://astro-project-executor:8787";
export const A2A_DEV_EXECUTOR_PORT_ENV = "ASTRO_EXECUTOR_STREAM_PORT";
export const A2A_DEFAULT_DEV_EXECUTOR_PORT = "8790";

export type A2ADiscoveryCandidate = {
	agent_id: string | number;
	agent_description: string;
	a2a_card: Record<string, unknown>;
};

export function resolveCurrentAstroAgentName(env: NodeJS.ProcessEnv = process.env): string {
	const fixed = env.ASTRO_FIXED_AGENT_NAME?.trim();
	if (fixed) return fixed;
	const activeSpecialist = env.ASTRO_ACTIVE_SPECIALIST?.trim();
	if (activeSpecialist) return activeSpecialist;
	return "astro-orchestrator";
}

export function isOrchestratorAgentName(agentName: string | null | undefined): boolean {
	return !agentName || agentName === "astro-orchestrator";
}

export function buildA2ADiscoveryPrompt(options: {
	request: string;
	responseFormat?: string | Record<string, unknown> | null;
	agentHint?: string | null;
}): string {
	const lines = [`Intent: ${options.request.trim()}`];
	if (options.agentHint?.trim()) {
		lines.push(`Preferred agent hint: ${options.agentHint.trim()}`);
	}
	if (typeof options.responseFormat === "string" && options.responseFormat.trim()) {
		lines.push(`Required response format: ${options.responseFormat.trim()}`);
	} else if (options.responseFormat && typeof options.responseFormat === "object") {
		lines.push(`Required response format: ${JSON.stringify(options.responseFormat)}`);
	}
	return lines.join("\n");
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function extractStringProperty(record: Record<string, unknown>, keys: string[]): string | null {
	for (const key of keys) {
		const value = record[key];
		if (typeof value === "string" && value.trim()) return value.trim();
	}
	return null;
}

function extractStringArrayProperty(record: Record<string, unknown>, keys: string[]): string[] {
	for (const key of keys) {
		const value = record[key];
		if (!Array.isArray(value)) continue;
		const strings = value
			.map((entry) => (typeof entry === "string" ? entry.trim() : ""))
			.filter(Boolean);
		if (strings.length > 0) return strings;
	}
	return [];
}

export function resolveA2ADevProject(env: NodeJS.ProcessEnv = process.env): string | null {
	const raw = env[A2A_DEV_PROJECT_ENV]?.trim();
	if (!raw) return null;
	const mounted = env[A2A_DEV_PROJECT_MOUNTED_PATH_ENV]?.trim();
	return path.resolve(mounted || raw);
}

export function resolveA2ADevBaseUrls(env: NodeJS.ProcessEnv = process.env): string[] {
	const explicit = env[A2A_DEV_BASE_URL_ENV]?.trim();
	const hostPort = env[A2A_DEV_EXECUTOR_PORT_ENV]?.trim() || A2A_DEFAULT_DEV_EXECUTOR_PORT;
	const urls = [
		explicit || null,
		A2A_DEV_EXECUTOR_SERVICE_URL,
		`http://127.0.0.1:${hostPort}`,
	].filter((value, index, all): value is string => Boolean(value) && all.indexOf(value) === index);
	return urls;
}

export function readA2ADevCandidate(env: NodeJS.ProcessEnv = process.env): {
	projectPath: string;
	cardPath: string;
	candidate: A2ADiscoveryCandidate;
} {
	const projectPath = resolveA2ADevProject(env);
	if (!projectPath) {
		throw new Error(`${A2A_DEV_PROJECT_ENV} is not set.`);
	}

	const cardPath = path.join(projectPath, ".agents", "agent_card.json");
	if (!fs.existsSync(cardPath)) {
		throw new Error(`Missing local A2A agent card: ${cardPath}`);
	}

	let parsed: unknown;
	try {
		parsed = JSON.parse(fs.readFileSync(cardPath, "utf8"));
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		throw new Error(`Could not parse local A2A agent card at ${cardPath}: ${message}`);
	}

	if (!isPlainObject(parsed)) {
		throw new Error(`Local A2A agent card must be a JSON object: ${cardPath}`);
	}

	const card = parsed;
	const projectName = path.basename(projectPath);
	const descriptionParts: string[] = [];
	const cardName = extractStringProperty(card, ["name", "agent_name", "agentName", "title"]);
	const cardDescription = extractStringProperty(card, ["description", "summary"]);
	const capabilities = extractStringArrayProperty(card, ["capabilities", "skills", "tags"]);

	if (cardName) descriptionParts.push(`Name: ${cardName}`);
	if (cardDescription) descriptionParts.push(`Description: ${cardDescription}`);
	if (capabilities.length > 0) descriptionParts.push(`Capabilities: ${capabilities.join(", ")}`);
	descriptionParts.push(`Project: ${projectName}`);

	const candidate: A2ADiscoveryCandidate = {
		agent_id:
			extractStringProperty(card, ["agent_id", "agentId", "name", "agent_name", "agentName"]) ||
			`local-dev:${projectName}`,
		agent_description: descriptionParts.join(" | "),
		a2a_card: card,
	};

	return {
		projectPath,
		cardPath,
		candidate,
	};
}

function tokenize(value: string): string[] {
	return value
		.toLowerCase()
		.split(/[^a-z0-9]+/)
		.map((token) => token.trim())
		.filter((token) => token.length >= 3);
}

export function selectBestA2ACandidate(
	candidates: A2ADiscoveryCandidate[],
	discoveryPrompt: string,
): { candidate: A2ADiscoveryCandidate; score: number } | null {
	if (candidates.length === 0) return null;

	const intentTokens = new Set(tokenize(discoveryPrompt));
	let best: { candidate: A2ADiscoveryCandidate; score: number } | null = null;

	for (const candidate of candidates) {
		const candidateText = `${candidate.agent_description}\n${JSON.stringify(candidate.a2a_card)}`;
		const score = tokenize(candidateText).reduce(
			(total, token) => total + (intentTokens.has(token) ? 1 : 0),
			0,
		);

		if (!best || score > best.score) {
			best = { candidate, score };
		}
	}

	return best ?? { candidate: candidates[0], score: 0 };
}

export function normalizeA2AResponseFormat(
	value: unknown,
): string | Record<string, unknown> | null {
	if (typeof value === "string" && value.trim()) return value.trim();
	if (isPlainObject(value)) return value;
	return null;
}

export function buildA2ASystemInstruction(options: {
	callerAgentName: string;
	responseFormat: string | Record<string, unknown> | null;
	callerMetadata?: Record<string, unknown> | null;
}): string {
	const lines = [
		"This request arrived through Astro's A2A channel.",
		"Treat it as agent-to-agent communication, not end-user chat.",
		"Do not answer as if you are speaking directly to a human user.",
		"Follow the requested response format as a hard contract.",
		`Caller agent: ${options.callerAgentName}`,
	];

	if (options.callerMetadata && Object.keys(options.callerMetadata).length > 0) {
		lines.push(`Caller metadata: ${JSON.stringify(options.callerMetadata)}`);
	}

	if (typeof options.responseFormat === "string" && options.responseFormat.trim()) {
		lines.push(`Required response format: ${options.responseFormat.trim()}`);
	} else if (options.responseFormat && typeof options.responseFormat === "object") {
		lines.push(`Required response format: ${JSON.stringify(options.responseFormat)}`);
	} else {
		lines.push("Required response format: return a concise machine-facing response.");
	}

	return lines.join("\n");
}
