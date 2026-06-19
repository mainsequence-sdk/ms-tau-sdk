import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

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

function formatRuntimeVersionTag(
	label: string,
	version: string | null,
	options: { prefixWithV?: boolean; separator?: string } = {},
): string {
	if (!version) return `${label} unknown`;
	const separator = options.separator ?? " ";
	return options.prefixWithV ? `${label}${separator}v${version}` : `${label}${separator}${version}`;
}

function normalizeEnvString(value: string | undefined): string | null {
	const trimmed = value?.trim();
	return trimmed ? trimmed : null;
}

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "get_runtime_info",
		label: "Get Runtime Info",
		description:
			"Return deterministic runtime/build information for this Astro process, including Astro release version, Python version, Node version, execution mode, and key runtime paths. Use this for debugging and version questions instead of guessing.",
		promptSnippet:
			"get_runtime_info: report deterministic Astro/runtime version details such as Astro release, Python, and Node",
		promptGuidelines: [
			"Use this when the user asks which Astro release, Python version, Node version, or runtime mode is currently running.",
			"Use this for Astro deployment debugging instead of inferring versions from prompts or file names.",
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
			const pythonVersion = readPythonVersion();
			const includePaths = params.includePaths !== false;
			const executionMode = normalizeEnvString(process.env.ASTRO_EXECUTION_MODE) ?? "default";
			const processCwd = process.cwd();
			const fixedAgentType = normalizeEnvString(process.env.ASTRO_FIXED_AGENT_TYPE);
			const projectCwd = normalizeEnvString(process.env.ASTRO_FIXED_PROJECT_CWD);
			const effectiveWorkspaceCwd = projectCwd ?? processCwd;

			const details = {
				astro_release_version: astroReleaseVersionEnv ?? astroPackageVersion,
				astro_release_version_env: process.env.ASTRO_RELEASE_VERSION?.trim() || null,
				astro_package_version: astroPackageVersion,
				python_version: pythonVersion.ok ? pythonVersion.value : null,
				node_version: process.versions.node,
				execution_mode: executionMode,
				fixed_agent_type: fixedAgentType,
				project_image_ref: normalizeEnvString(process.env.ASTRO_PROJECT_IMAGE_REF),
				...(includePaths
					? {
							effective_workspace_cwd: effectiveWorkspaceCwd,
							process_cwd: processCwd,
							project_cwd: projectCwd,
							home: process.env.HOME?.trim() || null,
							astro_container_data_dir: process.env.ASTRO_CONTAINER_DATA_DIR?.trim() || null,
							pi_coding_agent_dir: process.env.PI_CODING_AGENT_DIR?.trim() || null,
					  }
					: {}),
				errors: {
					python_version: pythonVersion.ok ? null : pythonVersion.error,
				},
			};

			const versionTags = [
				formatRuntimeVersionTag("Astro", details.astro_release_version, { prefixWithV: true }),
				formatRuntimeVersionTag("Python", details.python_version),
				formatRuntimeVersionTag("Node", details.node_version),
			];

			const lines = [
				`Versions: ${versionTags.join(" | ")}`,
				`Astro package ${details.astro_package_version ?? "unknown"}`,
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
