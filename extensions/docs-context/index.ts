import * as path from "node:path";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { refreshDocsIndex } from "../shared/docsIndex.js";
import { findRepoRoot, safeReadFile, truncateMiddle } from "../shared/repo.js";

const MAX_CONTEXT_CHARS = 10000;
const KNOWLEDGE_CANDIDATES = [
	"knowledge/agent-context.md",
	"knowledge/docs-index.md",
	"knowledge/codebase-map.md",
];

const FALLBACK_DOCS = [
	"README.md",
	"docs/architecture.md",
	"docs/wiring-flow.md",
	"AGENTS.md",
];

function loadFirstAvailable(
	repoRoot: string,
	candidates: string[],
): { text: string; source: string } | null {
	for (const relativePath of candidates) {
		const text = safeReadFile(path.join(repoRoot, relativePath));
		if (text?.trim()) {
			return { text, source: relativePath };
		}
	}

	return null;
}

function loadFallbackDocs(repoRoot: string): { text: string; source: string } | null {
	const parts: string[] = [];

	for (const relativePath of FALLBACK_DOCS) {
		const text = safeReadFile(path.join(repoRoot, relativePath));
		if (text?.trim()) {
			parts.push(`# ${relativePath}\n\n${text.trim()}`);
		}
	}

	if (parts.length === 0) return null;

	return {
		text: parts.join("\n\n"),
		source: "fallback markdown files",
	};
}

function loadDocContext(repoRoot: string): { text: string; source: string } | null {
	return loadFirstAvailable(repoRoot, KNOWLEDGE_CANDIDATES) || loadFallbackDocs(repoRoot);
}

export default function (pi: ExtensionAPI) {
	if (process.env.ASTRO_SUBAGENT_CHILD !== "1") {
		pi.registerTool({
			name: "refresh_docs_index",
			label: "Refresh Docs Index",
			description:
				"Regenerate knowledge/docs-index.json, knowledge/docs-index.md, knowledge/codebase-map.md, and knowledge/agent-context.md from the current Astro repo.",
			promptSnippet:
				"refresh_docs_index: regenerate Astro's knowledge files after docs, prompt, skill, or extension changes",
			promptGuidelines: [
				"Use refresh_docs_index after structural or documentation changes so generated repo context stays in sync.",
			],
			parameters: Type.Object({}),
			async execute(_toolCallId, _params, _signal, _onUpdate, ctx) {
				const repoRoot = findRepoRoot(ctx.cwd);
				const result = refreshDocsIndex(repoRoot);
				if (ctx.hasUI) ctx.ui.setStatus("astro-docs", "docs:ready");

				return {
					content: [
						{
							type: "text",
							text: [
								"Refreshed generated Astro knowledge files:",
								...result.generatedFiles.map((filePath) => `- ${path.relative(repoRoot, filePath)}`),
							].join("\n"),
						},
					],
					details: {
						repoRoot,
						generatedFiles: result.generatedFiles,
					},
				};
			},
		});
	}

	pi.on("before_agent_start", async (event, ctx) => {
		const repoRoot = findRepoRoot(ctx.cwd);
		const loaded = loadDocContext(repoRoot);
		if (!loaded) return;

		const trimmed = truncateMiddle(loaded.text.trim(), MAX_CONTEXT_CHARS);

		return {
			systemPrompt: `${event.systemPrompt}

# Astro repository documentation context
Source: ${loaded.source}

${trimmed}`,
		};
	});

	pi.on("session_start", async (_event, ctx) => {
		const repoRoot = findRepoRoot(ctx.cwd);
		const hasContext = Boolean(safeReadFile(path.join(repoRoot, "knowledge", "agent-context.md")));
		if (ctx.hasUI && hasContext) ctx.ui.setStatus("astro-docs", "docs:ready");
	});
}
