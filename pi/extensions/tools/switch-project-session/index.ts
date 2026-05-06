import * as fs from "node:fs";
import * as path from "node:path";
import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

function isDirectory(candidate: string): boolean {
	try {
		return fs.statSync(candidate).isDirectory();
	} catch {
		return false;
	}
}

export default function (pi: ExtensionAPI) {
	if (process.env.ASTRO_SUBAGENT_CHILD === "1") return;

	pi.registerTool({
		name: "switch_project_session",
		label: "Switch Project Session",
		description:
			"Request a real session switch from the parent orchestrator into a checked-out mainsequence-project-coder session. Use this after the project id and checked-out cwd are known.",
		promptSnippet:
			"switch_project_session: create a real project-scoped session handoff for mainsequence-project-coder",
		promptGuidelines: [
			"Use this when the user should continue inside one checked-out Main Sequence project.",
			"Pass both projectId and cwd.",
			"Pass agentId when the backend already supplied the target mainsequence-project-coder agent id.",
			"Use initialTask only when the current user turn already includes concrete project-local work.",
			"Do not claim a session switch in plain text without calling this tool.",
		],
		parameters: Type.Object({
			projectId: Type.String({
				description: "Selected Main Sequence project id for the target project session.",
			}),
			cwd: Type.String({
				description: "Absolute path to the checked-out target project directory.",
			}),
			agentId: Type.Optional(
				Type.Number({
					description:
						"Optional backend integer agent id for the target mainsequence-project-coder agent when already known.",
				}),
			),
			initialTask: Type.Optional(
				Type.String({
					description:
						"Optional concrete project-local task to carry into the coder session when the current turn already includes one.",
				}),
			),
			summary: Type.Optional(
				Type.String({
					description:
						"Optional short summary of why the session is being switched or what project context was established.",
				}),
			),
		}),
		async execute(_toolCallId, params) {
			const projectId = params.projectId.trim();
			const resolvedCwd = path.resolve(params.cwd);

			if (!projectId) {
				return {
					content: [{ type: "text", text: "switch_project_session requires projectId." }],
					isError: true,
				};
			}

			if (!params.cwd.trim()) {
				return {
					content: [{ type: "text", text: "switch_project_session requires cwd." }],
					isError: true,
				};
			}

			if (!isDirectory(resolvedCwd)) {
				return {
					content: [
						{
							type: "text",
							text: `switch_project_session requires cwd to be an existing project directory. Invalid cwd: ${resolvedCwd}`,
						},
					],
					isError: true,
				};
			}

			const initialTask = typeof params.initialTask === "string" && params.initialTask.trim()
				? params.initialTask.trim()
				: null;
			const summary = typeof params.summary === "string" && params.summary.trim()
				? params.summary.trim()
				: null;
			const agentId = typeof params.agentId === "number" && Number.isFinite(params.agentId)
				? Math.trunc(params.agentId)
				: null;

			return {
				content: [
					{
						type: "text",
						text: `Project session switch requested for project ${projectId} at ${resolvedCwd}.`,
					},
				],
				details: {
					sessionSwitch: {
						kind: "project_session_switch",
						agentName: "mainsequence-project-coder",
						projectId,
						cwd: resolvedCwd,
						agentId,
						initialTask,
						summary,
					},
				},
			};
		},
	});
}
