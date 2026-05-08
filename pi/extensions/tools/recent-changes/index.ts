import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { findRepoRoot, normalizeFilePath } from "../../shared/repo.js";

const MAX_TRACKED_FILES = 50;
const trackedFiles: string[] = [];

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
			"List files recently changed by the parent agent session via write/edit. Use this to review or summarize recent edits.",
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
}
