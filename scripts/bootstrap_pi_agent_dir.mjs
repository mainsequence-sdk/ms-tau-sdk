import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const RESOURCE_ARRAY_KEYS = ["extensions", "skills", "prompts", "themes"];
const PATH_BASED_SETTINGS_KEYS = ["packages", ...RESOURCE_ARRAY_KEYS];
const CONTAINER_PROVIDER_AUTH_ENTRIES = [
	"auth.json",
	"sessions",
	"astro-model-provider-auth.json",
	"astro-model-provider-signin.json",
];
const ASTRO_ORCHESTRATOR_CWD_ENV = "ASTRO_ORCHESTRATOR_CWD";
const ASTRO_ORCHESTRATOR_PROJECT_PI_DIR_ENV = "ASTRO_ORCHESTRATOR_PROJECT_PI_DIR";
const ASTRO_WORKSPACE_ANALYSIS_SKILL_PATH_ENV = "ASTRO_WORKSPACE_ANALYSIS_SKILL_PATH";
const ASTRO_A2A_COMMUNICATION_SKILL_PATH_ENV = "ASTRO_A2A_COMMUNICATION_SKILL_PATH";
const WORKSPACE_ANALYSIS_SKILL_SLUG = "command_center/workspace_analysis";
const A2A_COMMUNICATION_SKILL_SLUG = "a2a_communication";
const MAINSEQUENCE_SKILL_BOOTSTRAP_TIMEOUT_MS = (() => {
	const configured = Number(process.env.ASTRO_MAINSEQUENCE_SKILL_BOOTSTRAP_TIMEOUT_MS ?? "5000");
	return Number.isFinite(configured) && configured >= 1000 ? Math.trunc(configured) : 5000;
})();

function ensureDir(dirPath) {
	fs.mkdirSync(dirPath, { recursive: true, mode: 0o700 });
}

function ensureFile(filePath, contents = "", mode = 0o600) {
	ensureDir(path.dirname(filePath));
	if (!fs.existsSync(filePath)) {
		fs.writeFileSync(filePath, contents, { mode });
		return;
	}
	try {
		fs.chmodSync(filePath, mode);
	} catch {
		// ignore chmod failures
	}
}

function removePath(targetPath) {
	try {
		fs.rmSync(targetPath, { recursive: true, force: true });
	} catch {
		// ignore cleanup failures
	}
}

function ensureSymlink(sourcePath, targetPath) {
	const sourceStat = fs.statSync(sourcePath);
	const linkType = sourceStat.isDirectory() ? "dir" : "file";

	try {
		const existingStat = fs.lstatSync(targetPath);
		if (existingStat.isSymbolicLink() && fs.readlinkSync(targetPath) === sourcePath) {
			return false;
		}
		removePath(targetPath);
	} catch {
		// target does not exist yet
	}

	fs.symlinkSync(sourcePath, targetPath, linkType);
	return true;
}

function readJsonFile(filePath) {
	try {
		if (!fs.existsSync(filePath)) return {};
		const contents = fs.readFileSync(filePath, "utf8");
		if (!contents.trim()) return {};
		const parsed = JSON.parse(contents);
		return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
	} catch {
		return {};
	}
}

function writeJsonFile(filePath, value) {
	ensureDir(path.dirname(filePath));
	fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, {
		mode: 0o600,
	});
}

function isPlainObject(value) {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function mergeSettings(base, overrides) {
	const merged = { ...base };

	for (const [key, value] of Object.entries(overrides)) {
		if (value === undefined) continue;

		if (Array.isArray(value) && Array.isArray(merged[key])) {
			merged[key] = [...merged[key], ...value];
			continue;
		}

		if (isPlainObject(value) && isPlainObject(merged[key])) {
			merged[key] = mergeSettings(merged[key], value);
			continue;
		}

		merged[key] = value;
	}

	return merged;
}

function dedupeByJson(items) {
	const seen = new Set();
	const deduped = [];

	for (const item of items) {
		const key = JSON.stringify(item);
		if (seen.has(key)) continue;
		seen.add(key);
		deduped.push(item);
	}

	return deduped;
}

function omitPathBasedSettings(settings) {
	const sanitized = { ...settings };

	for (const key of PATH_BASED_SETTINGS_KEYS) {
		delete sanitized[key];
	}

	return sanitized;
}

function isLikelyLocalSource(value) {
	if (typeof value !== "string") return false;
	const trimmed = value.trim();
	if (!trimmed) return false;
	return (
		trimmed.startsWith(".") ||
		trimmed.startsWith("/") ||
		trimmed === "~" ||
		trimmed.startsWith("~/") ||
		/^[A-Za-z]:[\\/]/.test(trimmed)
	);
}

function resolveSettingPath(value, baseDir) {
	const trimmed = value.trim();
	if (!trimmed) return trimmed;
	if (trimmed === "~") return os.homedir();
	if (trimmed.startsWith("~/")) return path.join(os.homedir(), trimmed.slice(2));
	return path.resolve(baseDir, trimmed);
}

function relativizeResolvedPath(resolvedPath, targetDir) {
	const relativePath = path.relative(targetDir, resolvedPath);
	if (!relativePath) {
		return ".";
	}
	return relativePath.split(path.sep).join("/");
}

function relativizePackageSource(source, sourceBaseDir, targetDir) {
	if (typeof source === "string") {
		return isLikelyLocalSource(source)
			? relativizeResolvedPath(resolveSettingPath(source, sourceBaseDir), targetDir)
			: source;
	}

	if (!isPlainObject(source) || typeof source.source !== "string") {
		return source;
	}

	return {
		...source,
		source: isLikelyLocalSource(source.source)
			? relativizeResolvedPath(resolveSettingPath(source.source, sourceBaseDir), targetDir)
			: source.source,
	};
}

function relativizeSettingsForTarget(sourceBaseDir, targetDir, settings) {
	const normalized = mergeSettings({}, settings);

	if (Array.isArray(normalized.packages)) {
		normalized.packages = dedupeByJson(
			normalized.packages
				.map((entry) => relativizePackageSource(entry, sourceBaseDir, targetDir))
				.filter(Boolean),
		);
	}

	for (const key of RESOURCE_ARRAY_KEYS) {
		if (!Array.isArray(normalized[key])) continue;
		normalized[key] = dedupeByJson(
			normalized[key]
				.filter((entry) => typeof entry === "string" && entry.trim())
				.map((entry) => relativizeResolvedPath(resolveSettingPath(entry, sourceBaseDir), targetDir)),
		);
	}

	return normalized;
}

function ensureArray(value) {
	return Array.isArray(value) ? value : [];
}

function shouldPruneContainerProviderAuth(env = process.env) {
	return Boolean(env.ASTRO_CONTAINER_DATA_DIR?.trim());
}

function pruneContainerProviderAuthState(targetDir, env = process.env) {
	if (!shouldPruneContainerProviderAuth(env)) return [];

	const prunedEntries = [];
	for (const entry of CONTAINER_PROVIDER_AUTH_ENTRIES) {
		const targetPath = path.join(targetDir, entry);
		if (!fs.existsSync(targetPath)) continue;
		removePath(targetPath);
		prunedEntries.push(entry);
	}
	return prunedEntries;
}

function resolveScopedProviderCredentialDir(env = process.env) {
	const configured = env.ASTRO_PROVIDER_CREDENTIAL_DIR?.trim();
	if (configured) return path.resolve(configured);

	const sessionStateDir = env.ASTRO_SESSION_STATE_DIR?.trim();
	if (sessionStateDir) return path.join(path.resolve(sessionStateDir), "pi-agent-auth");

	const streamSessionDir = env.ASTRO_STREAM_SESSION_DIR?.trim();
	if (streamSessionDir) return path.join(path.dirname(path.resolve(streamSessionDir)), "pi-agent-auth");

	return null;
}

function pruneScopedProviderCredentialState(env = process.env) {
	if (!shouldPruneContainerProviderAuth(env)) return null;
	const scopedCredentialDir = resolveScopedProviderCredentialDir(env);
	if (!scopedCredentialDir) return null;
	removePath(scopedCredentialDir);
	ensureDir(scopedCredentialDir);
	return scopedCredentialDir;
}

function ensureRuntimeSettings(targetDir) {
	const targetSettingsPath = path.join(targetDir, "settings.json");
	const repoPiDir = path.join(repoRoot, ".pi");
	const repoSettingsPath = path.join(repoPiDir, "settings.json");

	const runtimeSettings = omitPathBasedSettings(readJsonFile(targetSettingsPath));
	const repoSettings = relativizeSettingsForTarget(repoPiDir, targetDir, readJsonFile(repoSettingsPath));
	const requiredPackages = [
		relativizeResolvedPath(repoRoot, targetDir),
		relativizeResolvedPath(path.join(repoRoot, "node_modules", "pi-web-access"), targetDir),
	];

	const finalSettings = mergeSettings(runtimeSettings, repoSettings);
	finalSettings.packages = dedupeByJson([
		...ensureArray(repoSettings.packages),
		...requiredPackages,
	]);

	for (const key of RESOURCE_ARRAY_KEYS) {
		if (key in finalSettings) {
			finalSettings[key] = dedupeByJson(ensureArray(finalSettings[key]));
		}
	}

	try {
		const existingStat = fs.lstatSync(targetSettingsPath);
		if (existingStat.isSymbolicLink()) {
			removePath(targetSettingsPath);
		}
	} catch {
		// target does not exist yet
	}

	ensureDir(path.dirname(targetSettingsPath));
	fs.writeFileSync(targetSettingsPath, `${JSON.stringify(finalSettings, null, 2)}\n`, {
		mode: 0o600,
	});

	return targetSettingsPath;
}

// Pi's standard settings model is:
// - global settings: PI_CODING_AGENT_DIR/settings.json
// - project settings: <cwd>/.pi/settings.json
// Containers may run with /app read-only, so Astro materializes its project-local
// Pi config into a writable runtime cwd instead of patching Pi or writing to /app.
function buildRuntimePiSettings(sourcePiDir, targetPiDir) {
	const sourceSettingsPath = path.join(sourcePiDir, "settings.json");
	const settings = relativizeSettingsForTarget(sourcePiDir, targetPiDir, readJsonFile(sourceSettingsPath));
	const requiredPackages = [
		relativizeResolvedPath(repoRoot, targetPiDir),
		relativizeResolvedPath(path.join(repoRoot, "node_modules", "pi-web-access"), targetPiDir),
	];

	settings.packages = dedupeByJson([
		...ensureArray(settings.packages),
		...requiredPackages,
	]);

	for (const key of RESOURCE_ARRAY_KEYS) {
		if (key in settings) {
			settings[key] = dedupeByJson(ensureArray(settings[key]));
		}
	}

	return settings;
}

function materializeRuntimeProjectPiDir(targetPiDir) {
	const repoPiDir = path.join(repoRoot, ".pi");
	removePath(targetPiDir);
	ensureDir(path.dirname(targetPiDir));
	fs.cpSync(repoPiDir, targetPiDir, { recursive: true, force: true });
	writeJsonFile(path.join(targetPiDir, "settings.json"), buildRuntimePiSettings(repoPiDir, targetPiDir));
	removePath(path.join(targetPiDir, "settings.json.lock"));
	return targetPiDir;
}

function ensureOrchestratorRuntimeProject(options) {
	const { containerDataRoot, targetDir } = options;
	const runtimeRoot = containerDataRoot ?? path.join(path.resolve(targetDir), ".astro-runtime");
	const configuredRuntimeCwd = process.env[ASTRO_ORCHESTRATOR_CWD_ENV]?.trim();
	const configuredProjectPiDir = process.env[ASTRO_ORCHESTRATOR_PROJECT_PI_DIR_ENV]?.trim();
	const runtimeCwd = path.resolve(
		configuredRuntimeCwd || path.join(runtimeRoot, "astro-orchestrator-runtime"),
	);
	const projectPiDir = path.resolve(
		configuredProjectPiDir || path.join(runtimeRoot, ".pi", "project"),
	);

	materializeRuntimeProjectPiDir(projectPiDir);
	ensureDir(runtimeCwd);
	const projectPiLink = path.join(runtimeCwd, ".pi");
	const projectPiLinkChanged = ensureSymlink(projectPiDir, projectPiLink);

	process.env[ASTRO_ORCHESTRATOR_CWD_ENV] = runtimeCwd;
	process.env[ASTRO_ORCHESTRATOR_PROJECT_PI_DIR_ENV] = projectPiDir;

	return {
		runtimeCwd,
		projectPiDir,
		projectPiLink,
		projectPiLinkChanged,
	};
}

function ensureRuntimeDirectoryLink(sourceDir, targetPath) {
	ensureDir(sourceDir);
	ensureDir(path.dirname(targetPath));

	let migratedExistingState = false;
	try {
		const existingStat = fs.lstatSync(targetPath);
		if (existingStat.isSymbolicLink() && fs.readlinkSync(targetPath) === sourceDir) {
			return {
				sourceDir,
				targetPath,
				migratedExistingState: false,
			};
		}
		if (!existingStat.isSymbolicLink()) {
			fs.cpSync(targetPath, sourceDir, { recursive: true, force: false, errorOnExist: false });
			migratedExistingState = true;
		}
		removePath(targetPath);
	} catch {
		// target does not exist yet
	}

	ensureSymlink(sourceDir, targetPath);
	return {
		sourceDir,
		targetPath,
		migratedExistingState,
	};
}

function ensurePiAgentHomeLink(homeDir, configuredAgentDir) {
	const targetPath = path.join(homeDir, ".pi", "agent");
	const sourceDir = path.resolve(configuredAgentDir);
	if (sourceDir === targetPath) {
		ensureDir(sourceDir);
		return {
			sourceDir,
			targetPath,
			migratedExistingState: false,
		};
	}
	return ensureRuntimeDirectoryLink(sourceDir, targetPath);
}

function ensureAstroStreamSessions(homeDir) {
	const configuredDir = process.env.ASTRO_STREAM_SESSION_DIR?.trim();
	if (!configuredDir) {
		return {
			sourceDir: null,
			targetPath: null,
			migratedExistingState: false,
		};
	}

	const sourceDir = path.resolve(configuredDir);
	const targetPath = path.join(homeDir, ".astro", "stream-sessions");
	if (sourceDir === targetPath) {
		ensureDir(sourceDir);
		return {
			sourceDir,
			targetPath,
			migratedExistingState: false,
		};
	}

	const link = ensureRuntimeDirectoryLink(sourceDir, targetPath);
	return link;
}

function ensureMainsequenceCliConfig(homeDir) {
	const configuredDir = process.env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim();
	if (!configuredDir) {
		return {
			configDir: null,
			targetPath: null,
			migratedExistingState: false,
		};
	}

	const sourceDir = path.resolve(configuredDir);
	const targetPath = path.join(homeDir, ".config", "mainsequence");
	ensureDir(sourceDir);
	ensureDir(path.dirname(targetPath));

	let migratedExistingState = false;
	try {
		const existingStat = fs.lstatSync(targetPath);
		if (!existingStat.isSymbolicLink()) {
			fs.cpSync(targetPath, sourceDir, { recursive: true, force: false, errorOnExist: false });
			migratedExistingState = true;
			removePath(targetPath);
		}
	} catch {
		// target does not exist yet
	}

	ensureSymlink(sourceDir, targetPath);
	return {
		configDir: sourceDir,
		targetPath,
		migratedExistingState,
	};
}

function ensureContainerDataRoots(homeDir) {
	const configuredRoot = process.env.ASTRO_CONTAINER_DATA_DIR?.trim();
	if (!configuredRoot) {
		return {
			rootDir: null,
			links: [],
		};
	}

	const rootDir = path.resolve(configuredRoot);
	ensureDir(rootDir);

	return {
		rootDir,
		links: [
			ensureRuntimeDirectoryLink(path.join(rootDir, ".ssh"), path.join(homeDir, ".ssh")),
			ensureRuntimeDirectoryLink(path.join(rootDir, "mainsequence"), path.join(homeDir, "mainsequence")),
			ensureRuntimeDirectoryLink(
				path.join(rootDir, "mainsequence-dev"),
				path.join(homeDir, "mainsequence-dev"),
			),
			ensureRuntimeDirectoryLink(path.join(rootDir, "uv"), path.join(homeDir, ".local", "share", "uv")),
		],
	};
}

function ensureRuntimeSshConfig(homeDir, rootDir) {
	const sshDir = rootDir ? path.join(rootDir, ".ssh") : path.join(homeDir, ".ssh");
	ensureDir(sshDir);

	const knownHostsPath = path.join(sshDir, "known_hosts");
	ensureFile(knownHostsPath, "", 0o600);

	const managedConfigPath = rootDir ? path.join(rootDir, ".ssh", "known_hosts") : knownHostsPath;
	const managedBlockStart = "# >>> astro-managed-runtime-ssh >>>";
	const managedBlockEnd = "# <<< astro-managed-runtime-ssh <<<";
	const managedBlock = [
		managedBlockStart,
		"Host *",
		"  StrictHostKeyChecking accept-new",
		`  UserKnownHostsFile ${managedConfigPath}`,
		"  IdentitiesOnly yes",
		managedBlockEnd,
	].join("\n");

	const configPath = path.join(sshDir, "config");
	const existingConfig = fs.existsSync(configPath) ? fs.readFileSync(configPath, "utf8") : "";
	const managedPattern = new RegExp(
		`${managedBlockStart}[\\s\\S]*?${managedBlockEnd}\\n?`,
		"g",
	);
	const strippedConfig = existingConfig.replace(managedPattern, "").trimEnd();
	const nextConfig =
		strippedConfig.length > 0 ? `${strippedConfig}\n\n${managedBlock}\n` : `${managedBlock}\n`;
	fs.writeFileSync(configPath, nextConfig, { mode: 0o600 });

	return {
		sshDir,
		knownHostsPath,
		configPath,
	};
}

function ensureManagedMainsequenceShim(targetDir) {
	const binDir = path.join(targetDir, "bin");
	ensureDir(binDir);
	const shimPath = path.join(binDir, "mainsequence");
	const shimContents = `#!/bin/sh
set -eu

REAL_MAINSEQUENCE="\${ASTRO_REAL_MAINSEQUENCE:-mainsequence}"

if [ "\${ASTRO_MAINSEQUENCE_BYPASS_SHIM:-0}" = "1" ]; then
  exec "$REAL_MAINSEQUENCE" "$@"
fi

exec "$REAL_MAINSEQUENCE" "$@"
`;
	fs.writeFileSync(shimPath, shimContents, { mode: 0o755 });
	return shimPath;
}

function lastNonEmptyLine(text) {
	return text
		.split(/\r?\n/)
		.map((line) => line.trim())
		.filter(Boolean)
		.at(-1) ?? "";
}

function resolveExecutableOnPath(command, pathValue, excludedDir = null) {
	const normalizedExcludedDir = excludedDir ? path.resolve(excludedDir) : null;
	for (const entry of (pathValue || "").split(path.delimiter)) {
		const trimmed = entry.trim();
		if (!trimmed) continue;
		const resolvedEntry = path.resolve(trimmed);
		if (normalizedExcludedDir && resolvedEntry === normalizedExcludedDir) continue;
		const candidate = path.join(resolvedEntry, command);
		if (!fs.existsSync(candidate)) continue;
		try {
			if (!fs.statSync(candidate).isFile()) continue;
		} catch {
			continue;
		}
		return candidate;
	}
	return null;
}

function uniqueExistingDirs(paths) {
	const seen = new Set();
	const dirs = [];
	for (const candidate of paths) {
		if (!candidate) continue;
		const resolved = path.resolve(candidate);
		if (seen.has(resolved)) continue;
		seen.add(resolved);
		try {
			if (fs.statSync(resolved).isDirectory()) dirs.push(resolved);
		} catch {
			// Candidate does not exist in this image.
		}
	}
	return dirs;
}

function collectPythonSitePackageDirs() {
	const libRoots = uniqueExistingDirs([
		process.env.VIRTUAL_ENV ? path.join(process.env.VIRTUAL_ENV, "lib") : null,
		"/usr/local/lib",
		"/usr/lib",
		"/opt/venv/lib",
		"/app/.venv/lib",
	]);
	const candidates = [];
	for (const libRoot of libRoots) {
		try {
			for (const entry of fs.readdirSync(libRoot, { withFileTypes: true })) {
				if (!entry.isDirectory() || !entry.name.startsWith("python")) continue;
				candidates.push(path.join(libRoot, entry.name, "site-packages"));
			}
		} catch {
			// Ignore unreadable roots.
		}
	}
	return uniqueExistingDirs(candidates);
}

function normalizeSkillSource(candidate) {
	if (!candidate) return null;
	const resolved = path.resolve(candidate);
	let stat;
	try {
		stat = fs.statSync(resolved);
	} catch {
		return null;
	}
	const sourceDir = stat.isDirectory() ? resolved : path.dirname(resolved);
	const skillFilePath = stat.isDirectory() ? path.join(sourceDir, "SKILL.md") : resolved;
	if (!fs.existsSync(skillFilePath)) return null;
	return {
		sourcePath: skillFilePath,
		sourceDir,
		skillFilePath,
	};
}

function collectMainsequenceSkillSourceCandidates({ slug, envVarName, targetPath }) {
	const explicitSkillRoots = uniqueExistingDirs([
		process.env.ASTRO_MAINSEQUENCE_SKILLS_ROOT,
		process.env.MAINSEQUENCE_SKILLS_ROOT,
	]);
	const candidates = [
		process.env[envVarName],
		targetPath,
		...explicitSkillRoots.map((root) => path.join(root, ...slug.split("/"))),
		...collectPythonSitePackageDirs().map((sitePackages) =>
			path.join(sitePackages, "agent_scaffold", "skills", ...slug.split("/")),
		),
	];
	const seen = new Set();
	return candidates.filter((candidate) => {
		if (!candidate) return false;
		const resolved = path.resolve(candidate);
		if (seen.has(resolved)) return false;
		seen.add(resolved);
		return true;
	});
}

function buildWorkspaceAnalysisBootstrapEnv(targetDir, runtimeCwd) {
	const env = { ...process.env };
	const existingPath = env.PATH ?? "";
	const managedBinDir = path.join(path.resolve(targetDir), "bin");
	const realMainsequence =
		env.ASTRO_REAL_MAINSEQUENCE ??
		resolveExecutableOnPath("mainsequence", existingPath, managedBinDir) ??
		"mainsequence";
	env.PATH = [managedBinDir, existingPath].filter(Boolean).join(path.delimiter);
	env.PI_CODING_AGENT_DIR = path.resolve(targetDir);
	env.PWD = runtimeCwd;
	env.ASTRO_REAL_MAINSEQUENCE = realMainsequence;
	return env;
}

function materializeMainsequenceSkill(options) {
	const { targetDir, runtimeCwd, projectPiDir, slug, envVarName, label } = options;
	const targetPath = path.join(
		projectPiDir,
		"skills",
		...slug.split("/"),
	);
	const env = buildWorkspaceAnalysisBootstrapEnv(targetDir, runtimeCwd);
	const command = env.ASTRO_REAL_MAINSEQUENCE || "mainsequence";
	const args = ["skills", "path", slug];
	const startedAt = Date.now();
	const result = spawnSync(command, args, {
		cwd: runtimeCwd,
		shell: false,
		env,
		encoding: "utf8",
		timeout: MAINSEQUENCE_SKILL_BOOTSTRAP_TIMEOUT_MS,
	});
	const cliDurationMs = Date.now() - startedAt;
	const commandText = [command, ...args].join(" ");
	let cliFailureReason = null;

	if (result.error) {
		cliFailureReason =
			result.error.code === "ETIMEDOUT"
				? `${label} skill bootstrap timed out after ${MAINSEQUENCE_SKILL_BOOTSTRAP_TIMEOUT_MS}ms while running: ${commandText}`
				: result.error.code === "ENOENT"
				? `Missing required command while materializing ${label} skill: ${command}`
				: `${label} skill bootstrap failed: ${result.error.message}`;
	} else if ((result.status ?? 1) !== 0) {
		const stdout = typeof result.stdout === "string" ? result.stdout : "";
		const stderr = typeof result.stderr === "string" ? result.stderr : "";
		const fragments = [
			`${label} skill bootstrap failed while running: ${commandText}`,
			`cwd=${runtimeCwd}`,
			`exit_code=${result.status ?? "unknown"}`,
		];
		if (stderr.trim()) fragments.push(`stderr=${stderr.trim()}`);
		if (stdout.trim()) fragments.push(`stdout=${stdout.trim()}`);
		cliFailureReason = fragments.join(" | ");
	} else {
		const stdout = typeof result.stdout === "string" ? result.stdout : "";
		const resolvedSourcePathText = lastNonEmptyLine(stdout);
		if (!resolvedSourcePathText) {
			cliFailureReason =
				`${label} skill bootstrap failed: ${commandText} returned no usable path.`;
		} else {
			const resolvedSourcePath = path.resolve(resolvedSourcePathText);
			const resolvedSource = normalizeSkillSource(resolvedSourcePath);
			if (!resolvedSource) {
				cliFailureReason =
					`${label} skill bootstrap failed: resolved source path does not exist or is missing SKILL.md: ${resolvedSourcePath}`;
			} else {
				removePath(targetPath);
				ensureDir(path.dirname(targetPath));
				fs.cpSync(resolvedSource.sourceDir, targetPath, { recursive: true, force: true });

				const skillFilePath = path.join(targetPath, "SKILL.md");
				if (!fs.existsSync(skillFilePath)) {
					cliFailureReason =
						`${label} skill bootstrap failed: copied skill is missing SKILL.md at ${skillFilePath}`;
				} else {
					process.env[envVarName] = targetPath;
					return {
						slug,
						command: commandText,
						cwd: runtimeCwd,
						sourcePath: resolvedSource.sourcePath,
						sourceDir: resolvedSource.sourceDir,
						targetPath,
						skillFilePath,
						resolution: "mainsequence_cli",
						durationMs: Date.now() - startedAt,
						cliDurationMs,
					};
				}
			}
		}
	}

	for (const candidate of collectMainsequenceSkillSourceCandidates({ slug, envVarName, targetPath })) {
		const resolved = normalizeSkillSource(candidate);
		if (!resolved) continue;
		if (path.resolve(resolved.sourceDir) !== path.resolve(targetPath)) {
			removePath(targetPath);
			ensureDir(path.dirname(targetPath));
			fs.cpSync(resolved.sourceDir, targetPath, { recursive: true, force: true });
		}
		const skillFilePath = path.join(targetPath, "SKILL.md");
		if (!fs.existsSync(skillFilePath)) continue;
		process.env[envVarName] = targetPath;
		return {
			slug,
			command: commandText,
			cwd: runtimeCwd,
			sourcePath: resolved.sourcePath,
			sourceDir: resolved.sourceDir,
			targetPath,
			skillFilePath,
			resolution: path.resolve(resolved.sourceDir) === path.resolve(targetPath)
				? "existing_target_fallback"
				: "direct_source_fallback",
			fallbackReason: cliFailureReason,
			durationMs: Date.now() - startedAt,
			cliDurationMs,
		};
	}

	throw new Error(
		cliFailureReason ??
			`${label} skill bootstrap failed: no usable source found for ${slug}.`,
	);
}

export function bootstrapPiAgentDir() {
	const homeDir = process.env.HOME || os.homedir();
	const targetDir = process.env.PI_CODING_AGENT_DIR || path.join(homeDir, ".pi", "agent");
	const containerData = ensureContainerDataRoots(homeDir);

	ensureDir(targetDir);
	const prunedProviderAuthEntries = pruneContainerProviderAuthState(path.resolve(targetDir));
	const prunedScopedProviderCredentialDir = pruneScopedProviderCredentialState();
	ensureDir(path.join(targetDir, "bin"));
	const mainsequenceShimPath = ensureManagedMainsequenceShim(targetDir);
	const runtimeSettingsPath = ensureRuntimeSettings(targetDir);
	const orchestratorRuntime = ensureOrchestratorRuntimeProject({
		containerDataRoot: containerData.rootDir,
		targetDir,
	});
	const mainsequenceCliConfig = ensureMainsequenceCliConfig(homeDir);
	const piAgentHomeLink = ensurePiAgentHomeLink(homeDir, targetDir);
	const streamSessions = ensureAstroStreamSessions(homeDir);
	const sshRuntime = ensureRuntimeSshConfig(homeDir, containerData.rootDir);
	const workspaceAnalysisSkill = materializeMainsequenceSkill({
		targetDir,
		runtimeCwd: orchestratorRuntime.runtimeCwd,
		projectPiDir: orchestratorRuntime.projectPiDir,
		slug: WORKSPACE_ANALYSIS_SKILL_SLUG,
		envVarName: ASTRO_WORKSPACE_ANALYSIS_SKILL_PATH_ENV,
		label: "Workspace-analysis",
	});
	const a2aCommunicationSkill = materializeMainsequenceSkill({
		targetDir,
		runtimeCwd: orchestratorRuntime.runtimeCwd,
		projectPiDir: orchestratorRuntime.projectPiDir,
		slug: A2A_COMMUNICATION_SKILL_SLUG,
		envVarName: ASTRO_A2A_COMMUNICATION_SKILL_PATH_ENV,
		label: "A2A communication",
	});

	return {
		targetDir,
		runtimeSettingsPath,
		orchestratorRuntime,
		containerData,
		mainsequenceCliConfig,
		piAgentHomeLink,
		streamSessions,
		sshRuntime,
		mainsequenceShimPath,
		workspaceAnalysisSkill,
		a2aCommunicationSkill,
		prunedProviderAuthEntries,
		prunedScopedProviderCredentialDir,
	};
}
