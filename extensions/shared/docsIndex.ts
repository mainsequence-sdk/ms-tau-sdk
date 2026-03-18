import * as fs from "node:fs";
import * as path from "node:path";

export interface MarkdownItem {
	path: string;
	kind: string;
	title: string;
	description: string;
}

export interface SpecialistItem {
	name: string;
	description: string;
	tools: string;
	model: string;
	path: string;
}

export interface ExtensionItem {
	name: string;
	path: string;
	tools: string[];
}

export interface ExternalPackageItem {
	name: string;
	ref: string;
	path: string;
	description: string;
	version: string;
	installed: boolean;
	tools: string[];
}

export interface RefreshDocsIndexResult {
	repoRoot: string;
	knowledgeDir: string;
	generatedFiles: string[];
}

const TOOL_NAME_PATTERN = /name:\s*"([a-z0-9_-]+)"/g;
const IMAGE_ONLY_PATTERN = /^!\[[^\]]*\]\([^)]+\)$/;

function readText(filePath: string): string {
	return fs.readFileSync(filePath, "utf8");
}

function writeText(filePath: string, text: string) {
	fs.writeFileSync(filePath, text, "utf8");
}

function exists(filePath: string): boolean {
	return fs.existsSync(filePath);
}

function toPosixRelative(root: string, filePath: string): string {
	return path.relative(root, filePath).split(path.sep).join("/");
}

function titleCaseSlug(value: string): string {
	return value
		.split(/[^A-Za-z0-9]+/)
		.filter(Boolean)
		.map((part) => part[0].toUpperCase() + part.slice(1).toLowerCase())
		.join(" ");
}

function collapseWhitespace(text: string): string {
	return text.split(/\s+/).filter(Boolean).join(" ");
}

function projectDisplayName(repoRoot: string): string {
	const readmePath = path.join(repoRoot, "README.md");
	if (exists(readmePath)) {
		const heading = firstHeading(readText(readmePath));
		if (heading) return heading;
	}

	const packagePath = path.join(repoRoot, "package.json");
	if (exists(packagePath)) {
		try {
			const packageName = JSON.parse(readText(packagePath)).name;
			if (typeof packageName === "string" && packageName.trim()) {
				return titleCaseSlug(packageName);
			}
		} catch {
			// ignore invalid package json
		}
	}

	return titleCaseSlug(path.basename(repoRoot));
}

function parseFrontmatter(text: string): { data: Record<string, string>; body: string } {
	if (!text.startsWith("---\n")) return { data: {}, body: text };

	const markerIndex = text.indexOf("\n---\n", 4);
	if (markerIndex === -1) return { data: {}, body: text };

	const rawFrontmatter = text.slice(4, markerIndex);
	const body = text.slice(markerIndex + 5);
	const data: Record<string, string> = {};

	for (const line of rawFrontmatter.split("\n")) {
		const separator = line.indexOf(":");
		if (separator === -1) continue;
		const key = line.slice(0, separator).trim();
		const value = line.slice(separator + 1).trim();
		if (key) data[key] = value;
	}

	return { data, body };
}

function firstHeading(body: string): string | null {
	for (const line of body.split("\n")) {
		const trimmed = line.trim();
		if (trimmed.startsWith("#")) return trimmed.replace(/^#+\s*/, "");
	}
	return null;
}

function firstParagraph(body: string): string | null {
	for (const chunk of body.split("\n\n")) {
		const trimmed = chunk.trim();
		if (!trimmed) continue;
		if (trimmed.startsWith("#")) continue;
		if (trimmed.startsWith("```")) continue;
		if (trimmed.startsWith("---")) continue;
		if (IMAGE_ONLY_PATTERN.test(trimmed)) continue;
		return collapseWhitespace(trimmed);
	}

	return null;
}

function shorten(text: string | null | undefined, maxChars = 180): string {
	if (!text) return "";
	const normalized = collapseWhitespace(text);
	if (normalized.length <= maxChars) return normalized;
	return `${normalized.slice(0, maxChars - 3).trimEnd()}...`;
}

function walkFiles(baseDir: string, predicate: (filePath: string) => boolean): string[] {
	if (!exists(baseDir)) return [];

	const results: string[] = [];
	const stack = [baseDir];

	while (stack.length > 0) {
		const current = stack.pop()!;
		for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
			const fullPath = path.join(current, entry.name);
			if (entry.isDirectory()) {
				stack.push(fullPath);
				continue;
			}
			if (predicate(fullPath)) results.push(fullPath);
		}
	}

	return results.sort();
}

function markdownEntry(repoRoot: string, filePath: string, kind: string): MarkdownItem {
	const text = readText(filePath);
	const { data, body } = parseFrontmatter(text);
	const title = data.name || firstHeading(body) || path.basename(filePath, path.extname(filePath));
	const description = data.description || firstParagraph(body) || "";

	return {
		path: toPosixRelative(repoRoot, filePath),
		kind,
		title,
		description: shorten(description),
	};
}

function scanMarkdown(repoRoot: string): MarkdownItem[] {
	const items: MarkdownItem[] = [];

	for (const [relativeDir, kind] of [
		["docs", "docs"],
		["tutorial", "tutorial"],
		["prompts", "prompt"],
	] as const) {
		const baseDir = path.join(repoRoot, relativeDir);
		for (const filePath of walkFiles(baseDir, (candidate) => candidate.endsWith(".md"))) {
			items.push(markdownEntry(repoRoot, filePath, kind));
		}
	}

	for (const filePath of walkFiles(path.join(repoRoot, "skills"), (candidate) => path.basename(candidate) === "SKILL.md")) {
		items.push(markdownEntry(repoRoot, filePath, "skill"));
	}

	const agentsDir = path.join(repoRoot, ".pi", "agents");
	if (exists(agentsDir)) {
		for (const entry of fs.readdirSync(agentsDir)) {
			if (!entry.endsWith(".md")) continue;
			items.push(markdownEntry(repoRoot, path.join(agentsDir, entry), "specialist"));
		}
	}

	for (const relativePath of ["README.md", "AGENTS.md"]) {
		const filePath = path.join(repoRoot, relativePath);
		if (exists(filePath)) items.push(markdownEntry(repoRoot, filePath, "root"));
	}

	return items;
}

function scanSpecialists(repoRoot: string): SpecialistItem[] {
	const items: SpecialistItem[] = [];
	const agentsDir = path.join(repoRoot, ".pi", "agents");
	if (!exists(agentsDir)) return items;

	for (const entry of fs.readdirSync(agentsDir).sort()) {
		if (!entry.endsWith(".md")) continue;
		const filePath = path.join(agentsDir, entry);
		const { data } = parseFrontmatter(readText(filePath));
		items.push({
			name: data.name || path.basename(entry, ".md"),
			description: data.description || "",
			tools: data.tools || "",
			model: data.model || "",
			path: toPosixRelative(repoRoot, filePath),
		});
	}

	return items;
}

function scanToolNames(text: string): string[] {
	return Array.from(new Set(Array.from(text.matchAll(TOOL_NAME_PATTERN), (match) => match[1]))).sort();
}

function scanExtensionTools(repoRoot: string): ExtensionItem[] {
	const baseDir = path.join(repoRoot, "extensions");
	if (!exists(baseDir)) return [];

	const items: ExtensionItem[] = [];
	for (const entry of fs.readdirSync(baseDir, { withFileTypes: true })) {
		if (!entry.isDirectory()) continue;
		const indexPath = path.join(baseDir, entry.name, "index.ts");
		if (!exists(indexPath)) continue;
		items.push({
			name: entry.name,
			path: toPosixRelative(repoRoot, indexPath),
			tools: scanToolNames(readText(indexPath)),
		});
	}

	return items.sort((a, b) => a.name.localeCompare(b.name));
}

function loadSettingsPackageRefs(repoRoot: string): string[] {
	const settingsPath = path.join(repoRoot, ".pi", "settings.json");
	if (!exists(settingsPath)) return [];

	try {
		const settings = JSON.parse(readText(settingsPath));
		if (!Array.isArray(settings.packages)) return [];
		return settings.packages.filter((item: unknown): item is string => typeof item === "string" && item.trim().length > 0);
	} catch {
		return [];
	}
}

function scanExternalPackages(repoRoot: string): ExternalPackageItem[] {
	const items: ExternalPackageItem[] = [];

	for (const ref of loadSettingsPackageRefs(repoRoot)) {
		if (!ref.startsWith("npm:")) continue;

		const packageName = ref.slice(4).trim();
		if (!packageName) continue;

		const packageRoot = path.join(repoRoot, ".pi", "npm", "node_modules", packageName);
		const packageJsonPath = path.join(packageRoot, "package.json");
		const installed = exists(packageJsonPath);
		let description = "";
		let version = "";
		let tools: string[] = [];

		if (installed) {
			try {
				const packageData = JSON.parse(readText(packageJsonPath));
				if (typeof packageData.description === "string") description = packageData.description;
				if (typeof packageData.version === "string") version = packageData.version;
				if (packageData.pi && Array.isArray(packageData.pi.extensions)) {
					tools = Array.from(
						new Set<string>(
							packageData.pi.extensions.flatMap((extensionEntry: unknown): string[] => {
								if (typeof extensionEntry !== "string") return [];
								const extensionPath = path.join(packageRoot, extensionEntry);
								return exists(extensionPath) ? scanToolNames(readText(extensionPath)) : [];
							}),
						),
					).sort();
				}
			} catch {
				// ignore invalid package data
			}
		}

		items.push({
			name: packageName,
			ref,
			path: path.posix.join(".pi", "npm", "node_modules", packageName),
			description: shorten(description),
			version,
			installed,
			tools,
		});
	}

	return items;
}

function writeDocsIndex(
	repoRoot: string,
	markdownItems: MarkdownItem[],
	specialists: SpecialistItem[],
	extensions: ExtensionItem[],
	externalPackages: ExternalPackageItem[],
) {
	const knowledgeDir = path.join(repoRoot, "knowledge");
	const data = {
		generated_at: new Date().toISOString(),
		documents: markdownItems,
		specialists,
		extensions,
		external_packages: externalPackages,
	};

	writeText(path.join(knowledgeDir, "docs-index.json"), `${JSON.stringify(data, null, 2)}\n`);

	const lines: string[] = [];
	lines.push("# Documentation index", "", `Generated at: ${data.generated_at}`, "", "## Documents");
	for (const item of markdownItems) {
		lines.push(`- \`${item.path}\` — **${item.title}**: ${item.description}`);
	}
	lines.push("", "## Specialists");
	for (const item of specialists) {
		lines.push(`- \`${item.name}\` — ${item.description} | tools: ${item.tools || "(default)"} | model: ${item.model || "(default)"}`);
	}
	lines.push("", "## Extensions and tools");
	for (const item of extensions) {
		lines.push(`- \`${item.name}\` — tools: ${item.tools.length ? item.tools.join(", ") : "(no registered tool found by simple scan)"}`);
	}
	lines.push("", "## External runtime packages");
	if (externalPackages.length === 0) {
		lines.push("- none");
	} else {
		for (const item of externalPackages) {
			const toolText = item.tools.length ? item.tools.join(", ") : "(tool scan unavailable until installed)";
			const installText = item.installed
				? item.version
					? `installed (${item.version})`
					: "installed"
				: "referenced in `.pi/settings.json`";
			lines.push(`- \`${item.name}\` — ${item.description || "No description available."} | tools: ${toolText} | ${installText}`);
		}
	}

	writeText(path.join(knowledgeDir, "docs-index.md"), `${lines.join("\n").trim()}\n`);
}

function writeCodebaseMap(repoRoot: string, extensions: ExtensionItem[], externalPackages: ExternalPackageItem[]) {
	const knowledgeDir = path.join(repoRoot, "knowledge");
	const lines: string[] = [];

	lines.push("# Codebase map", "", "```text", `${path.basename(repoRoot)}/`);
	lines.push("├── .pi/                      Project-local Pi settings and specialist definitions");
	lines.push("├── config/                   Child-specialist policy and other runtime config");
	lines.push("├── docs/                     Project documentation");
	lines.push("├── tutorial/                 Teaching-oriented extension guide");
	lines.push("├── extensions/               Pi hooks and tools");
	for (const item of extensions) {
		lines.push(`│   ├── ${item.name}/`);
	}
	lines.push("├── knowledge/                Generated repo context used by the agent");
	lines.push("├── prompts/                  Reusable prompt templates");
	lines.push("├── scripts/                  Utility scripts");
	lines.push("├── skills/                   Optional deep instructions");
	lines.push("└── Dockerfile                Optional Python 3.11 runtime for mainsequence");
	lines.push("```", "", "## External Pi packages");

	if (externalPackages.length === 0) {
		lines.push("- none");
	} else {
		for (const item of externalPackages) {
			const toolText = item.tools.length ? item.tools.join(", ") : "(tool scan unavailable until installed)";
			lines.push(`- \`${item.name}\` — tools: ${toolText}`);
		}
	}

	lines.push("", "## Runtime flow", "");
	lines.push("1. `.pi/APPEND_SYSTEM.md` provides the static parent orchestrator prompt.");
	lines.push("2. `docs-context` appends generated repo context at `before_agent_start`.");
	lines.push("3. `project-policy` appends child-specialist policy only when Astro spawns a child process.");
	lines.push("4. Parent agent uses the Main Sequence CLI, prepares `astro/` files, and may call `delegate_specialist` with a target `cwd`.");
	lines.push("5. Child `pi` process runs a specialist from `.pi/agents/` inside the checked-out project folder.");
	lines.push("6. Parent agent reviews status directly or via `doc-bug-auditor`; `audit_recent_changes` remains available for Astro-side edits.");

	writeText(path.join(knowledgeDir, "codebase-map.md"), `${lines.join("\n")}\n`);
}

function writeAgentContext(
	repoRoot: string,
	markdownItems: MarkdownItem[],
	specialists: SpecialistItem[],
	extensions: ExtensionItem[],
	externalPackages: ExternalPackageItem[],
) {
	const knowledgeDir = path.join(repoRoot, "knowledge");
	const lines: string[] = [];

	lines.push(`# ${projectDisplayName(repoRoot)} agent context`, "");
	lines.push("This repository is a Pi package that teaches and implements a Main Sequence project orchestration architecture.", "");
	lines.push("## Core runtime flow", "");
	lines.push("1. `.pi/APPEND_SYSTEM.md` provides the static parent orchestrator prompt.");
	lines.push("2. `docs-context` appends generated repository context at `before_agent_start`.");
	lines.push("3. `project-policy` appends child-specialist policy only when Astro spawns a child process.");
	lines.push("4. The parent agent translates the user request into a Main Sequence project brief and uses the Main Sequence CLI to create or open the project.");
	lines.push("5. The parent agent writes the checked-out project's `astro/` handoff files.");
	lines.push("6. The parent agent normally delegates implementation to `mainsequence-project-coder` through `delegate_specialist` with the project's `cwd`.");
	lines.push("7. The parent agent uses `doc-bug-auditor` for structured project status review and can still use `audit_recent_changes` for Astro-side edits.");

	lines.push("", "## Specialists");
	if (specialists.length === 0) {
		lines.push("- none");
	} else {
		for (const item of specialists) {
			lines.push(`- ${item.name}: ${item.description} | tools: ${item.tools || "(default)"}`);
		}
	}

	lines.push("", "## Registered extension tools");
	if (extensions.length === 0) {
		lines.push("- none");
	} else {
		for (const item of extensions) {
			lines.push(`- ${item.name}: ${item.tools.length ? item.tools.join(", ") : "(no registered tool found by simple scan)"}`);
		}
	}

	lines.push("", "## External runtime packages");
	if (externalPackages.length === 0) {
		lines.push("- none");
	} else {
		for (const item of externalPackages) {
			const toolText = item.tools.length ? item.tools.join(", ") : "(tool scan unavailable until installed)";
			const installText = item.installed
				? item.version
					? `installed (${item.version})`
					: "installed"
				: "referenced in `.pi/settings.json`";
			lines.push(`- ${item.name}: ${toolText} | ${installText}`);
		}
	}

	lines.push("", "## House rules");
	lines.push("- Prefer additive changes over core rewrites.");
	lines.push("- Prefer extensions, specialists, prompts, skills, and shared TypeScript helpers before invasive redesign.");
	lines.push("- Update docs/tutorial/knowledge together when architecture changes.");
	lines.push("- Run `refresh_docs_index` or `npm run docs:index` after structural changes.");

		const importantDocs = new Set([
			"README.md",
			"docs/architecture.md",
			"docs/wiring-flow.md",
			"docs/scope.md",
			"tutorial/00-start-here.md",
	]);

	lines.push("", "## Important reading");
	for (const item of markdownItems) {
		if (importantDocs.has(item.path)) {
			lines.push(`- \`${item.path}\` — ${item.title}`);
		}
	}

	writeText(path.join(knowledgeDir, "agent-context.md"), `${lines.join("\n")}\n`);
}

export function refreshDocsIndex(repoRoot: string): RefreshDocsIndexResult {
	const knowledgeDir = path.join(repoRoot, "knowledge");
	fs.mkdirSync(knowledgeDir, { recursive: true });

	const markdownItems = scanMarkdown(repoRoot);
	const specialists = scanSpecialists(repoRoot);
	const extensions = scanExtensionTools(repoRoot);
	const externalPackages = scanExternalPackages(repoRoot);

	writeDocsIndex(repoRoot, markdownItems, specialists, extensions, externalPackages);
	writeCodebaseMap(repoRoot, extensions, externalPackages);
	writeAgentContext(repoRoot, markdownItems, specialists, extensions, externalPackages);

	return {
		repoRoot,
		knowledgeDir,
		generatedFiles: [
			path.join(knowledgeDir, "docs-index.json"),
			path.join(knowledgeDir, "docs-index.md"),
			path.join(knowledgeDir, "codebase-map.md"),
			path.join(knowledgeDir, "agent-context.md"),
		],
	};
}
