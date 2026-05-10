import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { resolveCurrentAstroAgentName } from "../../shared/a2a.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
const packageJsonPath = path.join(repoRoot, "package.json");

type CommandResult = {
	ok: boolean;
	value: string | null;
	error: string | null;
};

function readAstroPackageVersion(): string | null {
	try {
		const parsed = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
		return typeof parsed?.version === "string" && parsed.version.trim() ? parsed.version.trim() : null;
	} catch {
		return null;
	}
}

function normalizeMeaningfulVersion(value: string | null | undefined): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	if (!trimmed) return null;
	if (["dev", "latest", "unknown"].includes(trimmed.toLowerCase())) return null;
	return trimmed;
}

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
				error:
					(result.error as NodeJS.ErrnoException).code === "ENOENT"
						? `Missing required command: ${command}`
						: result.error.message,
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

function formatRuntimeVersionTag(
	label: string,
	version: string | null,
	options: { prefixWithV?: boolean; separator?: string } = {},
): string {
	if (!version) return `${label} unknown`;
	const separator = options.separator ?? " ";
	return options.prefixWithV ? `${label}${separator}v${version}` : `${label}${separator}${version}`;
}

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "get_runtime_info",
		label: "Get Runtime Info",
		description:
			"Return deterministic runtime/build information for this Astro process, including Astro release version, installed Main Sequence SDK version, Python version, Node version, execution mode, and key runtime paths. Use this for debugging and version questions instead of guessing.",
		promptSnippet:
			"get_runtime_info: report deterministic Astro/runtime version details such as Astro release, installed mainsequence SDK, Python, and Node",
		promptGuidelines: [
			"Use this when the user asks which Astro release, SDK version, Python version, Node version, or runtime mode is currently running.",
			"Use this for executor/orchestrator deployment debugging instead of inferring versions from prompts or file names.",
			"Prefer the tool output over guesses when reporting runtime/build details.",
		],
		parameters: Type.Object({
			includePaths: Type.Optional(
				Type.Boolean({
					description: "Whether to include key runtime paths such as cwd, project cwd, and config directories.",
					default: true,
				}),
			),
		}),
		async execute(_toolCallId, params) {
			const astroPackageVersion = readAstroPackageVersion();
			const astroReleaseVersionEnv = normalizeMeaningfulVersion(process.env.ASTRO_RELEASE_VERSION);
			const mainsequenceSdk = readMainsequenceSdkVersion();
			const mainsequenceCli = readMainsequenceCliVersion();
			const pythonVersion = readPythonVersion();
			const includePaths = params.includePaths !== false;
			const executionMode = process.env.ASTRO_EXECUTION_MODE?.trim() || "default";
			const processCwd = process.cwd();
			const projectCwd = process.env.ASTRO_FIXED_PROJECT_CWD?.trim() || null;
			const effectiveWorkspaceCwd =
				executionMode === "remote_project_worker" && projectCwd ? projectCwd : processCwd;

			const details = {
				astro_release_version: astroReleaseVersionEnv ?? astroPackageVersion,
				astro_release_version_env: process.env.ASTRO_RELEASE_VERSION?.trim() || null,
				astro_package_version: astroPackageVersion,
				mainsequence_sdk_version: mainsequenceSdk.ok ? mainsequenceSdk.value : null,
				mainsequence_cli_version: mainsequenceCli.ok ? mainsequenceCli.value : null,
				python_version: pythonVersion.ok ? pythonVersion.value : null,
				node_version: process.versions.node,
				agent_name: resolveCurrentAstroAgentName(process.env),
				execution_mode: executionMode,
				project_image_ref: process.env.ASTRO_PROJECT_IMAGE_REF?.trim() || null,
				auth_mode: process.env.MAINSEQUENCE_AUTH_MODE?.trim() || null,
				...(includePaths
					? {
							effective_workspace_cwd: effectiveWorkspaceCwd,
							process_cwd: processCwd,
							project_cwd: projectCwd,
							home: process.env.HOME?.trim() || null,
							astro_container_data_dir: process.env.ASTRO_CONTAINER_DATA_DIR?.trim() || null,
							astro_mainsequence_config_dir:
								process.env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim() || null,
							pi_coding_agent_dir: process.env.PI_CODING_AGENT_DIR?.trim() || null,
					  }
					: {}),
				errors: {
					mainsequence_sdk_version: mainsequenceSdk.ok ? null : mainsequenceSdk.error,
					mainsequence_cli_version: mainsequenceCli.ok ? null : mainsequenceCli.error,
					python_version: pythonVersion.ok ? null : pythonVersion.error,
				},
			};

			const versionTags = [
				formatRuntimeVersionTag("Astro", details.astro_release_version, { prefixWithV: true }),
				formatRuntimeVersionTag("ms-sdk", details.mainsequence_sdk_version, { separator: "-" }),
				formatRuntimeVersionTag("Python", details.python_version),
				formatRuntimeVersionTag("Node", details.node_version),
			];

			const lines = [
				`Versions: ${versionTags.join(" | ")}`,
				`Astro package ${details.astro_package_version ?? "unknown"}`,
				`Agent: ${details.agent_name}`,
				`Execution mode: ${details.execution_mode}`,
			];

			if (includePaths) {
				lines.push(
					`Workspace cwd: ${details.effective_workspace_cwd}`,
					`Astro process cwd: ${details.process_cwd}`,
					`Project cwd: ${details.project_cwd ?? "unset"}`,
					`Container data dir: ${details.astro_container_data_dir ?? "unset"}`,
				);
			}

			return {
				content: [
					{
						type: "text",
						text: lines.join("\n"),
					},
				],
				details: {
					...details,
					version_tags: versionTags,
				},
			};
		},
	});
}
