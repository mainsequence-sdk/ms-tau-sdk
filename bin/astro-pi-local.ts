import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { bootstrapPiAgentDir } from "../runtime/bootstrap/pi-agent-dir.mjs";
import {
	buildMainsequenceStoredAuthEnv,
	bootstrapMainsequenceCliAuth,
	loadEnvFile,
	startMainsequenceCredentialExchangeLoop,
} from "../adapters/mainsequence/runtime-auth.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

function fail(message: string): never {
	console.error(`\n[astro] ${message}`);
	process.exit(1);
}

function run(command: string, args: string[], options: Parameters<typeof spawnSync>[2] = {}) {
	const result = spawnSync(command, args, {
		cwd: repoRoot,
		stdio: "inherit",
		...options,
	});

	if (result.error) {
		if ((result.error as NodeJS.ErrnoException).code === "ENOENT") {
			fail(`Missing required command: ${command}`);
		}
		fail(result.error.message);
	}

	if (result.status !== 0) process.exit(result.status ?? 1);
}

function normalizeMainsequenceUserUid(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	if (!trimmed) return null;
	return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(trimmed)
		? trimmed
		: null;
}

function hasLocalNpmDeps() {
	return existsSync(path.join(repoRoot, "node_modules", ".bin", "tsx"));
}

function hasRepoPiWebAccess() {
	return existsSync(path.join(repoRoot, "node_modules", "pi-web-access", "package.json"));
}

function ensureNodeVersion() {
	const major = Number.parseInt(process.versions.node.split(".")[0], 10);

	if (!Number.isFinite(major) || major < 20) {
		fail(`Node 20+ is required. Current version: ${process.version}`);
	}
}

function ensurePiCli(cwd = repoRoot) {
	const result = spawnSync("pi", ["--version"], {
		cwd,
		stdio: "ignore",
	});

	if ((result.error as NodeJS.ErrnoException | undefined)?.code === "ENOENT") {
		fail("Pi CLI is not installed or not on your PATH.");
	}
	if (result.status !== 0) fail("Pi CLI is installed, but `pi --version` did not succeed.");
}

async function main() {
	loadEnvFile(repoRoot);
	const piAgentState = bootstrapPiAgentDir();
	await bootstrapMainsequenceCliAuth({
		env: process.env,
		log: (message) => console.log(`[astro] ${message}`),
	});
	const runtimeUserId = normalizeMainsequenceUserUid(process.env.ASTRO_MAINSEQUENCE_USER_UID);
	ensureNodeVersion();

	if (!hasLocalNpmDeps()) {
		console.log("[astro] Installing local npm dependencies...");
		run("npm", ["install"]);
	}

	if (!hasRepoPiWebAccess()) {
		console.log("[astro] Installing missing repo npm dependencies...");
		run("npm", ["install"]);
	}

	const runtimeCwd = piAgentState.orchestratorRuntime?.runtimeCwd ?? repoRoot;
	ensurePiCli(runtimeCwd);
	if (piAgentState.prunedProviderAuthEntries?.length) {
		console.log(
			`[astro] Pruned container-local provider auth state: ${piAgentState.prunedProviderAuthEntries.join(", ")}.`,
		);
	}
	if (piAgentState.prunedScopedProviderCredentialDir) {
		console.log(
			`[astro] Pruned scoped provider credential directory: ${piAgentState.prunedScopedProviderCredentialDir}.`,
		);
	}

	console.log("[astro] Running TypeScript check...");
	run("npm", ["run", "check"]);

	const credentialExchangeLoop = startMainsequenceCredentialExchangeLoop({
		env: process.env,
		log: (message) => console.log(`[astro] ${message}`),
	});

	console.log("[astro] Starting Pi...");
	const child = spawn("pi", [], {
		cwd: runtimeCwd,
		stdio: "inherit",
		shell: false,
		env: {
			...buildMainsequenceStoredAuthEnv(process.env),
			PWD: runtimeCwd,
			...(runtimeUserId ? { ASTRO_MAINSEQUENCE_USER_UID: runtimeUserId } : {}),
		},
	});

	child.on("exit", (code) => {
		credentialExchangeLoop?.stop();
		process.exit(code ?? 0);
	});
	child.on("error", (error: NodeJS.ErrnoException) => {
		credentialExchangeLoop?.stop();
		if (error.code === "ENOENT") fail("Pi CLI is not installed or not on your PATH.");
		fail(error.message);
	});
}

main().catch((error) => {
	fail(error instanceof Error ? error.message : "Unknown startup failure.");
});
