import { spawn } from "node:child_process";
import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

type Env = Record<string, string | undefined>;

const REPAIR_COMMAND_ENV = "MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND";
const TIMEOUT_MS_ENV = "MAINSEQUENCE_PI_CLI_AUTH_REPAIR_TIMEOUT_MS";

function repairCommand(env: Env): string | null {
	const value = env[REPAIR_COMMAND_ENV]?.trim();
	return value || null;
}

function repairTimeoutMs(env: Env): number {
	const configured = Number(env[TIMEOUT_MS_ENV] ?? "30000");
	return Number.isFinite(configured) && configured >= 1000 ? Math.trunc(configured) : 30000;
}

function summarize(text: string): string {
	const trimmed = text.trim();
	if (trimmed.length <= 4000) return trimmed;
	return `${trimmed.slice(0, 4000)}...`;
}

async function runRepair(command: string, timeoutMs: number): Promise<{
	ok: boolean;
	code: number | null;
	stdout: string;
	stderr: string;
	error?: string;
}> {
	return new Promise((resolve) => {
		const child = spawn(command, {
			shell: true,
			stdio: ["ignore", "pipe", "pipe"],
			env: process.env,
		});

		let stdout = "";
		let stderr = "";
		let settled = false;

		const timeout = setTimeout(() => {
			if (settled) return;
			settled = true;
			child.kill("SIGTERM");
			resolve({
				ok: false,
				code: null,
				stdout: summarize(stdout),
				stderr: summarize(stderr),
				error: `Main Sequence CLI auth repair timed out after ${timeoutMs}ms.`,
			});
		}, timeoutMs);

		child.stdout?.on("data", (chunk) => {
			stdout += String(chunk);
		});

		child.stderr?.on("data", (chunk) => {
			stderr += String(chunk);
		});

		child.on("error", (error) => {
			if (settled) return;
			settled = true;
			clearTimeout(timeout);
			resolve({
				ok: false,
				code: null,
				stdout: summarize(stdout),
				stderr: summarize(stderr),
				error: error.message,
			});
		});

		child.on("close", (code) => {
			if (settled) return;
			settled = true;
			clearTimeout(timeout);
			resolve({
				ok: code === 0,
				code,
				stdout: summarize(stdout),
				stderr: summarize(stderr),
			});
		});
	});
}

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "ensure_mainsequence_cli_auth",
		label: "Ensure Main Sequence CLI Auth",
		description:
			"Run the host-provided Main Sequence CLI auth repair command, then retry the blocked Main Sequence CLI command.",
		promptSnippet:
			"ensure_mainsequence_cli_auth: repair Main Sequence CLI auth before retrying a blocked mainsequence command",
		promptGuidelines: [
			"Use this once when a mainsequence command reports an auth failure.",
			"Retry the blocked mainsequence command after this tool succeeds.",
			"If this tool fails, report the auth repair failure instead of asking the user for credentials.",
		],
		parameters: Type.Object({
			reason: Type.Optional(
				Type.String({
					description: "Optional short note about which command or auth error triggered this repair.",
				}),
			),
		}),
		async execute(_toolCallId, params) {
			const command = repairCommand(process.env);
			const reason = typeof params.reason === "string" && params.reason.trim()
				? params.reason.trim()
				: null;

			if (!command) {
				return {
					content: [
						{
							type: "text",
							text: `Main Sequence CLI auth repair is not configured. The host must set ${REPAIR_COMMAND_ENV}.`,
						},
					],
					details: {
						ok: false,
						reason,
						missingEnv: REPAIR_COMMAND_ENV,
					},
					isError: true,
				};
			}

			const result = await runRepair(command, repairTimeoutMs(process.env));
			if (!result.ok) {
				return {
					content: [
						{
							type: "text",
							text: `Main Sequence CLI auth repair failed${result.error ? `: ${result.error}` : "."}`,
						},
					],
					details: {
						ok: false,
						reason,
						command,
						...result,
					},
					isError: true,
				};
			}

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
					command,
					...result,
				},
			};
		},
	});
}
