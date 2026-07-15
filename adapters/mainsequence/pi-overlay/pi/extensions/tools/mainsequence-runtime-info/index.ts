import { spawnSync } from "node:child_process";
import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

type CommandResult = {
	ok: boolean;
	value: string | null;
	error: string | null;
};

function runCommand(command: string, args: string[]): CommandResult {
	try {
		const result = spawnSync(command, args, {
			env: process.env,
			encoding: "utf8",
			stdio: ["ignore", "pipe", "pipe"],
		});

		if (result.error) {
			return {
				ok: false,
				value: null,
				error: result.error.message,
			};
		}

		if ((result.status ?? 1) !== 0) {
			const stderr = result.stderr.trim();
			const stdout = result.stdout.trim();
			return {
				ok: false,
				value: null,
				error: stderr || stdout || `${command} exited with code ${result.status ?? 1}.`,
			};
		}

		const value = result.stdout.trim() || result.stderr.trim();
		return {
			ok: true,
			value: value || null,
			error: null,
		};
	} catch (error) {
		return {
			ok: false,
			value: null,
			error: error instanceof Error ? error.message : String(error),
		};
	}
}

function readPythonVersion(): CommandResult {
	return runCommand("python3", ["-c", "import platform; print(platform.python_version())"]);
}

function readMainsequenceSdkVersion(): CommandResult {
	return runCommand("python3", ["-c", "import importlib.metadata as im; print(im.version('mainsequence'))"]);
}

function readMainsequenceCliVersion(): CommandResult {
	return runCommand("mainsequence", ["--version"]);
}

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "get_mainsequence_runtime_info",
		label: "Get Main Sequence Runtime Info",
		description:
			"Return deterministic Main Sequence environment information, including installed SDK, CLI, Python, and Node versions.",
		promptSnippet:
			"get_mainsequence_runtime_info: report installed Main Sequence SDK, CLI, Python, and Node versions",
		promptGuidelines: [
			"Use this when the user asks which Main Sequence SDK or CLI version is installed.",
			"Use this for Main Sequence package/runtime debugging instead of guessing versions.",
			"Do not report host-specific deployment details unless the host provides a separate runtime-info tool.",
		],
		parameters: Type.Object({
			includeProcessInfo: Type.Optional(
				Type.Boolean({
					description: "Whether to include process cwd and HOME.",
					default: false,
				}),
			),
		}),
		async execute(_toolCallId, params) {
			const mainsequenceSdk = readMainsequenceSdkVersion();
			const mainsequenceCli = readMainsequenceCliVersion();
			const pythonVersion = readPythonVersion();
			const includeProcessInfo = params.includeProcessInfo === true;

			const details = {
				mainsequence_sdk_version: mainsequenceSdk.ok ? mainsequenceSdk.value : null,
				mainsequence_cli_version: mainsequenceCli.ok ? mainsequenceCli.value : null,
				python_version: pythonVersion.ok ? pythonVersion.value : null,
				node_version: process.versions.node,
				...(includeProcessInfo
					? {
							process_cwd: process.cwd(),
							home: process.env.HOME?.trim() || null,
					  }
					: {}),
				errors: {
					mainsequence_sdk_version: mainsequenceSdk.ok ? null : mainsequenceSdk.error,
					mainsequence_cli_version: mainsequenceCli.ok ? null : mainsequenceCli.error,
					python_version: pythonVersion.ok ? null : pythonVersion.error,
				},
			};

			const lines = [
				`Main Sequence SDK: ${details.mainsequence_sdk_version ?? "unknown"}`,
				`Main Sequence CLI: ${details.mainsequence_cli_version ?? "unknown"}`,
				`Python: ${details.python_version ?? "unknown"}`,
				`Node: ${details.node_version}`,
			];

			if (includeProcessInfo) {
				lines.push(`Process cwd: ${details.process_cwd}`, `Home: ${details.home ?? "unset"}`);
			}

			return {
				content: [
					{
						type: "text",
						text: lines.join("\n"),
					},
				],
				details,
			};
		},
	});
}
