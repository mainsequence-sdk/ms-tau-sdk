import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { bootstrapMainsequenceCliAuth } from "../../../../scripts/mainsequence_runtime_auth.js";

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "ensure_mainsequence_cli_auth",
		label: "Ensure Main Sequence CLI Auth",
		description:
			"Repair or re-run the runtime-managed Main Sequence CLI login for this agent session. Use this when a mainsequence command reports auth failure, then retry the blocked command.",
		promptSnippet:
			"ensure_mainsequence_cli_auth: repair runtime-managed Main Sequence CLI auth before retrying a blocked mainsequence command",
		promptGuidelines: [
			"Use this when a mainsequence command reports auth failure such as Not logged in, Token is invalid, or Current user fetch failed.",
			"Call it once, then retry the blocked mainsequence command.",
			"If the retry still fails with auth errors after this tool succeeds, report a runtime auth problem instead of asking the user to log in manually.",
		],
		parameters: Type.Object({
			reason: Type.Optional(
				Type.String({
					description: "Optional short note about which command or auth error triggered this repair.",
				}),
			),
		}),
		async execute(_toolCallId, params) {
			const logs: string[] = [];

			try {
				await bootstrapMainsequenceCliAuth({
					env: process.env,
					log: (message) => logs.push(message),
				});

				const reason = typeof params.reason === "string" && params.reason.trim()
					? params.reason.trim()
					: null;

				return {
					content: [
						{
							type: "text",
							text: reason
								? `Main Sequence CLI auth is ready again. Retry the blocked command that failed with: ${reason}`
								: "Main Sequence CLI auth is ready again. Retry the blocked mainsequence command now.",
						},
					],
					details: {
						ok: true,
						reason,
						logs,
					},
				};
			} catch (error) {
				const message =
					error instanceof Error ? error.message : "Unknown Main Sequence CLI auth repair failure.";

				return {
					content: [
						{
							type: "text",
							text: `Main Sequence CLI auth repair failed: ${message}`,
						},
					],
					details: {
						ok: false,
						logs,
						error: message,
					},
					isError: true,
				};
			}
		},
	});
}
