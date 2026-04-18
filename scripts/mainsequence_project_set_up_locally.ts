import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import process from "node:process";
import { existsSync, lstatSync, mkdirSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { bootstrapPiAgentDir } from "./bootstrap_pi_agent_dir.mjs";
import { buildMainsequenceStoredAuthEnv, loadEnvFile } from "./mainsequence_runtime_auth.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const DEFAULT_RETRY_DELAYS_MS = [1000, 2000, 5000];
const TRANSIENT_FAILURE_PATTERNS = [
	/host key verification failed/i,
	/permission denied \(publickey\)/i,
	/repository not found/i,
] as const;

function fail(message: string): never {
	console.error(`[astro] ${message}`);
	process.exit(1);
}

function sleep(ms: number) {
	return new Promise((resolve) => setTimeout(resolve, ms));
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

function extractProjectId(args: string[]): string | null {
	for (let index = 0; index < args.length; index += 1) {
		const arg = args[index];
		if (!arg) continue;
		if (arg === "--base-dir") {
			index += 1;
			continue;
		}
		if (arg.startsWith("-")) continue;
		return arg.trim();
	}
	return null;
}

function resolveSharedMainsequenceConfigDir(): string {
	const configured = process.env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim();
	if (configured) return path.resolve(configured);

	const homeDir = process.env.HOME?.trim() || homedir();
	return path.join(homeDir, ".config", "mainsequence");
}

function ensureProjectScopedCheckoutHome(projectId: string) {
	const containerDataRoot =
		process.env.ASTRO_CONTAINER_DATA_DIR?.trim()
			? path.resolve(process.env.ASTRO_CONTAINER_DATA_DIR)
			: path.join(process.env.HOME?.trim() || homedir(), ".astro-container-data");
	const projectHome = path.join(
		containerDataRoot,
		"project-checkout-runtime",
		`project-${sanitizeId(projectId)}`,
		"home",
	);
	const sshDir = path.join(projectHome, ".ssh");
	const knownHostsPath = path.join(sshDir, "known_hosts");
	const sshConfigPath = path.join(sshDir, "config");
	const configRoot = path.join(projectHome, ".config");
	const sharedMainsequenceConfigDir = resolveSharedMainsequenceConfigDir();

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
	};
}

function isTransientProjectSetupFailure(output: string): boolean {
	return TRANSIENT_FAILURE_PATTERNS.some((pattern) => pattern.test(output));
}

async function main() {
	bootstrapPiAgentDir();
	loadEnvFile(repoRoot);
	const mainsequenceCommand = process.env.ASTRO_REAL_MAINSEQUENCE || "mainsequence";

	const forwardedArgs = process.argv.slice(2);
	if (forwardedArgs.length === 0) {
		fail(
			"Usage: tsx /app/scripts/mainsequence_project_set_up_locally.ts <project-id> [--base-dir <dir>] [--scaffold-docker|--no-scaffold-docker]",
		);
	}
	const projectId = extractProjectId(forwardedArgs);
	if (!projectId) {
		fail("Could not determine the Main Sequence project id for local setup.");
	}
	const checkoutHome = ensureProjectScopedCheckoutHome(projectId);

	const maxAttempts = DEFAULT_RETRY_DELAYS_MS.length + 1;
	for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
		const result = spawnSync(mainsequenceCommand, ["project", "set-up-locally", ...forwardedArgs], {
			cwd: repoRoot,
			shell: false,
			env: {
				...buildMainsequenceStoredAuthEnv(process.env),
				ASTRO_MAINSEQUENCE_BYPASS_SHIM: "1",
				HOME: checkoutHome.projectHome,
				USERPROFILE: checkoutHome.projectHome,
				XDG_CONFIG_HOME: path.join(checkoutHome.projectHome, ".config"),
			},
			encoding: "utf8",
		});

		if (typeof result.stdout === "string" && result.stdout.length > 0) {
			process.stdout.write(result.stdout);
		}
		if (typeof result.stderr === "string" && result.stderr.length > 0) {
			process.stderr.write(result.stderr);
		}

		if (result.error) {
			const error = result.error as NodeJS.ErrnoException;
			if (error.code === "ENOENT") {
				fail("Missing required command: mainsequence");
			}
			fail(error.message);
		}

		if ((result.status ?? 1) === 0) {
			return;
		}

		const combinedOutput = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
		const shouldRetry =
			attempt < maxAttempts && isTransientProjectSetupFailure(combinedOutput);
		if (!shouldRetry) {
			process.exit(result.status ?? 1);
		}

		const delayMs = DEFAULT_RETRY_DELAYS_MS[attempt - 1] ?? DEFAULT_RETRY_DELAYS_MS.at(-1) ?? 1000;
		process.stderr.write(
			`[astro] Transient SSH/deploy-key failure while setting up the project locally. Retrying in ${delayMs}ms (attempt ${attempt + 1}/${maxAttempts}).\n`,
		);
		await sleep(delayMs);
	}
}

await main();
