import { existsSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";

export const MAINSEQUENCE_REFRESH_INTERVAL_ENV = "MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS";
export const MAINSEQUENCE_CLI_SESSION_ID_ENV = "MAINSEQUENCE_CLI_SESSION_ID";
export const MAINSEQUENCE_REQUIRED_ENV = [
	"MAINSEQUENCE_BACKEND",
	"MAINSEQUENCE_PROJECTS_BASE",
] as const;
const DEFAULT_BACKEND = "https://api.main-sequence.app";
const DEFAULT_CLI_SESSION_ID_PREFIX = "astro-mainsequence-cli";

type MainsequenceCredentials = {
	accessToken: string | null;
	refreshToken: string | null;
};

export function loadEnvFile(repoRoot: string, env: NodeJS.ProcessEnv = process.env) {
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
		if (env[key] === undefined) {
			env[key] = value;
		}
	}
}

function getRefreshIntervalMs(env: NodeJS.ProcessEnv = process.env): number | null {
	const raw = env[MAINSEQUENCE_REFRESH_INTERVAL_ENV];
	if (!raw) return null;
	const seconds = Number.parseInt(raw, 10);
	if (!Number.isFinite(seconds) || seconds <= 0) {
		throw new Error(`${MAINSEQUENCE_REFRESH_INTERVAL_ENV} must be a positive integer (seconds).`);
	}
	return seconds * 1000;
}

function getMissingMainsequenceEnv(env: NodeJS.ProcessEnv = process.env): string[] {
	return MAINSEQUENCE_REQUIRED_ENV.filter((key) => !env[key]);
}

function resolveBackendUrl(env: NodeJS.ProcessEnv = process.env): string {
	const raw =
		env.MAINSEQUENCE_BACKEND ||
		env.MAIN_SEQUENCE_BACKEND_URL ||
		env.MAINSEQUENCE_ENDPOINT ||
		env.TDAG_ENDPOINT ||
		DEFAULT_BACKEND;

	return raw.replace(/\/+$/, "");
}

function normalizeMainsequenceToken(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function resolveMainsequenceCliSessionId(env: NodeJS.ProcessEnv = process.env): string {
	const configured = env[MAINSEQUENCE_CLI_SESSION_ID_ENV]?.trim();
	if (configured) {
		return configured;
	}

	const backend = resolveBackendUrl(env);
	const projectsBase = env.MAINSEQUENCE_PROJECTS_BASE?.trim() || "default-projects-base";
	return `${DEFAULT_CLI_SESSION_ID_PREFIX}:${backend}:${projectsBase}`;
}

function buildCredentialsKey(credentials: MainsequenceCredentials): string | null {
	if (!credentials.accessToken || !credentials.refreshToken) return null;
	return `${credentials.accessToken}:${credentials.refreshToken}`;
}

function resolveMainsequenceAuthStorePath(env: NodeJS.ProcessEnv = process.env): string {
	const configuredDir = env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim();
	if (configuredDir) {
		return path.join(path.resolve(configuredDir), "auth.json");
	}

	const homeDir = env.HOME?.trim() || homedir();
	return path.join(homeDir, ".config", "mainsequence", "auth.json");
}

function readPersistedMainsequenceAuth(env: NodeJS.ProcessEnv = process.env): MainsequenceCredentials {
	const authPath = resolveMainsequenceAuthStorePath(env);
	if (!existsSync(authPath)) {
		return {
			accessToken: null,
			refreshToken: null,
		};
	}

	try {
		const parsed = JSON.parse(readFileSync(authPath, "utf8")) as Record<string, unknown>;
		return {
			accessToken: normalizeMainsequenceToken(parsed.access),
			refreshToken: normalizeMainsequenceToken(parsed.refresh),
		};
	} catch {
		return {
			accessToken: null,
			refreshToken: null,
		};
	}
}

function syncEnvFromPersistedMainsequenceAuth(
	env: NodeJS.ProcessEnv = process.env,
	fallback?: MainsequenceCredentials,
) {
	const persisted = readPersistedMainsequenceAuth(env);
	const nextAccessToken = persisted.accessToken ?? fallback?.accessToken ?? null;
	const nextRefreshToken = persisted.refreshToken ?? fallback?.refreshToken ?? null;

	if (nextAccessToken) env.MAINSEQUENCE_ACCESS_TOKEN = nextAccessToken;
	if (nextRefreshToken) env.MAINSEQUENCE_REFRESH_TOKEN = nextRefreshToken;
}

export function buildMainsequenceStoredAuthEnv(
	env: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
	const runtimeEnv = { ...env };
	delete runtimeEnv.MAINSEQUENCE_ACCESS_TOKEN;
	delete runtimeEnv.MAINSEQUENCE_REFRESH_TOKEN;
	runtimeEnv[MAINSEQUENCE_CLI_SESSION_ID_ENV] = resolveMainsequenceCliSessionId(env);
	const shimBinDir = resolveManagedMainsequenceShimBinDir(env);
	if (shimBinDir) {
		const existingPath = runtimeEnv.PATH ?? "";
		runtimeEnv.ASTRO_REAL_MAINSEQUENCE =
			runtimeEnv.ASTRO_REAL_MAINSEQUENCE ?? resolveExecutableOnPath("mainsequence", existingPath, shimBinDir) ?? "mainsequence";
		runtimeEnv.PATH = [shimBinDir, existingPath].filter(Boolean).join(path.delimiter);
	}
	return runtimeEnv;
}

function resolveManagedMainsequenceShimBinDir(env: NodeJS.ProcessEnv = process.env): string | null {
	const configuredAgentDir = env.PI_CODING_AGENT_DIR?.trim();
	if (configuredAgentDir) return path.join(path.resolve(configuredAgentDir), "bin");

	const configuredRoot = env.ASTRO_CONTAINER_DATA_DIR?.trim();
	if (configuredRoot) return path.join(path.resolve(configuredRoot), ".pi", "agent", "bin");

	const homeDir = env.HOME?.trim() || homedir();
	return path.join(homeDir, ".pi", "agent", "bin");
}

function resolveExecutableOnPath(
	command: string,
	pathValue: string,
	shimBinDir: string | null,
): string | null {
	const shimDir = shimBinDir ? path.resolve(shimBinDir) : null;
	for (const entry of pathValue.split(path.delimiter)) {
		const trimmed = entry.trim();
		if (!trimmed) continue;
		const resolvedEntry = path.resolve(trimmed);
		if (shimDir && resolvedEntry === shimDir) continue;
		const candidate = path.join(resolvedEntry, command);
		if (!existsSync(candidate)) continue;
		try {
			if (!statSync(candidate).isFile()) continue;
		} catch {
			continue;
		}
		return candidate;
	}
	return null;
}

function buildMainsequenceLoginArgs(
	credentials: MainsequenceCredentials,
	env: NodeJS.ProcessEnv = process.env,
): string[] {
	return [
		"login",
		"--access-token",
		credentials.accessToken ?? "",
		"--refresh-token",
		credentials.refreshToken ?? "",
		"--backend",
		env.MAINSEQUENCE_BACKEND ?? "",
		"--projects-base",
		env.MAINSEQUENCE_PROJECTS_BASE ?? "",
	];
}

function runMainsequenceLogin(options: {
	env?: NodeJS.ProcessEnv;
	credentials: MainsequenceCredentials;
}): { ok: true } | { ok: false; error: string } {
	const env = options.env ?? process.env;
	const loginResult = spawnSync("mainsequence", buildMainsequenceLoginArgs(options.credentials, env), {
		stdio: "inherit",
		shell: false,
		env: buildMainsequenceStoredAuthEnv(env),
	});

	if (loginResult.error) {
		if ((loginResult.error as NodeJS.ErrnoException).code === "ENOENT") {
			return { ok: false, error: "Missing required command: mainsequence" };
		}
		return { ok: false, error: loginResult.error.message };
	}
	if (loginResult.status !== 0) {
		return { ok: false, error: `mainsequence login failed with code ${loginResult.status ?? 1}.` };
	}

	return { ok: true };
}

function summarizeSpawnSyncFailure(result: {
	status: number | null;
	signal: NodeJS.Signals | null;
	stdout?: string | Buffer | null;
	stderr?: string | Buffer | null;
}): string {
	const details: string[] = [];
	if (typeof result.status === "number") {
		details.push(`exit code ${result.status}`);
	} else if (result.signal) {
		details.push(`signal ${result.signal}`);
	}
	const stderr = typeof result.stderr === "string" ? result.stderr.trim() : result.stderr?.toString("utf8").trim();
	const stdout = typeof result.stdout === "string" ? result.stdout.trim() : result.stdout?.toString("utf8").trim();
	if (stderr) {
		details.push(`stderr: ${stderr}`);
	}
	if (stdout) {
		details.push(`stdout: ${stdout}`);
	}
	return details.join("; ") || "no output";
}

function verifyMainsequenceCliAuthStore(
	env: NodeJS.ProcessEnv = process.env,
): { ok: true } | { ok: false; error: string } {
	const verifyResult = spawnSync("mainsequence", ["user"], {
		stdio: ["ignore", "pipe", "pipe"],
		shell: false,
		env: buildMainsequenceStoredAuthEnv(env),
		encoding: "utf8",
	});
	if (verifyResult.error) {
		if ((verifyResult.error as NodeJS.ErrnoException).code === "ENOENT") {
			return { ok: false, error: "Missing required command: mainsequence" };
		}
		return { ok: false, error: verifyResult.error.message };
	}
	if (verifyResult.status !== 0) {
		return {
			ok: false,
			error: `Main Sequence CLI login completed but persisted auth still failed verification (${summarizeSpawnSyncFailure(verifyResult)}).`,
		};
	}

	return { ok: true };
}

function isRefreshFailure(
	result: Awaited<ReturnType<typeof refreshMainsequenceAccessToken>>,
): result is { ok: false; error: string } {
	return result.ok === false;
}

async function refreshMainsequenceAccessToken(
	env: NodeJS.ProcessEnv = process.env,
	options: { persistToEnv?: boolean; refreshToken?: string | null } = {},
): Promise<{ ok: true; accessToken: string; refreshToken: string } | { ok: false; error: string }> {
	const refreshToken =
		normalizeMainsequenceToken(options.refreshToken) ??
		normalizeMainsequenceToken(env.MAINSEQUENCE_REFRESH_TOKEN);
	if (!refreshToken) {
		return { ok: false, error: "Missing refresh token." };
	}

	const response = await fetch(`${resolveBackendUrl(env)}/auth/jwt-token/token/refresh/`, {
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
			error: body.trim() || `Refresh failed with status ${response.status}.`,
		};
	}

	const payload = await response.json();
	const accessToken =
		normalizeMainsequenceToken(payload?.access);
	if (!accessToken) {
		return { ok: false, error: "Refresh response did not include access token." };
	}
	const nextRefreshToken = normalizeMainsequenceToken(payload?.refresh) ?? refreshToken;

	if (options.persistToEnv !== false) {
		env.MAINSEQUENCE_ACCESS_TOKEN = accessToken;
		env.MAINSEQUENCE_REFRESH_TOKEN = nextRefreshToken;
	}
	return { ok: true, accessToken, refreshToken: nextRefreshToken };
}

export async function bootstrapMainsequenceCliAuth(options: {
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
} = {}) {
	const env = options.env ?? process.env;
	const missing = getMissingMainsequenceEnv(env);
	if (missing.length) {
		throw new Error(`Missing required env for deterministic Main Sequence login: ${missing.join(", ")}`);
	}

	const configuredCredentials: MainsequenceCredentials = {
		accessToken: normalizeMainsequenceToken(env.MAINSEQUENCE_ACCESS_TOKEN),
		refreshToken: normalizeMainsequenceToken(env.MAINSEQUENCE_REFRESH_TOKEN),
	};
	const persistedCredentials = readPersistedMainsequenceAuth(env);
	if (!configuredCredentials.refreshToken && !persistedCredentials.refreshToken) {
		throw new Error(
			"Missing required Main Sequence refresh token in both env and the persisted CLI auth store.",
		);
	}

	const attemptedErrors: string[] = [];
	const attemptedCredentials = new Set<string>();

	const tryLogin = (label: string, credentials: MainsequenceCredentials) => {
		const credentialsKey = buildCredentialsKey(credentials);
		if (!credentialsKey || attemptedCredentials.has(credentialsKey)) {
			return false;
		}

		attemptedCredentials.add(credentialsKey);
		options.log?.(`Running deterministic Main Sequence CLI login with ${label}.`);
		const loginResult = runMainsequenceLogin({ env, credentials });
		if ("error" in loginResult) {
			attemptedErrors.push(`${label}: ${loginResult.error}`);
			return false;
		}

			const verifyResult = verifyMainsequenceCliAuthStore(env);
			if ("error" in verifyResult) {
				attemptedErrors.push(`${label}: ${verifyResult.error}`);
				return false;
			}

			syncEnvFromPersistedMainsequenceAuth(env, credentials);
			options.log?.("Main Sequence CLI auth is ready.");
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
		(candidate, index, list): candidate is { label: string; refreshToken: string } =>
			Boolean(candidate.refreshToken) &&
			list.findIndex((entry) => entry.refreshToken === candidate.refreshToken) === index,
	);

	if (refreshCandidates.length > 0) {
		options.log?.("Refreshing the Main Sequence access token before deterministic CLI login.");
	}
	for (const candidate of refreshCandidates) {
		const refreshResult = await refreshMainsequenceAccessToken(env, {
			persistToEnv: false,
			refreshToken: candidate.refreshToken,
		});
		if (isRefreshFailure(refreshResult)) {
			attemptedErrors.push(`${candidate.label}: ${refreshResult.error}`);
			continue;
		}

		if (
			tryLogin(`a freshly refreshed access token from the ${candidate.label}`, {
				accessToken: refreshResult.accessToken,
				refreshToken: refreshResult.refreshToken,
			})
		) {
			return;
		}
	}

	if (tryLogin("configured environment tokens", configuredCredentials)) {
		return;
	}

	if (tryLogin("persisted CLI auth store", persistedCredentials)) {
		return;
	}

	throw new Error(
		`Deterministic Main Sequence login failed: ${attemptedErrors.join(" | ") || "unknown failure."}`,
	);
}

export function startMainsequenceRefreshLoop(options: {
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
} = {}) {
	const env = options.env ?? process.env;
	const intervalMs = getRefreshIntervalMs(env);
	if (!intervalMs) return null;

	const missing = getMissingMainsequenceEnv(env);
	if (missing.length) {
		throw new Error(`Missing required env for token refresh: ${missing.join(", ")}`);
	}

	let refreshInFlight = false;
	const runLogin = async (reason: string) => {
		if (refreshInFlight) return;
		refreshInFlight = true;

		const refreshResult = await refreshMainsequenceAccessToken(env);
		if (isRefreshFailure(refreshResult)) {
			refreshInFlight = false;
			options.log?.(`Main Sequence access-token refresh failed (${reason}): ${refreshResult.error}`);
			return;
		}

		const child = spawn(
			"mainsequence",
			buildMainsequenceLoginArgs(
				{
					accessToken: normalizeMainsequenceToken(env.MAINSEQUENCE_ACCESS_TOKEN),
					refreshToken: normalizeMainsequenceToken(env.MAINSEQUENCE_REFRESH_TOKEN),
				},
				env,
			),
			{
				stdio: "inherit",
				shell: false,
				env: buildMainsequenceStoredAuthEnv(env),
			},
		);

		child.on("exit", (code) => {
			refreshInFlight = false;
			if (typeof code === "number" && code !== 0) {
				options.log?.(`mainsequence login failed (${reason}) with code ${code}.`);
				return;
			}
			const verifyResult = verifyMainsequenceCliAuthStore(env);
			if ("error" in verifyResult) {
				options.log?.(`persisted Main Sequence CLI auth verification failed (${reason}): ${verifyResult.error}`);
				return;
			}
			syncEnvFromPersistedMainsequenceAuth(env);
		});

		child.on("error", (error: NodeJS.ErrnoException) => {
			refreshInFlight = false;
			if (error.code === "ENOENT") {
				options.log?.("Missing required command: mainsequence");
				return;
			}
			options.log?.(`mainsequence login error (${reason}): ${error.message}`);
		});
	};

	options.log?.(`Starting Main Sequence token refresh loop every ${Math.floor(intervalMs / 1000)}s.`);
	const timer = setInterval(() => {
		void runLogin("interval");
	}, intervalMs);
	return {
		stop: () => clearInterval(timer),
	};
}
