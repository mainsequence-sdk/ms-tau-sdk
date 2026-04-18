import { spawnSync } from "node:child_process";
import path from "node:path";
import {
	existsSync,
	lstatSync,
	mkdirSync,
	readlinkSync,
	rmSync,
	statSync,
	symlinkSync,
	writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { buildMainsequenceStoredAuthEnv } from "../../../scripts/mainsequence_runtime_auth.js";

export type ProjectRuntimeSnapshot = {
	checkedAt: string;
	projectCwd: string;
	agentsFilePresent: boolean;
	agentsFileMissing: boolean;
	sdkStatus: {
		raw: unknown | null;
		currentVersion: string | null;
		latestVersion: string | null;
		updateRecommended: boolean;
	};
	venv: {
		path: string;
		binPath: string;
		pythonPath: string;
		mainsequenceVersion: string | null;
	};
};

export type ProjectRuntimeBootstrapStep =
	| "sdk_status"
	| "build_local_venv"
	| "resolve_venv"
	| "activate_venv"
	| "uv_sync"
	| "read_venv_mainsequence";

export type ProjectRuntimeBootstrapResult =
	| {
			ok: true;
			snapshot: ProjectRuntimeSnapshot;
	  }
	| {
			ok: false;
			step: ProjectRuntimeBootstrapStep;
			error: string;
			exitCode: number | null;
			stdout: string;
			stderr: string;
	  };

export type ProjectRuntimeBootstrapEvent =
	| {
			phase: "start";
			step: ProjectRuntimeBootstrapStep;
			command: string | null;
			cwd: string;
			details?: Record<string, unknown>;
	  }
	| {
			phase: "success";
			step: ProjectRuntimeBootstrapStep;
			command: string | null;
			cwd: string;
			summary: string;
			exitCode: number | null;
			stdout: string;
			stderr: string;
			details?: Record<string, unknown>;
	  }
	| {
			phase: "failure";
			step: ProjectRuntimeBootstrapStep;
			command: string | null;
			cwd: string;
			summary: string;
			error: string;
			exitCode: number | null;
			stdout: string;
			stderr: string;
			details?: Record<string, unknown>;
	  };

type CommandResult =
	| {
			ok: true;
			exitCode: number | null;
			stdout: string;
			stderr: string;
	  }
	| {
			ok: false;
			exitCode: number | null;
			stdout: string;
			stderr: string;
			error: string;
	  };

type CommandFailure = Extract<CommandResult, { ok: false }>;

type BootstrapOptions = {
	cwd: string;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
	onEvent?: (event: ProjectRuntimeBootstrapEvent) => void;
};

type ProjectScopedCheckoutEnvOptions = {
	projectId?: string | null;
	cwd?: string | null;
	repoUrl?: string | null;
};

function isCommandFailure(result: CommandResult): result is CommandFailure {
	return result.ok === false;
}

function ensureDir(dirPath: string) {
	mkdirSync(dirPath, { recursive: true, mode: 0o700 });
}

function removePath(targetPath: string) {
	try {
		rmSync(targetPath, { recursive: true, force: true });
	} catch {
		// ignore cleanup failures
	}
}

function ensureSymlink(sourcePath: string, targetPath: string) {
	try {
		const existing = lstatSync(targetPath);
		if (existing.isSymbolicLink() && readlinkSync(targetPath) === sourcePath) {
			return;
		}
		removePath(targetPath);
	} catch {
		// missing target
	}
	symlinkSync(sourcePath, targetPath, "dir");
}

function sanitizeId(value: string): string {
	return value.trim().replace(/[^A-Za-z0-9._-]+/g, "-");
}

function resolveContainerDataRoot(env: NodeJS.ProcessEnv): string {
	const configured = env.ASTRO_CONTAINER_DATA_DIR?.trim();
	if (configured) return path.resolve(configured);
	const homeDir = env.HOME?.trim() || process.env.HOME?.trim() || homedir();
	return path.join(homeDir, ".astro-container-data");
}

function resolveSharedMainsequenceConfigDir(env: NodeJS.ProcessEnv): string {
	const configured = env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim();
	if (configured) return path.resolve(configured);

	const homeDir = env.HOME?.trim() || process.env.HOME?.trim() || homedir();
	return path.join(homeDir, ".config", "mainsequence");
}

function ensureProjectScopedCheckoutHome(projectId: string, env: NodeJS.ProcessEnv) {
	const projectHome = path.join(
		resolveContainerDataRoot(env),
		"project-checkout-runtime",
		`project-${sanitizeId(projectId)}`,
		"home",
	);
	const sshDir = path.join(projectHome, ".ssh");
	const knownHostsPath = path.join(sshDir, "known_hosts");
	const sshConfigPath = path.join(sshDir, "config");
	const configRoot = path.join(projectHome, ".config");
	const sharedMainsequenceConfigDir = resolveSharedMainsequenceConfigDir(env);

	ensureDir(sshDir);
	ensureDir(configRoot);
	ensureDir(sharedMainsequenceConfigDir);
	if (!existsSync(knownHostsPath)) {
		writeFileSync(knownHostsPath, "", { mode: 0o600 });
	}
	writeFileSync(
		sshConfigPath,
		[
			"Host *",
			"  StrictHostKeyChecking accept-new",
			`  UserKnownHostsFile ${knownHostsPath}`,
			"  IdentitiesOnly yes",
			"",
		].join("\n"),
		{ mode: 0o600 },
	);
	ensureSymlink(sharedMainsequenceConfigDir, path.join(configRoot, "mainsequence"));

	return {
		projectHome,
		sshDir,
		knownHostsPath,
		sshConfigPath,
		configRoot,
	};
}

function deriveRepoKeySafeName(repoUrl: string): string {
	let last = repoUrl.replace(/[?#].*$/, "").split("/").at(-1) ?? repoUrl;
	if (last.toLowerCase().endsWith(".git")) last = last.slice(0, -4);
	return last.replace(/[^A-Za-z0-9._-]+/g, "-");
}

function resolveGitRemoteUrl(cwd: string, env: NodeJS.ProcessEnv): string | null {
	const result = spawnSync("git", ["config", "--get", "remote.origin.url"], {
		cwd,
		env,
		encoding: "utf8",
	});
	if (result.error || result.status !== 0) return null;
	const remoteUrl = result.stdout?.trim();
	return remoteUrl ? remoteUrl : null;
}

export function buildProjectScopedCheckoutEnv(
	baseEnv: NodeJS.ProcessEnv,
	options: ProjectScopedCheckoutEnvOptions,
): NodeJS.ProcessEnv {
	if (!options.projectId) return { ...baseEnv };

	const checkoutHome = ensureProjectScopedCheckoutHome(options.projectId, baseEnv);
	const nextEnv: NodeJS.ProcessEnv = {
		...baseEnv,
		HOME: checkoutHome.projectHome,
		USERPROFILE: checkoutHome.projectHome,
		XDG_CONFIG_HOME: path.join(checkoutHome.projectHome, ".config"),
		ASTRO_PROJECT_CHECKOUT_HOME: checkoutHome.projectHome,
	};

	const repoUrl =
		options.repoUrl ??
		(options.cwd ? resolveGitRemoteUrl(options.cwd, nextEnv) : null);
	if (!repoUrl) return nextEnv;

	const keyPath = path.join(checkoutHome.sshDir, deriveRepoKeySafeName(repoUrl));
	if (!existsSync(keyPath)) return nextEnv;

	return {
		...nextEnv,
		ASTRO_PROJECT_CHECKOUT_REPO_URL: repoUrl,
		ASTRO_PROJECT_CHECKOUT_KEY_PATH: keyPath,
		GIT_SSH_COMMAND: `ssh -F ${checkoutHome.sshConfigPath} -i ${keyPath} -o IdentitiesOnly=yes`,
	};
}

function normalizeVersion(value: unknown): string | null {
	if (typeof value === "number" && Number.isFinite(value)) return String(value);
	if (typeof value === "string" && value.trim()) return value.trim();
	return null;
}

function formatCommand(command: string, args: string[]): string {
	return [command, ...args.map((arg) => (/\s/.test(arg) ? JSON.stringify(arg) : arg))].join(" ");
}

function parseJsonFromStdout(stdout: string): unknown | null {
	const trimmed = stdout.trim();
	if (!trimmed) return null;

	try {
		return JSON.parse(trimmed);
	} catch {
		// fall through
	}

	const firstBrace = trimmed.search(/[\[{]/);
	if (firstBrace === -1) return null;
	const candidate = trimmed.slice(firstBrace);
	try {
		return JSON.parse(candidate);
	} catch {
		return null;
	}
}

function findNestedValue(
	value: unknown,
	keys: string[],
	seen = new Set<unknown>(),
): unknown {
	if (value == null) return null;
	if (typeof value !== "object") return null;
	if (seen.has(value)) return null;
	seen.add(value);

	if (Array.isArray(value)) {
		for (const item of value) {
			const nested = findNestedValue(item, keys, seen);
			if (nested != null) return nested;
		}
		return null;
	}

	const record = value as Record<string, unknown>;
	for (const key of keys) {
		if (key in record) return record[key];
	}

	for (const nestedValue of Object.values(record)) {
		const nested = findNestedValue(nestedValue, keys, seen);
		if (nested != null) return nested;
	}

	return null;
}

function parseSdkStatus(stdout: string): {
	raw: unknown | null;
	currentVersion: string | null;
	latestVersion: string | null;
	updateRecommended: boolean;
} {
	const raw = parseJsonFromStdout(stdout);
	const currentVersion = normalizeVersion(
		findNestedValue(raw, [
			"current_version",
			"currentVersion",
			"project_version",
			"projectVersion",
			"project_sdk_version",
			"projectSdkVersion",
			"sdk_version",
			"sdkVersion",
			"local_version",
			"localVersion",
			"installed_version",
			"installedVersion",
		]),
	);
	const latestVersion = normalizeVersion(
		findNestedValue(raw, [
			"latest_version",
			"latestVersion",
			"latest_sdk_version",
			"latestSdkVersion",
			"latest_available_version",
			"latestAvailableVersion",
			"latest",
		]),
	);
	const updateFlag = findNestedValue(raw, [
		"update_recommended",
		"updateRecommended",
		"outdated",
		"is_outdated",
		"isOutdated",
		"upgrade_available",
		"upgradeAvailable",
	]);
	const updateRecommended =
		typeof updateFlag === "boolean"
			? updateFlag
			: Boolean(currentVersion && latestVersion && currentVersion !== latestVersion);

	return {
		raw,
		currentVersion,
		latestVersion,
		updateRecommended,
	};
}

function runCommand(
	command: string,
	args: string[],
	options: {
		cwd: string;
		env: NodeJS.ProcessEnv;
	},
): CommandResult {
	const result = spawnSync(command, args, {
		cwd: options.cwd,
		env: options.env,
		encoding: "utf8",
	});
	const stdout = result.stdout ?? "";
	const stderr = result.stderr ?? "";
	const exitCode = typeof result.status === "number" ? result.status : null;

	if (result.error) {
		return {
			ok: false,
			exitCode,
			stdout,
			stderr,
			error: result.error.message,
		};
	}

	if (result.signal) {
		return {
			ok: false,
			exitCode,
			stdout,
			stderr,
			error: `Command exited with signal ${result.signal}.`,
		};
	}

	if (exitCode !== 0) {
		return {
			ok: false,
			exitCode,
			stdout,
			stderr,
			error: stderr.trim() || stdout.trim() || `Command exited with code ${String(exitCode)}.`,
		};
	}

	return {
		ok: true,
		exitCode,
		stdout,
		stderr,
	};
}

function isDirectory(candidate: string): boolean {
	try {
		return statSync(candidate).isDirectory();
	} catch {
		return false;
	}
}

function isFile(candidate: string): boolean {
	try {
		return statSync(candidate).isFile();
	} catch {
		return false;
	}
}

function emitBootstrapEvent(options: BootstrapOptions, event: ProjectRuntimeBootstrapEvent) {
	options.onEvent?.(event);
}

export function buildActivatedProjectEnv(
	baseEnv: NodeJS.ProcessEnv,
	snapshot: Pick<ProjectRuntimeSnapshot, "venv"> | null,
	options: ProjectScopedCheckoutEnvOptions = {},
): NodeJS.ProcessEnv {
	const checkoutEnv = buildProjectScopedCheckoutEnv(baseEnv, options);
	if (!snapshot) return checkoutEnv;

	const existingPath = checkoutEnv.PATH ?? "";
	const venvPath = snapshot.venv.path;
	const venvBin = snapshot.venv.binPath;
	return {
		...checkoutEnv,
		VIRTUAL_ENV: venvPath,
		PATH: existingPath ? `${venvBin}${path.delimiter}${existingPath}` : venvBin,
	};
}

export function formatProjectRuntimeSummary(snapshot: ProjectRuntimeSnapshot): string {
	const lines = [
		"Runtime bootstrap already completed for this project session.",
		`- project cwd: ${snapshot.projectCwd}`,
		`- AGENTS.md present: ${snapshot.agentsFilePresent ? "yes" : "no"}`,
		`- project SDK version: ${snapshot.sdkStatus.currentVersion ?? "unknown"}`,
		`- latest SDK version: ${snapshot.sdkStatus.latestVersion ?? "unknown"}`,
		`- SDK update recommended: ${snapshot.sdkStatus.updateRecommended ? "yes" : "no"}`,
		`- active virtual environment: ${snapshot.venv.path}`,
		`- active virtualenv python: ${snapshot.venv.pythonPath}`,
		`- active virtualenv mainsequence version: ${snapshot.venv.mainsequenceVersion ?? "unknown"}`,
		"Treat this as the deterministic runtime state for the session.",
		"Do not rerun `mainsequence project sdk-status --path . --json`, `mainsequence project build_local_venv --path .`, or `uv sync` unless you are intentionally refreshing the environment after a user-approved change.",
	];

	return lines.join("\n");
}

export function bootstrapProjectCoderRuntime(options: BootstrapOptions): ProjectRuntimeBootstrapResult {
	const cwd = path.resolve(options.cwd);
	const inputEnv = { ...process.env, ...(options.env ?? {}) };
	const env = buildMainsequenceStoredAuthEnv(inputEnv);
	const agentsFilePresent = existsSync(path.join(cwd, "AGENTS.md"));
	const sdkStatusCommand = formatCommand("mainsequence", ["project", "sdk-status", "--path", ".", "--json"]);
	const buildLocalVenvCommand = formatCommand("mainsequence", ["project", "build_local_venv", "--path", "."]);

	options.log?.(`Bootstrapping project runtime in ${cwd}`);
	emitBootstrapEvent(options, {
		phase: "start",
		step: "sdk_status",
		command: sdkStatusCommand,
		cwd,
	});

	const sdkStatusResult = runCommand(
		"mainsequence",
		["project", "sdk-status", "--path", ".", "--json"],
		{ cwd, env },
	);
	if (isCommandFailure(sdkStatusResult)) {
		emitBootstrapEvent(options, {
			phase: "failure",
			step: "sdk_status",
			command: sdkStatusCommand,
			cwd,
			summary: `Failed to read project SDK status: ${sdkStatusResult.error}`,
			error: sdkStatusResult.error,
			exitCode: sdkStatusResult.exitCode,
			stdout: sdkStatusResult.stdout,
			stderr: sdkStatusResult.stderr,
		});
		return {
			ok: false,
			step: "sdk_status",
			error: sdkStatusResult.error,
			exitCode: sdkStatusResult.exitCode,
			stdout: sdkStatusResult.stdout,
			stderr: sdkStatusResult.stderr,
		};
	}

	const sdkStatus = parseSdkStatus(sdkStatusResult.stdout);
	emitBootstrapEvent(options, {
		phase: "success",
		step: "sdk_status",
		command: sdkStatusCommand,
		cwd,
		summary: `SDK status loaded. current=${sdkStatus.currentVersion ?? "unknown"} latest=${sdkStatus.latestVersion ?? "unknown"} updateRecommended=${sdkStatus.updateRecommended ? "yes" : "no"}`,
		exitCode: sdkStatusResult.exitCode,
		stdout: sdkStatusResult.stdout,
		stderr: sdkStatusResult.stderr,
		details: {
			currentVersion: sdkStatus.currentVersion,
			latestVersion: sdkStatus.latestVersion,
			updateRecommended: sdkStatus.updateRecommended,
		},
	});

	emitBootstrapEvent(options, {
		phase: "start",
		step: "build_local_venv",
		command: buildLocalVenvCommand,
		cwd,
	});

	const buildLocalVenvResult = runCommand(
		"mainsequence",
		["project", "build_local_venv", "--path", "."],
		{ cwd, env },
	);
	if (isCommandFailure(buildLocalVenvResult)) {
		emitBootstrapEvent(options, {
			phase: "failure",
			step: "build_local_venv",
			command: buildLocalVenvCommand,
			cwd,
			summary: `Failed to build the local project virtual environment: ${buildLocalVenvResult.error}`,
			error: buildLocalVenvResult.error,
			exitCode: buildLocalVenvResult.exitCode,
			stdout: buildLocalVenvResult.stdout,
			stderr: buildLocalVenvResult.stderr,
		});
		return {
			ok: false,
			step: "build_local_venv",
			error: buildLocalVenvResult.error,
			exitCode: buildLocalVenvResult.exitCode,
			stdout: buildLocalVenvResult.stdout,
			stderr: buildLocalVenvResult.stderr,
		};
	}

	const venvPath = path.join(cwd, ".venv");
	const venvBinPath = path.join(venvPath, "bin");
	const venvPythonPath = path.join(venvBinPath, "python");
	emitBootstrapEvent(options, {
		phase: "success",
		step: "build_local_venv",
		command: buildLocalVenvCommand,
		cwd,
		summary: `Project virtual environment build completed.`,
		exitCode: buildLocalVenvResult.exitCode,
		stdout: buildLocalVenvResult.stdout,
		stderr: buildLocalVenvResult.stderr,
		details: {
			venvPath,
		},
	});

	emitBootstrapEvent(options, {
		phase: "start",
		step: "resolve_venv",
		command: null,
		cwd,
		details: {
			venvPath,
			venvBinPath,
			venvPythonPath,
		},
	});
	if (!isDirectory(venvPath) || !isDirectory(venvBinPath) || !isFile(venvPythonPath)) {
		emitBootstrapEvent(options, {
			phase: "failure",
			step: "resolve_venv",
			command: null,
			cwd,
			summary: `Expected a project virtual environment at ${venvPath}.`,
			error: `Expected a project virtual environment at ${venvPath}.`,
			exitCode: null,
			stdout: "",
			stderr: "",
			details: {
				venvPath,
				venvBinPath,
				venvPythonPath,
			},
		});
		return {
			ok: false,
			step: "resolve_venv",
			error: `Expected a project virtual environment at ${venvPath}.`,
			exitCode: null,
			stdout: "",
			stderr: "",
		};
	}
	emitBootstrapEvent(options, {
		phase: "success",
		step: "resolve_venv",
		command: null,
		cwd,
		summary: `Resolved project virtual environment at ${venvPath}.`,
		exitCode: null,
		stdout: "",
		stderr: "",
		details: {
			venvPath,
			venvBinPath,
			venvPythonPath,
		},
	});

	const activatedEnv = buildActivatedProjectEnv(env, {
		venv: {
			path: venvPath,
			binPath: venvBinPath,
			pythonPath: venvPythonPath,
			mainsequenceVersion: null,
		},
	}, {
		projectId: inputEnv.ASTRO_TARGET_PROJECT_ID ?? null,
		cwd,
	});
	emitBootstrapEvent(options, {
		phase: "start",
		step: "activate_venv",
		command: null,
		cwd,
		details: {
			virtualEnv: venvPath,
			pathPrefix: venvBinPath,
		},
	});
	emitBootstrapEvent(options, {
		phase: "success",
		step: "activate_venv",
		command: null,
		cwd,
		summary: `Activated the checked-out project's virtual environment for this session.`,
		exitCode: null,
		stdout: "",
		stderr: "",
		details: {
			virtualEnv: activatedEnv.VIRTUAL_ENV ?? venvPath,
			pathPrefix: venvBinPath,
		},
	});

	const uvSyncCommand = formatCommand("uv", ["sync"]);
	emitBootstrapEvent(options, {
		phase: "start",
		step: "uv_sync",
		command: uvSyncCommand,
		cwd,
		details: {
			virtualEnv: venvPath,
		},
	});
	const uvSyncResult = runCommand("uv", ["sync"], {
		cwd,
		env: activatedEnv,
	});
	if (isCommandFailure(uvSyncResult)) {
		emitBootstrapEvent(options, {
			phase: "failure",
			step: "uv_sync",
			command: uvSyncCommand,
			cwd,
			summary: `uv sync failed in the active project environment: ${uvSyncResult.error}`,
			error: uvSyncResult.error,
			exitCode: uvSyncResult.exitCode,
			stdout: uvSyncResult.stdout,
			stderr: uvSyncResult.stderr,
			details: {
				virtualEnv: venvPath,
			},
		});
		return {
			ok: false,
			step: "uv_sync",
			error: uvSyncResult.error,
			exitCode: uvSyncResult.exitCode,
			stdout: uvSyncResult.stdout,
			stderr: uvSyncResult.stderr,
		};
	}
	emitBootstrapEvent(options, {
		phase: "success",
		step: "uv_sync",
		command: uvSyncCommand,
		cwd,
		summary: `uv sync completed in the active project environment.`,
		exitCode: uvSyncResult.exitCode,
		stdout: uvSyncResult.stdout,
		stderr: uvSyncResult.stderr,
		details: {
			virtualEnv: venvPath,
		},
	});

	const mainsequenceVersionCommand = formatCommand(venvPythonPath, [
		"-c",
		"import importlib.metadata as im; print(im.version('mainsequence'))",
	]);
	emitBootstrapEvent(options, {
		phase: "start",
		step: "read_venv_mainsequence",
		command: mainsequenceVersionCommand,
		cwd,
		details: {
			virtualEnv: venvPath,
			pythonPath: venvPythonPath,
		},
	});
	const mainsequenceVersionResult = runCommand(
		venvPythonPath,
		["-c", "import importlib.metadata as im; print(im.version('mainsequence'))"],
		{
			cwd,
			env: activatedEnv,
		},
	);
	if (isCommandFailure(mainsequenceVersionResult)) {
		emitBootstrapEvent(options, {
			phase: "failure",
			step: "read_venv_mainsequence",
			command: mainsequenceVersionCommand,
			cwd,
			summary: `Failed to read the active virtualenv mainsequence version: ${mainsequenceVersionResult.error}`,
			error: mainsequenceVersionResult.error,
			exitCode: mainsequenceVersionResult.exitCode,
			stdout: mainsequenceVersionResult.stdout,
			stderr: mainsequenceVersionResult.stderr,
			details: {
				virtualEnv: venvPath,
				pythonPath: venvPythonPath,
			},
		});
		return {
			ok: false,
			step: "read_venv_mainsequence",
			error: mainsequenceVersionResult.error,
			exitCode: mainsequenceVersionResult.exitCode,
			stdout: mainsequenceVersionResult.stdout,
			stderr: mainsequenceVersionResult.stderr,
		};
	}
	const activeMainsequenceVersion = normalizeVersion(mainsequenceVersionResult.stdout.trim());
	emitBootstrapEvent(options, {
		phase: "success",
		step: "read_venv_mainsequence",
		command: mainsequenceVersionCommand,
		cwd,
		summary: `Active project virtualenv mainsequence version: ${activeMainsequenceVersion ?? "unknown"}.`,
		exitCode: mainsequenceVersionResult.exitCode,
		stdout: mainsequenceVersionResult.stdout,
		stderr: mainsequenceVersionResult.stderr,
		details: {
			virtualEnv: venvPath,
			pythonPath: venvPythonPath,
			mainsequenceVersion: activeMainsequenceVersion,
		},
	});

	return {
		ok: true,
		snapshot: {
			checkedAt: new Date().toISOString(),
			projectCwd: cwd,
			agentsFilePresent,
			agentsFileMissing: !agentsFilePresent,
			sdkStatus,
			venv: {
				path: venvPath,
				binPath: venvBinPath,
				pythonPath: venvPythonPath,
				mainsequenceVersion: activeMainsequenceVersion,
			},
		},
	};
}
