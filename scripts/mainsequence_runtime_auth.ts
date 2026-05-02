import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export const MAINSEQUENCE_RUNTIME_CREDENTIAL_EXCHANGE_INTERVAL_ENV =
	"MAINSEQUENCE_RUNTIME_CREDENTIAL_EXCHANGE_INTERVAL_SECONDS";
export const MAINSEQUENCE_CLI_SESSION_ID_ENV = "MAINSEQUENCE_CLI_SESSION_ID";
export const MAINSEQUENCE_AUTH_MODE_ENV = "MAINSEQUENCE_AUTH_MODE";
export const MAINSEQUENCE_RUNTIME_CREDENTIAL_ID_ENV = "MAINSEQUENCE_RUNTIME_CREDENTIAL_ID";
export const MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET_ENV = "MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET";
export const MAINSEQUENCE_RUNTIME_CREDENTIAL_REQUIRED_ENV = [
	MAINSEQUENCE_RUNTIME_CREDENTIAL_ID_ENV,
	MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET_ENV,
] as const;

const DEFAULT_BACKEND = "https://api.main-sequence.app";
const DEFAULT_CLI_SESSION_ID_PREFIX = "astro-mainsequence-cli";
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_NODE_BIN_DIR = path.join(REPO_ROOT, "node_modules", ".bin");

export type MainsequenceAuthMode = "runtime_credential";

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

function getRuntimeCredentialExchangeIntervalMs(env: NodeJS.ProcessEnv = process.env): number | null {
	const raw = env[MAINSEQUENCE_RUNTIME_CREDENTIAL_EXCHANGE_INTERVAL_ENV];
	if (!raw) return null;
	const seconds = Number.parseInt(raw, 10);
	if (!Number.isFinite(seconds) || seconds <= 0) {
		throw new Error(
			`${MAINSEQUENCE_RUNTIME_CREDENTIAL_EXCHANGE_INTERVAL_ENV} must be a positive integer (seconds).`,
		);
	}
	return seconds * 1000;
}

export function getMainsequenceAuthMode(
	env: NodeJS.ProcessEnv = process.env,
): MainsequenceAuthMode {
	const raw = env[MAINSEQUENCE_AUTH_MODE_ENV]?.trim().toLowerCase();
	if (!raw || raw === "runtime_credential") return "runtime_credential";
	throw new Error(`${MAINSEQUENCE_AUTH_MODE_ENV} must be "runtime_credential".`);
}

function getMissingMainsequenceRuntimeCredentialEnv(
	env: NodeJS.ProcessEnv = process.env,
): string[] {
	return MAINSEQUENCE_RUNTIME_CREDENTIAL_REQUIRED_ENV.filter((key) => !env[key]?.trim());
}

export function validateMainsequenceRuntimeCredentialEnv(
	env: NodeJS.ProcessEnv = process.env,
) {
	const missing = getMissingMainsequenceRuntimeCredentialEnv(env);
	if (missing.length) {
		throw new Error(
			`Missing required env for Main Sequence runtime credential auth: ${missing.join(", ")}`,
		);
	}
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

function resolveMainsequenceCliSessionId(env: NodeJS.ProcessEnv = process.env): string {
	const configured = env[MAINSEQUENCE_CLI_SESSION_ID_ENV]?.trim();
	if (configured) return configured;

	const backend = resolveBackendUrl(env);
	const projectsBase = env.MAINSEQUENCE_PROJECTS_BASE?.trim() || "default-projects-base";
	return `${DEFAULT_CLI_SESSION_ID_PREFIX}:${backend}:${projectsBase}`;
}

function resolveMainsequenceConfigDir(env: NodeJS.ProcessEnv = process.env): string {
	const configuredDir = env.ASTRO_MAINSEQUENCE_CONFIG_DIR?.trim();
	if (configuredDir) return path.resolve(configuredDir);

	const homeDir = env.HOME?.trim() || homedir();
	return path.join(homeDir, ".config", "mainsequence");
}

function expandHomePath(rawPath: string, homeDir: string): string {
	if (rawPath === "~") return homeDir;
	if (rawPath.startsWith("~/")) return path.join(homeDir, rawPath.slice(2));
	return rawPath;
}

function resolveCliProjectsBasePath(env: NodeJS.ProcessEnv = process.env): string {
	const homeDir = env.HOME?.trim() || homedir();
	const rawProjectsBase = env.MAINSEQUENCE_PROJECTS_BASE?.trim() || "mainsequence";
	const expanded = expandHomePath(rawProjectsBase, homeDir);
	if (path.isAbsolute(expanded)) return path.resolve(expanded);
	if (
		expanded.startsWith(".") ||
		expanded.includes("/") ||
		expanded.includes("\\")
	) {
		return path.resolve(expanded);
	}
	return path.resolve(homeDir, expanded);
}

function readJsonObject(filePath: string): Record<string, unknown> {
	try {
		if (!existsSync(filePath)) return {};
		const parsed = JSON.parse(readFileSync(filePath, "utf8"));
		return parsed && typeof parsed === "object" && !Array.isArray(parsed)
			? parsed as Record<string, unknown>
			: {};
	} catch {
		return {};
	}
}

function writeJsonObject(filePath: string, value: Record<string, unknown>) {
	mkdirSync(path.dirname(filePath), { recursive: true });
	writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
}

function resolveCliSessionOverridePath(env: NodeJS.ProcessEnv = process.env): string {
	const configDir = resolveMainsequenceConfigDir(env);
	const sessionId = resolveMainsequenceCliSessionId(env);
	const digest = createHash("sha256").update(sessionId).digest("hex");
	return path.join(configDir, "session_overrides", `${digest}.json`);
}

function normalizeConfigPath(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function repairMainsequenceCliPathConfig(env: NodeJS.ProcessEnv = process.env) {
	const configDir = resolveMainsequenceConfigDir(env);
	const backendUrl = resolveBackendUrl(env);
	const desiredProjectsBasePath = resolveCliProjectsBasePath(env);
	mkdirSync(desiredProjectsBasePath, { recursive: true });
	mkdirSync(configDir, { recursive: true });

	const configPath = path.join(configDir, "config.json");
	const config = readJsonObject(configPath);
	const currentConfigPath = normalizeConfigPath(config.mainsequence_path);
	const shouldRewriteConfig =
		currentConfigPath !== desiredProjectsBasePath ||
		normalizeConfigPath(config.backend_url) !== backendUrl;
	if (shouldRewriteConfig) {
		writeJsonObject(configPath, {
			...config,
			backend_url: backendUrl,
			mainsequence_path: desiredProjectsBasePath,
			version: typeof config.version === "number" ? config.version : 1,
			updated_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
		});
	}

	const sessionOverridesDir = path.join(configDir, "session_overrides");
	mkdirSync(sessionOverridesDir, { recursive: true });
	for (const entry of readdirSync(sessionOverridesDir, { withFileTypes: true })) {
		if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
		const overridePath = path.join(sessionOverridesDir, entry.name);
		const override = readJsonObject(overridePath);
		const overrideProjectsPath = normalizeConfigPath(override.mainsequence_path);
		if (
			overrideProjectsPath &&
			(overrideProjectsPath.startsWith("/root/") ||
				overrideProjectsPath !== desiredProjectsBasePath)
		) {
			writeJsonObject(overridePath, {
				...override,
				backend_url: backendUrl,
				mainsequence_path: desiredProjectsBasePath,
			});
		}
	}

	writeJsonObject(resolveCliSessionOverridePath(env), {
		backend_url: backendUrl,
		mainsequence_path: desiredProjectsBasePath,
	});
}

function clearMainsequenceTokenEnv(env: NodeJS.ProcessEnv = process.env) {
	for (const key of Object.keys(env)) {
		const normalized = key.toUpperCase();
		const isMainsequenceToken =
			(normalized.startsWith("MAINSEQUENCE_") || normalized.startsWith("MAIN_SEQUENCE_")) &&
			normalized.endsWith("_TOKEN");
		if (isMainsequenceToken) delete env[key];
	}
}

function redactRuntimeCredentialText(text: string, env: NodeJS.ProcessEnv = process.env): string {
	let redacted = text;
	for (const key of MAINSEQUENCE_RUNTIME_CREDENTIAL_REQUIRED_ENV) {
		const value = env[key]?.trim();
		if (!value) continue;
		redacted = redacted.split(value).join(`[redacted:${key}]`);
	}
	return redacted;
}

function summarizeHttpBody(text: string): string {
	const trimmed = text.replace(/\s+/g, " ").trim();
	if (!trimmed) return "(empty body)";
	return trimmed.length > 500 ? `${trimmed.slice(0, 500)}...` : trimmed;
}

async function diagnoseRuntimeCredentialExchangeFailure(
	env: NodeJS.ProcessEnv = process.env,
): Promise<string | null> {
	const backendUrl = resolveBackendUrl(env);
	const credentialId = env[MAINSEQUENCE_RUNTIME_CREDENTIAL_ID_ENV]?.trim() ?? "";
	const credentialSecret = env[MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET_ENV]?.trim() ?? "";
	if (!credentialId || !credentialSecret) return null;

	try {
		const response = await fetch(`${backendUrl}/orm/api/pods/runtime-credentials/token/`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				credential_id: credentialId,
				credential_secret: credentialSecret,
			}),
		});
		if (response.ok) {
			return "A direct runtime credential probe succeeded, but CLI persistence failed.";
		}
		const body = redactRuntimeCredentialText(await response.text(), env);
		return `Direct runtime credential probe returned HTTP ${response.status}: ${summarizeHttpBody(body)}`;
	} catch (error) {
		return `Direct runtime credential probe failed: ${error instanceof Error ? error.message : String(error)}`;
	}
}

export function buildMainsequenceStoredAuthEnv(
	env: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
	const runtimeEnv = { ...env };
	const backendUrl = resolveBackendUrl(env);
	clearMainsequenceTokenEnv(runtimeEnv);
	runtimeEnv.MAINSEQUENCE_ENDPOINT = runtimeEnv.MAINSEQUENCE_ENDPOINT ?? backendUrl;
	runtimeEnv.TDAG_ENDPOINT = runtimeEnv.TDAG_ENDPOINT ?? backendUrl;
	runtimeEnv[MAINSEQUENCE_CLI_SESSION_ID_ENV] = resolveMainsequenceCliSessionId(env);
	const shimBinDir = resolveManagedMainsequenceShimBinDir(env);
	if (shimBinDir) {
		const existingPath = runtimeEnv.PATH ?? "";
		runtimeEnv.ASTRO_REAL_MAINSEQUENCE =
			runtimeEnv.ASTRO_REAL_MAINSEQUENCE ?? resolveExecutableOnPath("mainsequence", existingPath, shimBinDir) ?? "mainsequence";
		runtimeEnv.PATH = [shimBinDir, existingPath].filter(Boolean).join(path.delimiter);
	}
	if (existsSync(REPO_NODE_BIN_DIR)) {
		const existingPath = runtimeEnv.PATH ?? "";
		runtimeEnv.PATH = [REPO_NODE_BIN_DIR, existingPath].filter(Boolean).join(path.delimiter);
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

function buildMainsequenceRuntimeCredentialLoginArgs(
	env: NodeJS.ProcessEnv = process.env,
): string[] {
	const args = ["login", "--backend", resolveBackendUrl(env)];
	const projectsBase = env.MAINSEQUENCE_PROJECTS_BASE?.trim();
	if (projectsBase) args.push("--projects-base", projectsBase);
	return args;
}

function runMainsequenceRuntimeCredentialLogin(
	env: NodeJS.ProcessEnv = process.env,
): { ok: true } | { ok: false; error: string } {
	repairMainsequenceCliPathConfig(env);
	clearMainsequenceTokenEnv(env);
	const loginResult = spawnSync("mainsequence", buildMainsequenceRuntimeCredentialLoginArgs(env), {
		stdio: ["ignore", "pipe", "pipe"],
		shell: false,
		env: buildMainsequenceStoredAuthEnv(env),
		encoding: "utf8",
	});

	if (loginResult.error) {
		if ((loginResult.error as NodeJS.ErrnoException).code === "ENOENT") {
			return { ok: false, error: "Missing required command: mainsequence" };
		}
		return { ok: false, error: loginResult.error.message };
	}
	if (loginResult.status !== 0) {
		return {
			ok: false,
			error: `mainsequence runtime credential login failed (${redactRuntimeCredentialText(summarizeSpawnSyncFailure(loginResult), env)}).`,
		};
	}

	clearMainsequenceTokenEnv(env);
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
	if (stderr) details.push(`stderr: ${stderr}`);
	if (stdout) details.push(`stdout: ${stdout}`);
	return details.join("; ") || "no output";
}

export async function bootstrapMainsequenceCliAuth(options: {
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
} = {}) {
	const env = options.env ?? process.env;
	getMainsequenceAuthMode(env);
	validateMainsequenceRuntimeCredentialEnv(env);
	clearMainsequenceTokenEnv(env);
	options.log?.("Using Main Sequence runtime credential auth.");
	options.log?.("Exchanging runtime credential for Main Sequence CLI auth.");
	const loginResult = runMainsequenceRuntimeCredentialLogin(env);
	if ("error" in loginResult) {
		const detail = await diagnoseRuntimeCredentialExchangeFailure(env);
		throw new Error(
			`Main Sequence runtime credential auth failed: ${loginResult.error}${detail ? ` ${detail}` : ""}`,
		);
	}
	clearMainsequenceTokenEnv(env);
	options.log?.("Main Sequence runtime credential auth is ready.");
}

export function startMainsequenceCredentialExchangeLoop(options: {
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
} = {}) {
	const env = options.env ?? process.env;
	const intervalMs = getRuntimeCredentialExchangeIntervalMs(env);
	if (!intervalMs) return null;

	getMainsequenceAuthMode(env);
	validateMainsequenceRuntimeCredentialEnv(env);

	let exchangeInFlight = false;
	const runLogin = (reason: string) => {
		if (exchangeInFlight) return;
		exchangeInFlight = true;
		clearMainsequenceTokenEnv(env);
		const loginResult = runMainsequenceRuntimeCredentialLogin(env);
		if ("error" in loginResult) {
			exchangeInFlight = false;
			options.log?.(`Main Sequence runtime credential exchange failed (${reason}): ${loginResult.error}`);
			return;
		}
		exchangeInFlight = false;
		clearMainsequenceTokenEnv(env);
	};

	options.log?.(
		`Starting Main Sequence runtime credential exchange loop every ${Math.floor(intervalMs / 1000)}s.`,
	);
	const timer = setInterval(() => {
		runLogin("interval");
	}, intervalMs);
	return {
		stop: () => clearInterval(timer),
	};
}
