import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const SAFE_HOST_ENTRIES = ["auth.json", "sessions"];
const SAFE_LEGACY_REPO_PI_AGENT_ENTRIES = [
	"settings.json",
	"bin",
	"astro-model-provider-signin.json",
	"astro-model-provider-auth.json",
];
const RESOURCE_ARRAY_KEYS = ["extensions", "skills", "prompts", "themes"];
const PATH_BASED_SETTINGS_KEYS = ["packages", ...RESOURCE_ARRAY_KEYS];
const PVC_LAYOUT_MIGRATION_VERSION = "pvc-layout-v1";

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

function resolveOptionalPath(value) {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	if (!trimmed) return null;
	return path.resolve(trimmed);
}

function resolveExistingPath(value) {
	try {
		return fs.realpathSync(value);
	} catch {
		return null;
	}
}

function isSameOrNestedPath(parentPath, childPath) {
	const relativePath = path.relative(parentPath, childPath);
	return relativePath === "" || (!relativePath.startsWith("..") && !path.isAbsolute(relativePath));
}

function ensureRuntimeSettings(targetDir, hostImportDir) {
	const targetSettingsPath = path.join(targetDir, "settings.json");
	const repoPiDir = path.join(repoRoot, ".pi");
	const repoSettingsPath = path.join(repoPiDir, "settings.json");
	const hostSettingsPath = hostImportDir ? path.join(hostImportDir, "settings.json") : null;

	const runtimeSettings = omitPathBasedSettings(readJsonFile(targetSettingsPath));
	const hostSettings = hostSettingsPath ? omitPathBasedSettings(readJsonFile(hostSettingsPath)) : {};
	const repoSettings = relativizeSettingsForTarget(repoPiDir, targetDir, readJsonFile(repoSettingsPath));
	const requiredPackages = [
		relativizeResolvedPath(repoRoot, targetDir),
		relativizeResolvedPath(path.join(repoRoot, "node_modules", "pi-web-access"), targetDir),
	];

	const mergedSettings = mergeSettings(hostSettings, runtimeSettings);
	const finalSettings = mergeSettings(mergedSettings, repoSettings);
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

function ensurePersistentDirectoryLink(sourceDir, targetPath) {
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

function copyIntoIfExists(sourcePath, targetPath) {
	if (!fs.existsSync(sourcePath)) return false;
	const sourceLstat = fs.lstatSync(sourcePath);
	const resolvedSourcePath = resolveExistingPath(sourcePath) ?? path.resolve(sourcePath);
	const sourceStat = fs.statSync(resolvedSourcePath);
	const resolvedTargetPath = resolveExistingPath(targetPath) ?? path.resolve(targetPath);

	if (resolvedSourcePath === resolvedTargetPath) {
		return false;
	}
	if (sourceStat.isDirectory() && isSameOrNestedPath(resolvedSourcePath, resolvedTargetPath)) {
		return false;
	}

	ensureDir(path.dirname(targetPath));
	if (sourceStat.isDirectory()) {
		ensureDir(path.dirname(targetPath));
		fs.cpSync(resolvedSourcePath, targetPath, { recursive: true, force: false, errorOnExist: false });
		return true;
	}
	if (!fs.existsSync(targetPath)) {
		const copySourcePath =
			sourceLstat.isSymbolicLink() || resolvedSourcePath !== path.resolve(sourcePath)
				? resolvedSourcePath
				: sourcePath;
		fs.cpSync(copySourcePath, targetPath, { force: false, errorOnExist: false });
		return true;
	}
	return false;
}

function materializeLegacyHostEntry(targetDir, legacyHostPiAgentDir, entry) {
	if (!legacyHostPiAgentDir) return false;

	const sourcePath = path.join(legacyHostPiAgentDir, entry);
	if (!fs.existsSync(sourcePath)) return false;

	const targetPath = path.join(targetDir, entry);
	try {
		const targetLstat = fs.lstatSync(targetPath);
		if (!targetLstat.isSymbolicLink()) {
			return false;
		}
	} catch {
		return false;
	}

	const resolvedSourcePath = resolveExistingPath(sourcePath) ?? path.resolve(sourcePath);
	const resolvedTargetPath = resolveExistingPath(targetPath) ?? path.resolve(targetPath);
	const shouldReplace =
		resolvedTargetPath === resolvedSourcePath ||
		isSameOrNestedPath(legacyHostPiAgentDir, resolvedTargetPath);

	if (!shouldReplace) return false;

	removePath(targetPath);
	const sourceStat = fs.statSync(resolvedSourcePath);
	if (sourceStat.isDirectory()) {
		fs.cpSync(resolvedSourcePath, targetPath, { recursive: true, force: false, errorOnExist: false });
		return true;
	}

	ensureDir(path.dirname(targetPath));
	fs.cpSync(resolvedSourcePath, targetPath, { force: false, errorOnExist: false });
	return true;
}

function materializeLegacyHostImports(targetDir, legacyHostPiAgentDir) {
	if (!legacyHostPiAgentDir || !fs.existsSync(legacyHostPiAgentDir)) {
		return [];
	}

	const materializedEntries = [];
	for (const entry of SAFE_HOST_ENTRIES) {
		if (materializeLegacyHostEntry(targetDir, legacyHostPiAgentDir, entry)) {
			materializedEntries.push(entry);
		}
	}
	return materializedEntries;
}

function resolveVolumeMigrationPaths(rootDir) {
	if (!rootDir) return null;
	return {
		markerPath: path.join(rootDir, ".astro", "migrations", `${PVC_LAYOUT_MIGRATION_VERSION}.json`),
		piAgentDir: path.join(rootDir, ".pi", "agent"),
		mainsequenceConfigDir: path.join(rootDir, ".config", "mainsequence"),
		streamSessionDir: path.join(rootDir, ".astro", "stream-sessions"),
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
	return ensurePersistentDirectoryLink(sourceDir, targetPath);
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

	const link = ensurePersistentDirectoryLink(sourceDir, targetPath);
	return link;
}

function performOneTimeVolumeMigration(options) {
	const {
		rootDir,
		targetDir,
		mainsequenceConfigDir,
		streamSessionDir,
		legacyRepoStateDir,
		legacyHostPiAgentDir,
	} = options;
	const paths = resolveVolumeMigrationPaths(rootDir);
	if (!paths) {
		return {
			performed: false,
			markerPath: null,
			importedEntries: [],
			hostSettingsSourceDir: null,
		};
	}

	if (fs.existsSync(paths.markerPath)) {
		return {
			performed: false,
			markerPath: paths.markerPath,
			importedEntries: [],
			hostSettingsSourceDir: null,
		};
	}

	const importedEntries = [];
	const resolvedLegacyRepoStateDir = resolveOptionalPath(legacyRepoStateDir);
	const resolvedLegacyHostPiAgentDir = resolveOptionalPath(legacyHostPiAgentDir);
	const resolvedTargetDir = resolveExistingPath(targetDir) ?? path.resolve(targetDir);

	if (resolvedLegacyRepoStateDir && fs.existsSync(resolvedLegacyRepoStateDir)) {
		const legacyRepoPiAgentDir = path.join(resolvedLegacyRepoStateDir, "pi-agent-runtime");
		for (const entry of SAFE_LEGACY_REPO_PI_AGENT_ENTRIES) {
			if (copyIntoIfExists(path.join(legacyRepoPiAgentDir, entry), path.join(targetDir, entry))) {
				importedEntries.push(`legacy_repo_pi_agent_${entry}`);
			}
		}
		if (copyIntoIfExists(path.join(resolvedLegacyRepoStateDir, "mainsequence-config"), mainsequenceConfigDir)) {
			importedEntries.push("legacy_repo_mainsequence_config");
		}
		if (copyIntoIfExists(path.join(resolvedLegacyRepoStateDir, "stream-sessions"), streamSessionDir)) {
			importedEntries.push("legacy_repo_stream_sessions");
		}
	}

	const resolvedLegacyHostPiAgentRealPath =
		resolvedLegacyHostPiAgentDir && fs.existsSync(resolvedLegacyHostPiAgentDir)
			? resolveExistingPath(resolvedLegacyHostPiAgentDir) ?? resolvedLegacyHostPiAgentDir
			: null;

	if (
		resolvedLegacyHostPiAgentRealPath &&
		fs.existsSync(resolvedLegacyHostPiAgentDir) &&
		resolvedLegacyHostPiAgentRealPath !== resolvedTargetDir
	) {
		for (const entry of SAFE_HOST_ENTRIES) {
			if (copyIntoIfExists(path.join(resolvedLegacyHostPiAgentDir, entry), path.join(targetDir, entry))) {
				importedEntries.push(`legacy_host_pi_${entry}`);
			}
		}
	}

	writeJsonFile(paths.markerPath, {
		version: PVC_LAYOUT_MIGRATION_VERSION,
		performedAt: new Date().toISOString(),
		importedEntries,
		legacyRepoStateDir: resolvedLegacyRepoStateDir,
		legacyHostPiAgentDir: resolvedLegacyHostPiAgentDir,
		targets: {
			piAgentDir: targetDir,
			mainsequenceConfigDir,
			streamSessionDir,
		},
	});

	return {
		performed: true,
		markerPath: paths.markerPath,
		importedEntries,
		hostSettingsSourceDir:
			resolvedLegacyHostPiAgentDir && fs.existsSync(resolvedLegacyHostPiAgentDir)
				? resolvedLegacyHostPiAgentDir
				: null,
	};
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
			ensurePersistentDirectoryLink(path.join(rootDir, ".ssh"), path.join(homeDir, ".ssh")),
			ensurePersistentDirectoryLink(path.join(rootDir, "mainsequence"), path.join(homeDir, "mainsequence")),
			ensurePersistentDirectoryLink(
				path.join(rootDir, "mainsequence-dev"),
				path.join(homeDir, "mainsequence-dev"),
			),
			ensurePersistentDirectoryLink(path.join(rootDir, "uv"), path.join(homeDir, ".local", "share", "uv")),
		],
	};
}

function ensurePersistentSshRuntime(homeDir, rootDir) {
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

if [ "\${1:-}" = "project" ] && [ "\${2:-}" = "set-up-locally" ]; then
  shift 2
  exec tsx "/app/scripts/mainsequence_project_set_up_locally.ts" "$@"
fi

exec "$REAL_MAINSEQUENCE" "$@"
`;
	fs.writeFileSync(shimPath, shimContents, { mode: 0o755 });
	return shimPath;
}

export function bootstrapPiAgentDir() {
	const homeDir = process.env.HOME || os.homedir();
	const targetDir = process.env.PI_CODING_AGENT_DIR || path.join(homeDir, ".pi", "agent");
	const containerData = ensureContainerDataRoots(homeDir);
	const configuredMainsequenceDir =
		process.env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim() || path.join(homeDir, ".config", "mainsequence");
	const configuredStreamSessionDir =
		process.env.ASTRO_STREAM_SESSION_DIR?.trim() || path.join(homeDir, ".astro", "stream-sessions");
	const migration = performOneTimeVolumeMigration({
		rootDir: containerData.rootDir,
		targetDir: path.resolve(targetDir),
		mainsequenceConfigDir: path.resolve(configuredMainsequenceDir),
		streamSessionDir: path.resolve(configuredStreamSessionDir),
		legacyRepoStateDir: process.env.ASTRO_LEGACY_REPO_STATE_DIR,
		legacyHostPiAgentDir:
			process.env.ASTRO_LEGACY_HOST_PI_AGENT_DIR ?? process.env.ASTRO_PI_HOST_AGENT_IMPORT_DIR,
	});

	ensureDir(targetDir);
	const materializedLegacyHostEntries = materializeLegacyHostImports(
		path.resolve(targetDir),
		process.env.ASTRO_LEGACY_HOST_PI_AGENT_DIR ?? process.env.ASTRO_PI_HOST_AGENT_IMPORT_DIR ?? null,
	);
	ensureDir(path.join(targetDir, "bin"));
	const mainsequenceShimPath = ensureManagedMainsequenceShim(targetDir);
	const runtimeSettingsPath = ensureRuntimeSettings(targetDir, migration.hostSettingsSourceDir);
	const mainsequenceCliConfig = ensureMainsequenceCliConfig(homeDir);
	const piAgentHomeLink = ensurePiAgentHomeLink(homeDir, targetDir);
	const streamSessions = ensureAstroStreamSessions(homeDir);
	const sshRuntime = ensurePersistentSshRuntime(homeDir, containerData.rootDir);

	return {
		targetDir,
		runtimeSettingsPath,
		containerData,
		mainsequenceCliConfig,
		piAgentHomeLink,
		streamSessions,
		sshRuntime,
		mainsequenceShimPath,
		migration,
		materializedLegacyHostEntries,
	};
}
