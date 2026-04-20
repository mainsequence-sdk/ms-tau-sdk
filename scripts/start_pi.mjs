import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { bootstrapPiAgentDir } from "./bootstrap_pi_agent_dir.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const MAINSEQUENCE_RUNTIME_CREDENTIAL_EXCHANGE_INTERVAL_ENV =
	"MAINSEQUENCE_RUNTIME_CREDENTIAL_EXCHANGE_INTERVAL_SECONDS";
const MAINSEQUENCE_CLI_SESSION_ID_ENV = "MAINSEQUENCE_CLI_SESSION_ID";
const MAINSEQUENCE_AUTH_MODE_ENV = "MAINSEQUENCE_AUTH_MODE";
const MAINSEQUENCE_RUNTIME_CREDENTIAL_ID_ENV = "MAINSEQUENCE_RUNTIME_CREDENTIAL_ID";
const MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET_ENV = "MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET";
const MAINSEQUENCE_RUNTIME_CREDENTIAL_REQUIRED_ENV = [
	MAINSEQUENCE_RUNTIME_CREDENTIAL_ID_ENV,
	MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET_ENV,
];
const DEFAULT_BACKEND = "https://api.main-sequence.app";
const DEFAULT_CLI_SESSION_ID_PREFIX = "astro-mainsequence-cli";

function sanitizeId(value) {
	return value.trim().replace(/\s+/g, "_");
}

function normalizeIdPart(value) {
	if (typeof value === "number" && Number.isFinite(value)) return sanitizeId(String(value));
	if (typeof value === "string" && value.trim()) return sanitizeId(value);
	return null;
}

function resolveMainsequenceUserId(options = {}) {
	const explicitUserId = normalizeIdPart(options.userId);
	if (explicitUserId) return explicitUserId;
	const env = options.env ?? process.env;
	return normalizeIdPart(env.ASTRO_MAINSEQUENCE_USER_ID);
}

function fail(message) {
	console.error(`\n[astro] ${message}`);
	process.exit(1);
}

function run(command, args, options = {}) {
	const result = spawnSync(command, args, {
		cwd: repoRoot,
		stdio: "inherit",
		...options,
	});

	if (result.error) {
		if (result.error.code === "ENOENT") fail(`Missing required command: ${command}`);
		fail(result.error.message);
	}

	if (result.status !== 0) process.exit(result.status ?? 1);
}

function loadEnvFile() {
	const envPath = path.join(repoRoot, ".env");
	if (!existsSync(envPath)) return;
	const contents = readFileSync(envPath, "utf8");
	for (const rawLine of contents.split(/\r?\n/)) {
		const line = rawLine.trim();
		if (!line || line.startsWith("#")) continue;
		const equalsIndex = line.indexOf("=");
		if (equalsIndex === -1) continue;
		const key = line.slice(0, equalsIndex).trim();
		if (!key) continue;
		let value = line.slice(equalsIndex + 1).trim();
		if (
			(value.startsWith("\"") && value.endsWith("\"")) ||
			(value.startsWith("'") && value.endsWith("'"))
		) {
			value = value.slice(1, -1);
		}
		if (process.env[key] === undefined) process.env[key] = value;
	}
}

function getRuntimeCredentialExchangeIntervalMs() {
	const raw = process.env[MAINSEQUENCE_RUNTIME_CREDENTIAL_EXCHANGE_INTERVAL_ENV];
	if (!raw) return null;
	const seconds = Number.parseInt(raw, 10);
	if (!Number.isFinite(seconds) || seconds <= 0) {
		fail(`${MAINSEQUENCE_RUNTIME_CREDENTIAL_EXCHANGE_INTERVAL_ENV} must be a positive integer (seconds).`);
	}
	return seconds * 1000;
}

function getMainsequenceAuthMode(env = process.env) {
	const raw = env[MAINSEQUENCE_AUTH_MODE_ENV]?.trim().toLowerCase();
	if (!raw || raw === "runtime_credential") return "runtime_credential";
	fail(`${MAINSEQUENCE_AUTH_MODE_ENV} must be "runtime_credential".`);
}

function ensureMainsequenceRuntimeCredentialEnv() {
	const missing = MAINSEQUENCE_RUNTIME_CREDENTIAL_REQUIRED_ENV.filter((key) => !process.env[key]?.trim());
	if (missing.length) {
		fail(`Missing required env for Main Sequence runtime credential auth: ${missing.join(", ")}`);
	}
}

function resolveBackendUrl() {
	const raw =
		process.env.MAINSEQUENCE_BACKEND ||
		process.env.MAIN_SEQUENCE_BACKEND_URL ||
		process.env.MAINSEQUENCE_ENDPOINT ||
		process.env.TDAG_ENDPOINT ||
		DEFAULT_BACKEND;

	return raw.replace(/\/+$/, "");
}

function resolveMainsequenceCliSessionId(env = process.env) {
	const configured = env[MAINSEQUENCE_CLI_SESSION_ID_ENV]?.trim();
	if (configured) return configured;

	const backend = resolveBackendUrl();
	const projectsBase = env.MAINSEQUENCE_PROJECTS_BASE?.trim() || "default-projects-base";
	return `${DEFAULT_CLI_SESSION_ID_PREFIX}:${backend}:${projectsBase}`;
}

function clearLegacyTokenEnv(env = process.env) {
	for (const key of Object.keys(env)) {
		const normalized = key.toUpperCase();
		const isMainsequenceToken =
			(normalized.startsWith("MAINSEQUENCE_") || normalized.startsWith("MAIN_SEQUENCE_")) &&
			normalized.endsWith("_TOKEN");
		if (isMainsequenceToken) delete env[key];
	}
}

function buildStoredAuthVerificationEnv() {
	const verifyEnv = { ...process.env };
	const backendUrl = resolveBackendUrl();
	clearLegacyTokenEnv(verifyEnv);
	verifyEnv.MAINSEQUENCE_ENDPOINT = verifyEnv.MAINSEQUENCE_ENDPOINT ?? backendUrl;
	verifyEnv.TDAG_ENDPOINT = verifyEnv.TDAG_ENDPOINT ?? backendUrl;
	verifyEnv[MAINSEQUENCE_CLI_SESSION_ID_ENV] = resolveMainsequenceCliSessionId(process.env);
	return verifyEnv;
}

function buildMainsequenceRuntimeCredentialLoginArgs() {
	return [
		"login",
		"--backend",
		process.env.MAINSEQUENCE_BACKEND ?? "",
		"--projects-base",
		process.env.MAINSEQUENCE_PROJECTS_BASE ?? "",
	];
}

function runMainsequenceRuntimeCredentialLogin() {
	clearLegacyTokenEnv();
	const loginResult = spawnSync("mainsequence", buildMainsequenceRuntimeCredentialLoginArgs(), {
		cwd: repoRoot,
		stdio: "inherit",
		env: buildStoredAuthVerificationEnv(),
	});

	if (loginResult.error) {
		if (loginResult.error.code === "ENOENT") {
			return { ok: false, error: "Missing required command: mainsequence" };
		}
		return { ok: false, error: loginResult.error.message };
	}
	if (loginResult.status !== 0) {
		return {
			ok: false,
			error: `mainsequence runtime credential login failed with code ${loginResult.status ?? 1}.`,
		};
	}

	clearLegacyTokenEnv();
	return { ok: true };
}

function summarizeSpawnSyncFailure(result) {
	const details = [];
	if (typeof result.status === "number") details.push(`exit code ${result.status}`);
	else if (result.signal) details.push(`signal ${result.signal}`);
	const stderr =
		typeof result.stderr === "string" ? result.stderr.trim() : result.stderr?.toString("utf8").trim();
	const stdout =
		typeof result.stdout === "string" ? result.stdout.trim() : result.stdout?.toString("utf8").trim();
	if (stderr) details.push(`stderr: ${stderr}`);
	if (stdout) details.push(`stdout: ${stdout}`);
	return details.join("; ") || "no output";
}

function verifyMainsequenceCliAuthStore() {
	clearLegacyTokenEnv();
	const verifyResult = spawnSync("mainsequence", ["user"], {
		cwd: repoRoot,
		stdio: ["ignore", "pipe", "pipe"],
		env: buildStoredAuthVerificationEnv(),
		encoding: "utf8",
	});
	if (verifyResult.error) {
		if (verifyResult.error.code === "ENOENT") {
			return { ok: false, error: "Missing required command: mainsequence" };
		}
		return { ok: false, error: verifyResult.error.message };
	}
	if (verifyResult.status !== 0) {
		return {
			ok: false,
			error: `Main Sequence CLI auth verification failed (${summarizeSpawnSyncFailure(verifyResult)}).`,
		};
	}

	clearLegacyTokenEnv();
	return { ok: true };
}

async function ensureMainsequenceCliAuth() {
	getMainsequenceAuthMode();
	ensureMainsequenceRuntimeCredentialEnv();
	clearLegacyTokenEnv();
	console.log("[astro] Using Main Sequence runtime credential auth...");
	console.log("[astro] Exchanging runtime credential before CLI auth verification...");
	const loginResult = runMainsequenceRuntimeCredentialLogin();
	if (!loginResult.ok) fail(`Main Sequence runtime credential auth failed: ${loginResult.error}`);
	const verifyResult = verifyMainsequenceCliAuthStore();
	if (!verifyResult.ok) fail(`Main Sequence runtime credential auth failed: ${verifyResult.error}`);
	clearLegacyTokenEnv();
	console.log("[astro] Main Sequence runtime credential auth is ready.");
}

function startMainsequenceCredentialExchangeLoop() {
	const intervalMs = getRuntimeCredentialExchangeIntervalMs();
	if (!intervalMs) return null;
	getMainsequenceAuthMode();
	ensureMainsequenceRuntimeCredentialEnv();

	let exchangeInFlight = false;
	const runLogin = (reason) => {
		if (exchangeInFlight) return;
		exchangeInFlight = true;
		clearLegacyTokenEnv();
		const loginResult = runMainsequenceRuntimeCredentialLogin();
		if (!loginResult.ok) {
			exchangeInFlight = false;
			console.error(
				`[astro] Main Sequence runtime credential exchange failed (${reason}): ${loginResult.error}`,
			);
			return;
		}
		const verifyResult = verifyMainsequenceCliAuthStore();
		exchangeInFlight = false;
		if (!verifyResult.ok) {
			console.error(
				`[astro] persisted Main Sequence runtime credential auth verification failed (${reason}): ${verifyResult.error}`,
			);
			return;
		}
		clearLegacyTokenEnv();
	};

	console.log(
		`[astro] Starting Main Sequence runtime credential exchange loop every ${Math.floor(intervalMs / 1000)}s...`,
	);
	const timer = setInterval(() => {
		runLogin("interval");
	}, intervalMs);
	return {
		stop: () => clearInterval(timer),
	};
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

	if (result.error?.code === "ENOENT") fail("Pi CLI is not installed or not on your PATH.");
	if (result.status !== 0) fail("Pi CLI is installed, but `pi --version` did not succeed.");
}

async function main() {
	loadEnvFile();
	const piAgentState = bootstrapPiAgentDir();
	await ensureMainsequenceCliAuth();
	const runtimeUserId = resolveMainsequenceUserId({ env: process.env });
	ensureNodeVersion();

	if (!hasLocalNpmDeps()) {
		console.log("[astro] Installing local npm dependencies...");
		run("npm", ["install"]);
	}

	if (!hasRepoPiWebAccess()) {
		console.log("[astro] Installing missing repo npm dependencies...");
		run("npm", ["install"]);
	}

	ensurePiCli(piAgentState.orchestratorRuntime?.runtimeCwd ?? repoRoot);

	if (piAgentState.hostImportDir) {
		const importedText =
			piAgentState.importedEntries.length > 0
				? piAgentState.importedEntries.join(", ")
				: "no reusable host Pi state";
		console.log(
			`[astro] Using container-local Pi agent dir at ${piAgentState.targetDir} (imported ${importedText} from ${piAgentState.hostImportDir}).`,
		);
	}

	console.log("[astro] Running TypeScript check...");
	run("npm", ["run", "check"]);

	const credentialExchangeLoop = startMainsequenceCredentialExchangeLoop();

	console.log("[astro] Starting Pi...");
	const child = spawn("pi", [], {
		cwd: piAgentState.orchestratorRuntime?.runtimeCwd ?? repoRoot,
		stdio: "inherit",
		shell: false,
		env: {
			...buildStoredAuthVerificationEnv(),
			PWD: piAgentState.orchestratorRuntime?.runtimeCwd ?? repoRoot,
			...(runtimeUserId ? { ASTRO_MAINSEQUENCE_USER_ID: runtimeUserId } : {}),
		},
	});

	child.on("exit", (code) => {
		credentialExchangeLoop?.stop();
		process.exit(code ?? 0);
	});
	child.on("error", (error) => {
		credentialExchangeLoop?.stop();
		if (error.code === "ENOENT") fail("Pi CLI is not installed or not on your PATH.");
		fail(error.message);
	});
}

main().catch((error) => {
	fail(error instanceof Error ? error.message : "Unknown startup failure.");
});
