import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const RESOURCE_ARRAY_KEYS = ["extensions", "skills", "prompts", "themes"];
const PATH_BASED_SETTINGS_KEYS = ["packages", ...RESOURCE_ARRAY_KEYS];
const CONTAINER_PROVIDER_AUTH_ENTRIES = [
	"auth.json",
	"sessions",
	"astro-model-provider-auth.json",
	"astro-model-provider-signin.json",
];
const ASTRO_RUNTIME_CWD_ENV = "ASTRO_RUNTIME_CWD";
const ASTRO_RUNTIME_PROJECT_PI_DIR_ENV = "ASTRO_RUNTIME_PROJECT_PI_DIR";
const ASTRO_ORCHESTRATOR_CWD_ENV = "ASTRO_ORCHESTRATOR_CWD";
const ASTRO_ORCHESTRATOR_PROJECT_PI_DIR_ENV = "ASTRO_ORCHESTRATOR_PROJECT_PI_DIR";
const ASTRO_PI_PACKAGE_PATHS_ENV = "ASTRO_PI_PACKAGE_PATHS";
const MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND_ENV = "MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND";

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

function parseConfiguredPiPackagePaths(env = process.env) {
	const rawValue = env[ASTRO_PI_PACKAGE_PATHS_ENV]?.trim();
	if (!rawValue) return [];

	if (rawValue.startsWith("[")) {
		try {
			const parsed = JSON.parse(rawValue);
			if (Array.isArray(parsed)) {
				return parsed
					.filter((entry) => typeof entry === "string")
					.map((entry) => entry.trim())
					.filter(Boolean);
			}
		} catch {
			// Fall back to the simple delimited format below.
		}
	}

	return rawValue
		.split(/[,\n]/)
		.map((entry) => entry.trim())
		.filter(Boolean);
}

function resolveConfiguredPackagePath(source, baseDir = repoRoot) {
	if (!isLikelyLocalSource(source)) return source;
	return resolveSettingPath(source, baseDir);
}

function relativizeConfiguredPackagePath(source, targetDir) {
	const resolved = resolveConfiguredPackagePath(source);
	return isLikelyLocalSource(source) ? relativizeResolvedPath(resolved, targetDir) : source;
}

function configuredPiPackagesForTarget(targetDir, env = process.env) {
	return dedupeByJson(
		parseConfiguredPiPackagePaths(env).map((entry) => relativizeConfiguredPackagePath(entry, targetDir)),
	);
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
	const configuredPackages = configuredPiPackagesForTarget(targetDir);

	const finalSettings = mergeSettings(runtimeSettings, repoSettings);
	finalSettings.packages = dedupeByJson([
		...ensureArray(repoSettings.packages),
		...requiredPackages,
		...configuredPackages,
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
	const configuredPackages = configuredPiPackagesForTarget(targetPiDir);

	settings.packages = dedupeByJson([
		...ensureArray(settings.packages),
		...requiredPackages,
		...configuredPackages,
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
	fs.writeFileSync(path.join(targetPiDir, "APPEND_SYSTEM.md"), buildRuntimeAppendSystem(repoPiDir), {
		mode: 0o600,
	});
	removePath(path.join(targetPiDir, "settings.json.lock"));
	return targetPiDir;
}

function defaultRuntimeCwd(runtimeRoot) {
	const neutral = path.join(runtimeRoot, "astro-runtime");
	const legacy = path.join(runtimeRoot, "astro-orchestrator-runtime");
	return fs.existsSync(legacy) && !fs.existsSync(neutral) ? legacy : neutral;
}

function ensureDefaultRuntimeProject(options) {
	const { containerDataRoot, targetDir } = options;
	const runtimeRoot = containerDataRoot ?? path.join(path.resolve(targetDir), ".astro-runtime");
	const configuredRuntimeCwd =
		process.env[ASTRO_RUNTIME_CWD_ENV]?.trim() ||
		process.env[ASTRO_ORCHESTRATOR_CWD_ENV]?.trim();
	const configuredProjectPiDir =
		process.env[ASTRO_RUNTIME_PROJECT_PI_DIR_ENV]?.trim() ||
		process.env[ASTRO_ORCHESTRATOR_PROJECT_PI_DIR_ENV]?.trim();
	const runtimeCwd = path.resolve(
		configuredRuntimeCwd || defaultRuntimeCwd(runtimeRoot),
	);
	const projectPiDir = path.resolve(
		configuredProjectPiDir || path.join(runtimeRoot, ".pi", "project"),
	);

	materializeRuntimeProjectPiDir(projectPiDir);
	ensureDir(runtimeCwd);
	const projectPiLink = path.join(runtimeCwd, ".pi");
	const projectPiLinkChanged = ensureSymlink(projectPiDir, projectPiLink);

	process.env[ASTRO_RUNTIME_CWD_ENV] = runtimeCwd;
	process.env[ASTRO_RUNTIME_PROJECT_PI_DIR_ENV] = projectPiDir;
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

function shellQuote(value) {
	return `'${String(value).replace(/'/g, "'\\''")}'`;
}

function ensureMainsequenceCliAuthRepairCommand() {
	const configured = process.env[MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND_ENV]?.trim();
	if (configured) return configured;

	const tsxBin = path.join(repoRoot, "node_modules", ".bin", process.platform === "win32" ? "tsx.cmd" : "tsx");
	const repairScript = path.join(repoRoot, "adapters", "mainsequence", "bin", "cli-auth-repair.ts");
	const command = `${shellQuote(tsxBin)} ${shellQuote(repairScript)}`;
	process.env[MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND_ENV] = command;
	return command;
}

function readTextFileIfPresent(filePath) {
	try {
		if (!fs.existsSync(filePath)) return null;
		const contents = fs.readFileSync(filePath, "utf8");
		return contents.trim() ? contents.trim() : null;
	} catch {
		return null;
	}
}

function configuredPackageAppendSystemSources(env = process.env) {
	const sources = [];
	for (const source of parseConfiguredPiPackagePaths(env)) {
		if (!isLikelyLocalSource(source)) continue;
		const packageDir = path.resolve(resolveConfiguredPackagePath(source));
		const candidates = [
			path.join(packageDir, ".pi", "APPEND_SYSTEM.md"),
			path.join(packageDir, "pi", "APPEND_SYSTEM.md"),
			path.join(packageDir, "pi", "system", "APPEND_SYSTEM.md"),
		];
		for (const candidate of candidates) {
			const contents = readTextFileIfPresent(candidate);
			if (!contents) continue;
			sources.push({
				source,
				path: candidate,
				contents,
			});
			break;
		}
	}
	return sources;
}

function buildRuntimeAppendSystem(sourcePiDir) {
	const basePrompt = readTextFileIfPresent(path.join(sourcePiDir, "APPEND_SYSTEM.md"));
	const packagePrompts = configuredPackageAppendSystemSources();
	const sections = [];
	if (basePrompt) sections.push(basePrompt);
	for (const prompt of packagePrompts) {
		sections.push([
			`# Package Runtime Contract: ${prompt.source}`,
			prompt.contents,
		].join("\n\n"));
	}
	return `${sections.join("\n\n")}\n`;
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
	const mainsequenceCliAuthRepairCommand = ensureMainsequenceCliAuthRepairCommand();
	const runtimeSettingsPath = ensureRuntimeSettings(targetDir);
	const configuredPiPackages = parseConfiguredPiPackagePaths();
	const runtimeProject = ensureDefaultRuntimeProject({
		containerDataRoot: containerData.rootDir,
		targetDir,
	});
	const mainsequenceCliConfig = ensureMainsequenceCliConfig(homeDir);
	const piAgentHomeLink = ensurePiAgentHomeLink(homeDir, targetDir);
	const streamSessions = ensureAstroStreamSessions(homeDir);
	const sshRuntime = ensureRuntimeSshConfig(homeDir, containerData.rootDir);

	return {
		targetDir,
		runtimeSettingsPath,
		runtimeProject,
		orchestratorRuntime: runtimeProject,
		containerData,
		mainsequenceCliConfig,
		piAgentHomeLink,
		streamSessions,
		sshRuntime,
		mainsequenceShimPath,
		mainsequenceCliAuthRepairCommand,
		configuredPiPackages,
		prunedProviderAuthEntries,
		prunedScopedProviderCredentialDir,
	};
}
