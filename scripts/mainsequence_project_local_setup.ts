import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import {
	existsSync,
	lstatSync,
	mkdirSync,
	readFileSync,
	readlinkSync,
	readdirSync,
	rmSync,
	symlinkSync,
	writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { bootstrapPiAgentDir } from "./bootstrap_pi_agent_dir.mjs";
import { buildMainsequenceStoredAuthEnv, loadEnvFile } from "./mainsequence_runtime_auth.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(__dirname, "..");
const DEFAULT_RETRY_DELAYS_MS = [1000, 2000, 5000];
const DEFAULT_BACKEND = "https://api.main-sequence.app";
const RUNTIME_CREDENTIAL_JWT_FAILURE_PATTERN =
	/JWT session tokens are missing\.\s*Run:\s*mainsequence login/i;
const TRANSIENT_FAILURE_PATTERNS = [
	/host key verification failed/i,
	/permission denied \(publickey\)/i,
	/repository not found/i,
] as const;
const MANAGED_PROJECT_ENV_KEYS = new Set([
	"MAINSEQUENCE_ACCESS_TOKEN",
	"MAIN_SEQUENCE_ACCESS_TOKEN",
	"MAINSEQUENCE_REFRESH_TOKEN",
	"MAIN_SEQUENCE_REFRESH_TOKEN",
	"MAINSEQUENCE_AUTH_MODE",
	"MAINSEQUENCE_RUNTIME_CREDENTIAL_ID",
	"MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET",
	"MAINSEQUENCE_BACKEND",
	"MAIN_SEQUENCE_BACKEND_URL",
	"MAINSEQUENCE_ENDPOINT",
	"TDAG_ENDPOINT",
	"MAINSEQUENCE_PROJECT_ID",
	"MAIN_SEQUENCE_PROJECT_ID",
]);

export type ProjectScopedCheckoutHome = {
	projectHome: string;
	sshDir: string;
	knownHostsPath: string;
	sshConfigPath: string;
};

export type ProjectLocalSetupResult = {
	projectId: string;
	checkoutDir: string | null;
	checkoutHome: ProjectScopedCheckoutHome;
	stdout: string;
	stderr: string;
	combinedOutput: string;
};

export class ExitCodeError extends Error {
	exitCode: number;

	constructor(exitCode: number, message = "") {
		super(message);
		this.name = "ExitCodeError";
		this.exitCode = exitCode;
	}
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

export function extractProjectId(args: string[]): string | null {
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

export function ensureProjectScopedCheckoutHome(projectId: string): ProjectScopedCheckoutHome {
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

export function buildProjectScopedProcessEnv(projectHome: string): NodeJS.ProcessEnv {
	return {
		...buildMainsequenceStoredAuthEnv(process.env),
		ASTRO_MAINSEQUENCE_BYPASS_SHIM: "1",
		HOME: projectHome,
		USERPROFILE: projectHome,
		XDG_CONFIG_HOME: path.join(projectHome, ".config"),
	};
}

function isTransientProjectSetupFailure(output: string): boolean {
	return TRANSIENT_FAILURE_PATTERNS.some((pattern) => pattern.test(output));
}

function isRuntimeCredentialAuthMode(env: NodeJS.ProcessEnv = process.env): boolean {
	const raw = env.MAINSEQUENCE_AUTH_MODE?.trim().toLowerCase();
	return !raw || raw === "runtime_credential";
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

function extractCheckoutTargetDir(output: string): string | null {
	const cloneMatch = output.match(/Cloning into ['"]([^'"]+)['"]/i);
	if (cloneMatch?.[1]) return path.resolve(cloneMatch[1]);
	const targetExistsMatch = output.match(/Target already exists:\s*([^\r\n]+)/i);
	if (targetExistsMatch?.[1]) return path.resolve(targetExistsMatch[1].trim());
	return null;
}

function summarizeHttpBody(text: string): string {
	const trimmed = text.replace(/\s+/g, " ").trim();
	if (!trimmed) return "(empty body)";
	return trimmed.length > 500 ? `${trimmed.slice(0, 500)}...` : trimmed;
}

async function exchangeRuntimeCredentialAccessToken(): Promise<string> {
	const credentialId = process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_ID?.trim();
	const credentialSecret = process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET?.trim();
	if (!credentialId || !credentialSecret) {
		throw new Error(
			"Missing MAINSEQUENCE_RUNTIME_CREDENTIAL_ID or MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET.",
		);
	}
	const backendUrl = resolveBackendUrl();
	const response = await fetch(`${backendUrl}/orm/api/pods/runtime-credentials/token/`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			credential_id: credentialId,
			credential_secret: credentialSecret,
		}),
	});
	const bodyText = await response.text();
	if (!response.ok) {
		throw new Error(
			`Runtime credential exchange failed while provisioning project .env (HTTP ${response.status}: ${summarizeHttpBody(bodyText)}).`,
		);
	}
	let body: unknown;
	try {
		body = JSON.parse(bodyText);
	} catch {
		throw new Error("Runtime credential exchange response was not valid JSON.");
	}
	if (!body || typeof body !== "object" || typeof (body as { access?: unknown }).access !== "string") {
		throw new Error("Runtime credential exchange response did not include an access token.");
	}
	return (body as { access: string }).access;
}

function formatEnvValue(value: unknown): string {
	const stringValue = value == null ? "" : String(value);
	if (/^[A-Za-z0-9_@%+=:,./-]*$/.test(stringValue)) return stringValue;
	return JSON.stringify(stringValue);
}

function renderEnvObject(value: Record<string, unknown>): string {
	return Object.entries(value)
		.filter(([key]) => key.trim().length > 0)
		.map(([key, entryValue]) => `${key}=${formatEnvValue(entryValue)}`)
		.join("\n");
}

function extractProjectEnvironmentText(bodyText: string): string {
	try {
		const parsed = JSON.parse(bodyText);
		if (typeof parsed === "string") return parsed;
		if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
			const object = parsed as Record<string, unknown>;
			for (const key of ["environment", "env_text", "env", "content", "text"]) {
				if (typeof object[key] === "string") return object[key];
			}
			if (object.environment && typeof object.environment === "object" && !Array.isArray(object.environment)) {
				return renderEnvObject(object.environment as Record<string, unknown>);
			}
			if (object.env && typeof object.env === "object" && !Array.isArray(object.env)) {
				return renderEnvObject(object.env as Record<string, unknown>);
			}
		}
	} catch {
		// The endpoint may legitimately return raw dotenv text.
	}
	return bodyText;
}

async function fetchProjectEnvironmentText(projectId: string, accessToken: string): Promise<string> {
	const backendUrl = resolveBackendUrl();
	const response = await fetch(
		`${backendUrl}/orm/api/pods/projects/${encodeURIComponent(projectId)}/get_environment/`,
		{
			headers: {
				Authorization: `Bearer ${accessToken}`,
				Accept: "application/json, text/plain",
			},
		},
	);
	const bodyText = await response.text();
	if (!response.ok) {
		throw new Error(
			`Project environment fetch failed (HTTP ${response.status}: ${summarizeHttpBody(bodyText)}).`,
		);
	}
	return extractProjectEnvironmentText(bodyText);
}

function stripManagedProjectEnvLines(envText: string): string {
	return envText
		.split(/\r?\n/)
		.filter((line) => {
			const trimmed = line.trim();
			if (!trimmed || trimmed.startsWith("#")) return true;
			const equalsIndex = trimmed.indexOf("=");
			if (equalsIndex === -1) return true;
			const key = trimmed.slice(0, equalsIndex).trim();
			return !MANAGED_PROJECT_ENV_KEYS.has(key);
		})
		.join("\n")
		.replace(/\s+$/u, "");
}

function renderRuntimeCredentialProjectEnv(envText: string, projectId: string): string {
	const backendUrl = resolveBackendUrl();
	const strippedEnvText = stripManagedProjectEnvLines(envText);
	const lines = strippedEnvText
		? strippedEnvText
				.split(/\r?\n/)
				.filter((line, index, allLines) => line.trim() || index < allLines.length - 1)
		: [];
	lines.push(
		"",
		"# Managed by Astro for local project runtime auth.",
		"MAINSEQUENCE_AUTH_MODE=runtime_credential",
		`MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=${formatEnvValue(process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_ID ?? "")}`,
		`MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=${formatEnvValue(process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET ?? "")}`,
		`MAINSEQUENCE_BACKEND=${formatEnvValue(backendUrl)}`,
		`MAIN_SEQUENCE_BACKEND_URL=${formatEnvValue(backendUrl)}`,
		`MAINSEQUENCE_ENDPOINT=${formatEnvValue(backendUrl)}`,
		`TDAG_ENDPOINT=${formatEnvValue(backendUrl)}`,
		`MAINSEQUENCE_PROJECT_ID=${formatEnvValue(projectId)}`,
		`MAIN_SEQUENCE_PROJECT_ID=${formatEnvValue(projectId)}`,
		"",
	);
	return lines.join("\n");
}

async function completeRuntimeCredentialProjectEnvFallback(
	projectId: string,
	output: string,
): Promise<boolean> {
	if (!isRuntimeCredentialAuthMode() || !RUNTIME_CREDENTIAL_JWT_FAILURE_PATTERN.test(output)) {
		return false;
	}
	const targetDir = extractCheckoutTargetDir(output);
	if (!targetDir || !existsSync(targetDir)) {
		throw new Error(
			"Main Sequence SDK requested JWT session tokens, but Astro could not locate the checked-out project directory for runtime credential .env provisioning.",
		);
	}
	const accessToken = await exchangeRuntimeCredentialAccessToken();
	const projectEnvText = await fetchProjectEnvironmentText(projectId, accessToken);
	writeFileSync(path.join(targetDir, ".env"), renderRuntimeCredentialProjectEnv(projectEnvText, projectId), {
		mode: 0o600,
	});
	process.stderr.write(
		`[astro] Main Sequence SDK project setup requested JWT session tokens; Astro completed runtime credential .env provisioning in ${targetDir}.\n`,
	);
	return true;
}

function writeCommandOutput(stdout: string, stderr: string) {
	if (stdout.length > 0) process.stdout.write(stdout);
	if (stderr.length > 0) process.stderr.write(stderr);
}

function projectEnvMentionsId(envPath: string, projectId: string): boolean {
	try {
		const envText = readFileSync(envPath, "utf8");
		return (
			envText.includes(`MAINSEQUENCE_PROJECT_ID=${projectId}`) ||
			envText.includes(`MAIN_SEQUENCE_PROJECT_ID=${projectId}`)
		);
	} catch {
		return false;
	}
}

function findCheckoutDirByProjectId(rootDir: string, projectId: string, depth = 0): string | null {
	if (!existsSync(rootDir) || depth > 6) return null;

	let entries;
	try {
		entries = readdirSync(rootDir, { withFileTypes: true });
	} catch {
		return null;
	}

	const envPath = path.join(rootDir, ".env");
	const gitPath = path.join(rootDir, ".git");
	if (existsSync(envPath) && existsSync(gitPath) && projectEnvMentionsId(envPath, projectId)) {
		return rootDir;
	}

	for (const entry of entries) {
		if (!entry.isDirectory() || entry.name.startsWith(".")) continue;
		const candidate = findCheckoutDirByProjectId(path.join(rootDir, entry.name), projectId, depth + 1);
		if (candidate) return candidate;
	}

	return null;
}

export async function setupProjectLocally(
	forwardedArgs: string[],
	options: { streamOutput?: boolean } = {},
): Promise<ProjectLocalSetupResult> {
	bootstrapPiAgentDir();
	loadEnvFile(repoRoot);
	const mainsequenceCommand = process.env.ASTRO_REAL_MAINSEQUENCE || "mainsequence";
	const projectId = extractProjectId(forwardedArgs);
	if (!projectId) {
		throw new Error("Could not determine the Main Sequence project id for local setup.");
	}
	const checkoutHome = ensureProjectScopedCheckoutHome(projectId);

	const maxAttempts = DEFAULT_RETRY_DELAYS_MS.length + 1;
	for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
		const result = spawnSync(mainsequenceCommand, ["project", "set-up-locally", ...forwardedArgs], {
			cwd: repoRoot,
			shell: false,
			env: buildProjectScopedProcessEnv(checkoutHome.projectHome),
			encoding: "utf8",
		});

		const stdout = typeof result.stdout === "string" ? result.stdout : "";
		const stderr = typeof result.stderr === "string" ? result.stderr : "";
		const combinedOutput = `${stdout}\n${stderr}`;

		if (result.error) {
			const error = result.error as NodeJS.ErrnoException;
			if (error.code === "ENOENT") {
				throw new Error("Missing required command: mainsequence");
			}
			throw error;
		}

		if ((result.status ?? 1) === 0) {
			if (options.streamOutput) writeCommandOutput(stdout, stderr);
			return {
				projectId,
				checkoutDir:
					extractCheckoutTargetDir(combinedOutput) ??
					findCheckoutDirByProjectId(checkoutHome.projectHome, projectId),
				checkoutHome,
				stdout,
				stderr,
				combinedOutput,
			};
		}

		try {
			if (await completeRuntimeCredentialProjectEnvFallback(projectId, combinedOutput)) {
				if (options.streamOutput) writeCommandOutput(stdout, stderr);
				return {
					projectId,
					checkoutDir:
						extractCheckoutTargetDir(combinedOutput) ??
						findCheckoutDirByProjectId(checkoutHome.projectHome, projectId),
					checkoutHome,
					stdout,
					stderr,
					combinedOutput,
				};
			}
		} catch (error) {
			throw error instanceof Error ? error : new Error(String(error));
		}

		const shouldRetry = attempt < maxAttempts && isTransientProjectSetupFailure(combinedOutput);
		if (!shouldRetry) {
			if (options.streamOutput) writeCommandOutput(stdout, stderr);
			throw new ExitCodeError(result.status ?? 1);
		}

		const delayMs = DEFAULT_RETRY_DELAYS_MS[attempt - 1] ?? DEFAULT_RETRY_DELAYS_MS.at(-1) ?? 1000;
		process.stderr.write(
			`[astro] Transient SSH/deploy-key failure while setting up the project locally. Retrying in ${delayMs}ms (attempt ${attempt + 1}/${maxAttempts}).\n`,
		);
		await sleep(delayMs);
	}

	throw new ExitCodeError(1, "Project local setup exhausted all retries.");
}
