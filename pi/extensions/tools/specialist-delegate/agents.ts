import * as fs from "node:fs";
import * as path from "node:path";
import { getAgentDir, parseFrontmatter } from "@mariozechner/pi-coding-agent";

export type AgentScope = "user" | "project" | "both";

export interface AgentConfig {
	name: string;
	description: string;
	tools?: string[];
	model?: string;
	systemPrompt: string;
	outputFormat?: string;
	appendPromptFiles?: string[];
	source: "user" | "project";
	filePath: string;
}

export interface AgentDiscoveryResult {
	agents: AgentConfig[];
	projectAgentsDir: string | null;
}

function isDirectory(candidate: string): boolean {
	try {
		return fs.statSync(candidate).isDirectory();
	} catch {
		return false;
	}
}

function loadAppendedPromptFiles(filePath: string, appendPromptFiles: string | undefined): {
	paths: string[];
	content: string;
} {
	if (!appendPromptFiles?.trim()) {
		return { paths: [], content: "" };
	}

	const baseDir = path.dirname(filePath);
	const paths = appendPromptFiles
		.split(",")
		.map((value) => value.trim())
		.filter(Boolean)
		.map((relativePath) => path.resolve(baseDir, relativePath));

	const sections: string[] = [];

	for (const resolvedPath of paths) {
		try {
			const content = fs.readFileSync(resolvedPath, "utf8").trim();
			if (!content) continue;
			sections.push(`# Shared guidelines: ${path.basename(resolvedPath)}\n${content}`);
		} catch {
			// Ignore unreadable shared prompt files so the specialist can still load.
		}
	}

	return {
		paths,
		content: sections.join("\n\n"),
	};
}

function loadAgentsFromDir(directory: string, source: "user" | "project"): AgentConfig[] {
	if (!isDirectory(directory)) return [];

	const agents: AgentConfig[] = [];
	const entries = fs.readdirSync(directory, { withFileTypes: true });
	const tutorialEnabled = ["1", "true", "yes"].includes(
		(process.env.ADD_TUTORIAL_AGENT ?? "").toLowerCase(),
	);

	for (const entry of entries) {
		if (!entry.name.endsWith(".md")) continue;
		if (!entry.isFile() && !entry.isSymbolicLink()) continue;

		const filePath = path.join(directory, entry.name);
		let content = "";

		try {
			content = fs.readFileSync(filePath, "utf8");
		} catch {
			continue;
		}

		const { frontmatter, body } = parseFrontmatter<Record<string, string>>(content);
		const name = frontmatter.name?.trim();
		const description = frontmatter.description?.trim();

		if (!name || !description) continue;
		if (name === "rpro-builder" && !tutorialEnabled) continue;

		const tools = frontmatter.tools
			?.split(",")
			.map((value) => value.trim())
			.filter(Boolean);

		const outputFormat = frontmatter.outputFormat?.trim();
		const appendedPromptFiles = loadAppendedPromptFiles(filePath, frontmatter.appendPromptFiles?.trim());
		let systemPrompt = [body.trim(), appendedPromptFiles.content].filter(Boolean).join("\n\n");

		if (outputFormat) {
			systemPrompt = `${systemPrompt}

# Output format
${outputFormat}`;
		}

		agents.push({
			name,
			description,
			tools: tools?.length ? tools : undefined,
			model: frontmatter.model?.trim() || undefined,
			systemPrompt,
			outputFormat,
			appendPromptFiles: appendedPromptFiles.paths.length ? appendedPromptFiles.paths : undefined,
			source,
			filePath,
		});
	}

	return agents;
}

function findNearestProjectAgentsDir(startCwd: string): string | null {
	let current = path.resolve(startCwd);

	while (true) {
		const candidate = path.join(current, ".pi", "agents");
		if (isDirectory(candidate)) return candidate;

		const parent = path.dirname(current);
		if (parent === current) return null;
		current = parent;
	}
}

export function discoverAgents(cwd: string, scope: AgentScope): AgentDiscoveryResult {
	const userAgentsDir = path.join(getAgentDir(), "agents");
	const projectAgentsDir = findNearestProjectAgentsDir(cwd);

	const userAgents = scope === "project" ? [] : loadAgentsFromDir(userAgentsDir, "user");
	const projectAgents =
		scope === "user" || !projectAgentsDir ? [] : loadAgentsFromDir(projectAgentsDir, "project");

	const byName = new Map<string, AgentConfig>();

	if (scope === "user") {
		for (const agent of userAgents) byName.set(agent.name, agent);
	} else if (scope === "project") {
		for (const agent of projectAgents) byName.set(agent.name, agent);
	} else {
		for (const agent of userAgents) byName.set(agent.name, agent);
		for (const agent of projectAgents) byName.set(agent.name, agent);
	}

	return {
		agents: Array.from(byName.values()),
		projectAgentsDir,
	};
}
