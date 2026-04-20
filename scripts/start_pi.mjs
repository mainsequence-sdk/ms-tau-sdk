import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { bootstrapPiAgentDir } from "./bootstrap_pi_agent_dir.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const MAINSEQUENCE_REFRESH_INTERVAL_ENV = "MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS";
const MAINSEQUENCE_CLI_SESSION_ID_ENV = "MAINSEQUENCE_CLI_SESSION_ID";
const MAINSEQUENCE_AUTH_MODE_ENV = "MAINSEQUENCE_AUTH_MODE";
const MAINSEQUENCE_RUNTIME_CREDENTIAL_ID_ENV = "MAINSEQUENCE_RUNTIME_CREDENTIAL_ID";
const MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET_ENV = "MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET";
const MAINSEQUENCE_REQUIRED_ENV = [
	"MAINSEQUENCE_BACKEND",
	"MAINSEQUENCE_PROJECTS_BASE",
];
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
	if (typeof value === "number" && Number.isFinite(value)) {
		return sanitizeId(String(value));
	}
	if (typeof value === "string" && value.trim()) {
		return sanitizeId(value);
	}
	return null;
}

function decodeBase64Url(value) {
	try {
		const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
		const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
		return Buffer.from(padded, "base64").toString("utf8");
	} catch {
		return null;
	}
}

function decodeJwtPayload(token) {
	if (!token) return null;
	const parts = token.split(".");
	if (parts.length !== 3) return null;
	const decoded = decodeBase64Url(parts[1]);
	if (!decoded) return null;

	try {
		const parsed = JSON.parse(decoded);
		return parsed && typeof parsed === "object" ? parsed : null;
	} catch {
		return null;
	}
}

function resolveMainsequenceUserId(options = {}) {
	const explicitUserId = normalizeIdPart(options.userId);
	if (explicitUserId) return explicitUserId;

	const env = options.env ?? process.env;
	const envUserId = normalizeIdPart(env.ASTRO_MAINSEQUENCE_USER_ID);
	if (envUserId) return envUserId;

	const jwtPayload =
		decodeJwtPayload(env.MAINSEQUENCE_ACCESS_TOKEN) ?? decodeJwtPayload(env.MAINSEQUENCE_REFRESH_TOKEN);
	return (
		normalizeIdPart(jwtPayload?.user_id) ??
		normalizeIdPart(jwtPayload?.userId) ??
		normalizeIdPart(jwtPayload?.id) ??
		normalizeIdPart(jwtPayload?.sub) ??
		null
	);
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
		if (result.error.code === "ENOENT") {
			fail(`Missing required command: ${command}`);
		}

		fail(result.error.message);
	}

	if (result.status !== 0) {
		process.exit(result.status ?? 1);
	}
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
		if (process.env[key] === undefined) {
			process.env[key] = value;
		}
	}
}

function getRefreshIntervalMs() {
	const raw = process.env[MAINSEQUENCE_REFRESH_INTERVAL_ENV];
	if (!raw) return null;
	const seconds = Number.parseInt(raw, 10);
	if (!Number.isFinite(seconds) || seconds <= 0) {
		fail(`${MAINSEQUENCE_REFRESH_INTERVAL_ENV} must be a positive integer (seconds).`);
	}
	return seconds * 1000;
}

function getMainsequenceAuthMode(env = process.env) {
	const raw = env[MAINSEQUENCE_AUTH_MODE_ENV]?.trim().toLowerCase();
	if (!raw || raw === "token") return "token";
	if (raw === "runtime_credential") return "runtime_credential";
	fail(`${MAINSEQUENCE_AUTH_MODE_ENV} must be "runtime_credential" or "token".`);
}

function ensureMainsequenceEnv() {
	const missing = MAINSEQUENCE_REQUIRED_ENV.filter((key) => !process.env[key]);
	if (missing.length) {
		fail(`Missing required env for deterministic Main Sequence login: ${missing.join(", ")}`);
	}
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

function normalizeMainsequenceToken(value) {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function resolveMainsequenceCliSessionId(env = process.env) {
	const configured = env[MAINSEQUENCE_CLI_SESSION_ID_ENV]?.trim();
	if (configured) {
		return configured;
	}

	const backend = resolveBackendUrl();
	const projectsBase = env.MAINSEQUENCE_PROJECTS_BASE?.trim() || "default-projects-base";
	return `${DEFAULT_CLI_SESSION_ID_PREFIX}:${backend}:${projectsBase}`;
}

function buildCredentialsKey(credentials) {
	if (!credentials?.accessToken || !credentials?.refreshToken) return null;
	return `${credentials.accessToken}:${credentials.refreshToken}`;
}

function resolveMainsequenceAuthStorePath() {
	const configuredDir = process.env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim();
	if (configuredDir) {
		return path.join(path.resolve(configuredDir), "auth.json");
	}

	const homeDir = process.env.HOME || process.env.USERPROFILE || "";
	return path.join(homeDir, ".config", "mainsequence", "auth.json");
}

function readPersistedMainsequenceAuth() {
	const authPath = resolveMainsequenceAuthStorePath();
	if (!existsSync(authPath)) {
		return {
			accessToken: null,
			refreshToken: null,
		};
	}

	try {
		const parsed = JSON.parse(readFileSync(authPath, "utf8"));
		return {
			accessToken: normalizeMainsequenceToken(parsed?.access),
			refreshToken: normalizeMainsequenceToken(parsed?.refresh),
		};
	} catch {
		return {
			accessToken: null,
			refreshToken: null,
		};
	}
}

function syncProcessEnvFromPersistedMainsequenceAuth(fallback) {
	const persisted = readPersistedMainsequenceAuth();
	const nextAccessToken = persisted.accessToken ?? fallback?.accessToken ?? null;
	const nextRefreshToken = persisted.refreshToken ?? fallback?.refreshToken ?? null;

	if (nextAccessToken) process.env.MAINSEQUENCE_ACCESS_TOKEN = nextAccessToken;
	if (nextRefreshToken) process.env.MAINSEQUENCE_REFRESH_TOKEN = nextRefreshToken;
}

function buildStoredAuthVerificationEnv() {
	const verifyEnv = { ...process.env };
	const backendUrl = resolveBackendUrl();
	delete verifyEnv.MAINSEQUENCE_ACCESS_TOKEN;
	delete verifyEnv.MAINSEQUENCE_REFRESH_TOKEN;
	verifyEnv.MAINSEQUENCE_ENDPOINT = verifyEnv.MAINSEQUENCE_ENDPOINT ?? backendUrl;
	verifyEnv.TDAG_ENDPOINT = verifyEnv.TDAG_ENDPOINT ?? backendUrl;
	verifyEnv[MAINSEQUENCE_CLI_SESSION_ID_ENV] = resolveMainsequenceCliSessionId(process.env);
	return verifyEnv;
}

function buildMainsequenceLoginArgs(credentials) {
	return [
		"login",
		"--access-token",
		credentials?.accessToken ?? "",
		"--refresh-token",
		credentials?.refreshToken ?? "",
		"--backend",
		process.env.MAINSEQUENCE_BACKEND ?? "",
		"--projects-base",
		process.env.MAINSEQUENCE_PROJECTS_BASE ?? "",
	];
}

function runMainsequenceLogin(credentials) {
	const loginResult = spawnSync("mainsequence", buildMainsequenceLoginArgs(credentials), {
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
		return { ok: false, error: `mainsequence login failed with code ${loginResult.status ?? 1}.` };
	}

	return { ok: true };
}

function summarizeSpawnSyncFailure(result) {
	const details = [];
	if (typeof result.status === "number") {
		details.push(`exit code ${result.status}`);
	} else if (result.signal) {
		details.push(`signal ${result.signal}`);
	}
	const stderr =
		typeof result.stderr === "string" ? result.stderr.trim() : result.stderr?.toString("utf8").trim();
	const stdout =
		typeof result.stdout === "string" ? result.stdout.trim() : result.stdout?.toString("utf8").trim();
	if (stderr) {
		details.push(`stderr: ${stderr}`);
	}
	if (stdout) {
		details.push(`stdout: ${stdout}`);
	}
	return details.join("; ") || "no output";
}

function verifyMainsequenceCliAuthStore() {
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

	return { ok: true };
}

async function refreshMainsequenceAccessToken(refreshTokenOverride = null) {
	ensureMainsequenceEnv();
	const refreshToken =
		normalizeMainsequenceToken(refreshTokenOverride) ??
		normalizeMainsequenceToken(process.env.MAINSEQUENCE_REFRESH_TOKEN);
	if (!refreshToken) {
		return { ok: false, error: "Missing refresh token." };
	}
	const response = await fetch(`${resolveBackendUrl()}/auth/jwt-token/token/refresh/`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
		},
		body: JSON.stringify({ refresh: refreshToken }),
	});

	if (!response.ok) {
		const body = await response.text();
		return {
			ok: false,
			error: body.trim() || `Failed to refresh Main Sequence access token (${response.status}).`,
		};
	}

	const payload = await response.json();
	const accessToken =
		normalizeMainsequenceToken(payload?.access);
	if (!accessToken) {
		return { ok: false, error: "Refresh response did not include a Main Sequence access token." };
	}
	const nextRefreshToken = normalizeMainsequenceToken(payload?.refresh) ?? refreshToken;
	process.env.MAINSEQUENCE_ACCESS_TOKEN = accessToken;
	process.env.MAINSEQUENCE_REFRESH_TOKEN = nextRefreshToken;
	return {
		ok: true,
		accessToken,
		refreshToken: nextRefreshToken,
	};
}

function isRefreshFailure(result) {
	return result.ok === false;
}

async function ensureMainsequenceCliAuth() {
	if (getMainsequenceAuthMode() === "runtime_credential") {
		ensureMainsequenceRuntimeCredentialEnv();
		console.log("[astro] Using Main Sequence runtime credential auth...");
		const verifyResult = verifyMainsequenceCliAuthStore();
		if (!verifyResult.ok) {
			fail(`Main Sequence runtime credential auth failed: ${verifyResult.error}`);
		}
		console.log("[astro] Main Sequence runtime credential auth is ready.");
		return;
	}

	ensureMainsequenceEnv();
	const configuredCredentials = {
		accessToken: normalizeMainsequenceToken(process.env.MAINSEQUENCE_ACCESS_TOKEN),
		refreshToken: normalizeMainsequenceToken(process.env.MAINSEQUENCE_REFRESH_TOKEN),
	};
	const persistedCredentials = readPersistedMainsequenceAuth();
	if (!configuredCredentials.refreshToken && !persistedCredentials.refreshToken) {
		fail("Missing Main Sequence refresh token in both env and the persisted CLI auth store.");
	}

	const attemptedCredentials = new Set();
	const attemptedErrors = [];
	const tryLogin = (label, credentials) => {
		const credentialsKey = buildCredentialsKey(credentials);
		if (!credentialsKey || attemptedCredentials.has(credentialsKey)) return false;
		attemptedCredentials.add(credentialsKey);
		console.log(`[astro] Running deterministic Main Sequence CLI login with ${label}...`);
		const loginResult = runMainsequenceLogin(credentials);
		if (!loginResult.ok) {
			attemptedErrors.push(`${label}: ${loginResult.error}`);
			return false;
		}

		const verifyResult = verifyMainsequenceCliAuthStore();
		if (!verifyResult.ok) {
			attemptedErrors.push(`${label}: ${verifyResult.error}`);
			return false;
		}

		syncProcessEnvFromPersistedMainsequenceAuth(credentials);
		return true;
	};

	const refreshCandidates = [
		{
			label: "configured environment refresh token",
			refreshToken: configuredCredentials.refreshToken,
		},
		{
			label: "persisted CLI auth store refresh token",
			refreshToken: persistedCredentials.refreshToken,
		},
	].filter(
		(candidate, index, list) =>
			candidate.refreshToken &&
			list.findIndex((entry) => entry.refreshToken === candidate.refreshToken) === index,
	);

	if (refreshCandidates.length > 0) {
		console.log("[astro] Refreshing the Main Sequence access token before deterministic CLI login...");
	}
	for (const candidate of refreshCandidates) {
		const refreshedCredentials = await refreshMainsequenceAccessToken(candidate.refreshToken);
		if (isRefreshFailure(refreshedCredentials)) {
			attemptedErrors.push(`${candidate.label}: ${refreshedCredentials.error}`);
			continue;
		}
		if (
			tryLogin(`a freshly refreshed access token from the ${candidate.label}`, refreshedCredentials)
		) {
			console.log("[astro] Main Sequence CLI auth is ready.");
			return;
		}
	}

	if (tryLogin("configured environment tokens", configuredCredentials)) {
		console.log("[astro] Main Sequence CLI auth is ready.");
		return;
	}

	if (tryLogin("persisted CLI auth store", persistedCredentials)) {
		console.log("[astro] Main Sequence CLI auth is ready.");
		return;
	}

	fail(
		`Deterministic Main Sequence login failed: ${attemptedErrors.join(" | ") || "unknown failure."}`,
	);
}

function startMainsequenceRefreshLoop() {
	if (getMainsequenceAuthMode() === "runtime_credential") {
		if (process.env[MAINSEQUENCE_REFRESH_INTERVAL_ENV]) {
			console.log("[astro] Skipping Main Sequence token refresh loop because runtime credential auth is active.");
		}
		return null;
	}

	const intervalMs = getRefreshIntervalMs();
	if (!intervalMs) return null;
	ensureMainsequenceEnv();

	let refreshInFlight = false;
	const runLogin = async (reason) => {
		if (refreshInFlight) return;
		refreshInFlight = true;
		const refreshResult = await refreshMainsequenceAccessToken();
		if (isRefreshFailure(refreshResult)) {
			refreshInFlight = false;
			console.error(
				`[astro] mainsequence access-token refresh failed (${reason}): ${refreshResult.error}`,
			);
			return;
		}
		const child = spawn(
			"mainsequence",
			buildMainsequenceLoginArgs({
				accessToken: normalizeMainsequenceToken(process.env.MAINSEQUENCE_ACCESS_TOKEN),
				refreshToken: normalizeMainsequenceToken(process.env.MAINSEQUENCE_REFRESH_TOKEN),
			}),
			{
				stdio: "inherit",
				shell: false,
				env: buildStoredAuthVerificationEnv(),
			},
		);

		child.on("exit", (code) => {
			refreshInFlight = false;
			if (typeof code === "number" && code !== 0) {
				console.error(`[astro] mainsequence login failed (${reason}) with code ${code}.`);
				return;
			}
			const verifyResult = verifyMainsequenceCliAuthStore();
			if (!verifyResult.ok) {
				console.error(
					`[astro] persisted Main Sequence CLI auth verification failed (${reason}): ${verifyResult.error}`,
				);
				return;
			}
			syncProcessEnvFromPersistedMainsequenceAuth();
		});

		child.on("error", (error) => {
			refreshInFlight = false;
			if (error.code === "ENOENT") {
				console.error("[astro] Missing required command: mainsequence");
				return;
			}
			console.error(`[astro] mainsequence login error (${reason}): ${error.message}`);
		});
	};

	console.log(
		`[astro] Starting Main Sequence token refresh loop every ${Math.floor(intervalMs / 1000)}s...`,
	);
	const timer = setInterval(() => {
		void runLogin("interval");
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

function ensurePiCli() {
	const result = spawnSync("pi", ["--version"], {
		cwd: repoRoot,
		stdio: "ignore",
	});

	if (result.error?.code === "ENOENT") {
		fail("Pi CLI is not installed or not on your PATH.");
	}

	if (result.status !== 0) {
		fail("Pi CLI is installed, but `pi --version` did not succeed.");
	}
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

	ensurePiCli();

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

	const refreshLoop = startMainsequenceRefreshLoop();

	console.log("[astro] Starting Pi...");
	const child = spawn("pi", [], {
		cwd: repoRoot,
		stdio: "inherit",
		shell: false,
		env: {
			...buildStoredAuthVerificationEnv(),
			...(runtimeUserId ? { ASTRO_MAINSEQUENCE_USER_ID: runtimeUserId } : {}),
		},
	});

	child.on("exit", (code) => {
		refreshLoop?.stop();
		process.exit(code ?? 0);
	});

	child.on("error", (error) => {
		refreshLoop?.stop();
		if (error.code === "ENOENT") {
			fail("Pi CLI is not installed or not on your PATH.");
		}
		fail(error.message);
	});
}

main().catch((error) => {
	fail(error instanceof Error ? error.message : "Unknown Astro startup failure.");
});
