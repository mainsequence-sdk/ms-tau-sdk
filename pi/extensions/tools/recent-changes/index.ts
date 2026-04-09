import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { type AgentScope, discoverAgents } from "../specialist-delegate/agents.js";
import { getFinalOutput, isResultFailure, runSingleAgent } from "../specialist-delegate/runtime.js";
import { findRepoRoot, normalizeFilePath } from "../../shared/repo.js";

const MAX_TRACKED_FILES = 50;
const trackedFiles: string[] = [];

const AgentScopeSchema = Type.Union(
	[Type.Literal("user"), Type.Literal("project"), Type.Literal("both")],
	{
		default: "project",
		description: "Where to discover specialists from. Default is project.",
	},
);

function remember(filePath: string) {
	const existingIndex = trackedFiles.indexOf(filePath);
	if (existingIndex >= 0) trackedFiles.splice(existingIndex, 1);
	trackedFiles.push(filePath);
	if (trackedFiles.length > MAX_TRACKED_FILES) trackedFiles.shift();
}

function clearTrackedFiles() {
	trackedFiles.length = 0;
}

function snapshot(limit = 10): string[] {
	return trackedFiles.slice(-limit).reverse();
}

function refreshStatus(ctx: any) {
	if (!ctx.hasUI) return;
	ctx.ui.setStatus("astro-changes", trackedFiles.length ? `changes:${trackedFiles.length}` : undefined);
}

export default function (pi: ExtensionAPI) {
	if (process.env.ASTRO_SUBAGENT_CHILD === "1") return;

	pi.on("session_start", async (_event, ctx) => {
		clearTrackedFiles();
		refreshStatus(ctx);
	});

	pi.on("session_switch", async (_event, ctx) => {
		clearTrackedFiles();
		refreshStatus(ctx);
	});

	pi.on("session_fork", async (_event, ctx) => {
		clearTrackedFiles();
		refreshStatus(ctx);
	});

	pi.on("tool_result", async (event, ctx) => {
		if (event.isError) return;
		if (event.toolName !== "write" && event.toolName !== "edit") return;

		const rawPath = (event.input.file_path || event.input.path) as string | undefined;
		if (!rawPath) return;

		const repoRoot = findRepoRoot(ctx.cwd);
		remember(normalizeFilePath(repoRoot, rawPath));
		refreshStatus(ctx);
	});

	pi.registerTool({
		name: "list_recent_changes",
		label: "List Recent Changes",
		description:
			"List files recently changed by the parent agent session via write/edit. Use this to choose what to audit or summarize.",
		promptSnippet:
			"list_recent_changes: list the files recently changed by the parent agent session",
		parameters: Type.Object({
			limit: Type.Optional(
				Type.Integer({
					description: "Maximum number of files to return",
					minimum: 1,
					maximum: 50,
					default: 10,
				}),
			),
		}),
		async execute(_toolCallId, params) {
			const files = snapshot(params.limit ?? 10);
			return {
				content: [
					{
						type: "text",
						text: files.length
							? files.map((file, index) => `${index + 1}. ${file}`).join("\n")
							: "No parent-session changes are currently tracked.",
					},
				],
				details: { files },
			};
		},
	});

	pi.registerTool({
		name: "audit_recent_changes",
		label: "Audit Recent Changes",
		description:
			"Ask the doc-bug-auditor specialist to review files recently changed by the parent Astro session.",
		promptSnippet:
			"audit_recent_changes: run doc-bug-auditor against files recently changed by the parent Astro session",
		promptGuidelines: [
			"Use audit_recent_changes after multiple Astro edits or before finalizing risky orchestrator changes.",
		],
		parameters: Type.Object({
			focus: Type.Optional(
				Type.String({
					description: "Optional extra focus, such as tests, docs, naming, or edge cases.",
				}),
			),
			limit: Type.Optional(
				Type.Integer({
					description: "How many recently changed files to include",
					minimum: 1,
					maximum: 25,
					default: 10,
				}),
			),
			agentScope: Type.Optional(AgentScopeSchema),
		}),
		async execute(_toolCallId, params, signal, onUpdate, ctx) {
			const files = snapshot(params.limit ?? 10);
			if (files.length === 0) {
				return {
					content: [{ type: "text", text: "No parent-session changes are currently tracked." }],
					details: { files: [] },
				};
			}

			const agentScope: AgentScope = params.agentScope ?? "project";
			const discovery = discoverAgents(ctx.cwd, agentScope);
			const agents = discovery.agents;

			if (!agents.some((agent) => agent.name === "doc-bug-auditor")) {
				return {
					content: [
						{
							type: "text",
							text: 'The "doc-bug-auditor" specialist was not found. Check .pi/agents/doc-bug-auditor.md.',
						},
					],
					details: { files },
					isError: true,
				};
			}

			const task = [
				"Audit the following recently changed files from the parent agent session.",
				params.focus
					? `Focus especially on: ${params.focus}.`
					: "Focus especially on bugs, documentation drift, inconsistency, and extension-boundary violations.",
				"",
				"Files:",
				...files.map((file) => `- ${file}`),
				"",
				"Read the files directly from the repository and review what is actually there.",
				"Return a concrete review that the parent agent can act on immediately.",
			].join("\n");

			const result = await runSingleAgent({
				defaultCwd: ctx.cwd,
				agents,
				projectAgentsDir: discovery.projectAgentsDir,
				agentName: "doc-bug-auditor",
				task,
				mode: "single",
				agentScope,
				signal,
				onUpdate,
			});

			if (isResultFailure(result)) {
				return {
					content: [
						{
							type: "text",
							text:
								result.errorMessage ||
								result.stderr ||
								getFinalOutput(result.messages) ||
								"Audit specialist failed.",
						},
					],
					details: {
						files,
						result,
					},
					isError: true,
				};
			}

			return {
				content: [{ type: "text", text: getFinalOutput(result.messages) || "(no output)" }],
				details: {
					files,
					result,
				},
			};
		},
	});
}
