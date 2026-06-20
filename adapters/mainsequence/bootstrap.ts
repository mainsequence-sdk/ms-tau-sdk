import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND_ENV = "MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND";

function ensureDir(dirPath: string) {
	fs.mkdirSync(dirPath, { recursive: true, mode: 0o700 });
}

function removePath(targetPath: string) {
	try {
		fs.rmSync(targetPath, { recursive: true, force: true });
	} catch {
		// ignore cleanup failures
	}
}

function ensureSymlink(sourcePath: string, targetPath: string) {
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

function ensureMainsequenceCliConfig(homeDir: string, env: NodeJS.ProcessEnv = process.env) {
	const configuredDir = env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim();
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

function ensureManagedMainsequenceShim(targetDir: string) {
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

function ensureRuntimeDirectoryLink(sourceDir: string, targetPath: string) {
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

function ensureMainsequenceWorkspaceLinks(input: {
	homeDir: string;
	containerDataRoot?: string | null;
}) {
	if (!input.containerDataRoot) return [];
	const rootDir = path.resolve(input.containerDataRoot);
	return [
		ensureRuntimeDirectoryLink(path.join(rootDir, "mainsequence"), path.join(input.homeDir, "mainsequence")),
		ensureRuntimeDirectoryLink(
			path.join(rootDir, "mainsequence-dev"),
			path.join(input.homeDir, "mainsequence-dev"),
		),
	];
}

function shellQuote(value: string) {
	return `'${String(value).replace(/'/g, "'\\''")}'`;
}

function ensureMainsequenceCliAuthRepairCommand(input: {
	env?: NodeJS.ProcessEnv;
	repoRoot?: string;
}) {
	const env = input.env ?? process.env;
	const configured = env[MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND_ENV]?.trim();
	if (configured) return configured;

	const resolvedRepoRoot = input.repoRoot ?? repoRoot;
	const tsxBin = path.join(resolvedRepoRoot, "node_modules", ".bin", process.platform === "win32" ? "tsx.cmd" : "tsx");
	const repairScript = path.join(resolvedRepoRoot, "adapters", "mainsequence", "bin", "cli-auth-repair.ts");
	const command = `${shellQuote(tsxBin)} ${shellQuote(repairScript)}`;
	env[MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND_ENV] = command;
	return command;
}

export function prepareMainsequenceBootstrap(options: {
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
	repoRoot?: string;
	piAgentDir?: string;
	homeDir?: string;
	containerDataRoot?: string | null;
} = {}) {
	const env = options.env ?? process.env;
	const homeDir = options.homeDir ?? env.HOME?.trim() ?? os.homedir();
	const piAgentDir = path.resolve(
		options.piAgentDir ??
			env.PI_CODING_AGENT_DIR?.trim() ??
			path.join(homeDir, ".pi", "agent"),
	);

	const mainsequenceCliConfig = ensureMainsequenceCliConfig(homeDir, env);
	const mainsequenceShimPath = ensureManagedMainsequenceShim(piAgentDir);
	const mainsequenceCliAuthRepairCommand = ensureMainsequenceCliAuthRepairCommand({
		env,
		repoRoot: options.repoRoot,
	});
	const mainsequenceWorkspaceLinks = ensureMainsequenceWorkspaceLinks({
		homeDir,
		containerDataRoot: options.containerDataRoot,
	});

	options.log?.("Main Sequence runtime bootstrap is ready.");

	return {
		mainsequenceCliConfig,
		mainsequenceShimPath,
		mainsequenceCliAuthRepairCommand,
		mainsequenceWorkspaceLinks,
	};
}
