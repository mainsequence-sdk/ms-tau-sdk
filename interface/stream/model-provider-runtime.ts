import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { AuthStorage, ModelRegistry } from "../../node_modules/@mariozechner/pi-coding-agent/dist/index.js";

export type RuntimeProviderAuthRecord = {
	signedOff: boolean;
	updatedAt: string;
};

export type RuntimeProviderAuthState = {
	providers?: Record<string, RuntimeProviderAuthRecord>;
};

export type ScopedPiRuntime = {
	authStorage: AuthStorage;
	modelRegistry: ModelRegistry;
};

const AUTH_STATE_FILE_NAME = "astro-model-provider-auth.json";

export function resolvePiAgentDir(env: NodeJS.ProcessEnv = process.env): string {
	const configured = env.PI_CODING_AGENT_DIR?.trim();
	if (configured === "~") return homedir();
	if (configured?.startsWith("~/")) return path.join(homedir(), configured.slice(2));
	if (configured) return path.resolve(configured);
	return path.join(homedir(), ".pi", "agent");
}

export function resolveRuntimeAuthStatePath(env: NodeJS.ProcessEnv = process.env): string {
	return path.join(resolvePiAgentDir(env), AUTH_STATE_FILE_NAME);
}

export function readRuntimeAuthState(env: NodeJS.ProcessEnv = process.env): RuntimeProviderAuthState {
	const statePath = resolveRuntimeAuthStatePath(env);
	if (!existsSync(statePath)) return {};
	try {
		const parsed = JSON.parse(readFileSync(statePath, "utf8")) as RuntimeProviderAuthState;
		return parsed && typeof parsed === "object" ? parsed : {};
	} catch {
		return {};
	}
}

export function writeRuntimeAuthState(nextState: RuntimeProviderAuthState, env: NodeJS.ProcessEnv = process.env) {
	const statePath = resolveRuntimeAuthStatePath(env);
	mkdirSync(path.dirname(statePath), { recursive: true, mode: 0o700 });
	writeFileSync(statePath, `${JSON.stringify(nextState, null, 2)}\n`, { mode: 0o600 });
}

export function getSignedOffRecord(
	provider: string,
	env: NodeJS.ProcessEnv = process.env,
): RuntimeProviderAuthRecord | null {
	const state = readRuntimeAuthState(env);
	const record = state.providers?.[provider];
	if (!record || record.signedOff !== true) return null;
	return record;
}

export function setProviderSignedOff(
	provider: string,
	signedOff: boolean,
	env: NodeJS.ProcessEnv = process.env,
) {
	const state = readRuntimeAuthState(env);
	const nextProviders = { ...(state.providers ?? {}) };
	if (signedOff) {
		nextProviders[provider] = {
			signedOff: true,
			updatedAt: new Date().toISOString(),
		};
	} else {
		delete nextProviders[provider];
	}
	writeRuntimeAuthState({ providers: nextProviders }, env);
}

export function withScopedPiAgentDir<T>(env: NodeJS.ProcessEnv, fn: () => T): T {
	const originalPiAgentDir = process.env.PI_CODING_AGENT_DIR;
	const scopedPiAgentDir = env.PI_CODING_AGENT_DIR;
	if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
		process.env.PI_CODING_AGENT_DIR = scopedPiAgentDir;
	}
	try {
		return fn();
	} finally {
		if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
			if (originalPiAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
			else process.env.PI_CODING_AGENT_DIR = originalPiAgentDir;
		}
	}
}

export async function withScopedPiAgentDirAsync<T>(
	env: NodeJS.ProcessEnv,
	fn: () => Promise<T>,
): Promise<T> {
	const originalPiAgentDir = process.env.PI_CODING_AGENT_DIR;
	const scopedPiAgentDir = env.PI_CODING_AGENT_DIR;
	if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
		process.env.PI_CODING_AGENT_DIR = scopedPiAgentDir;
	}
	try {
		return await fn();
	} finally {
		if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
			if (originalPiAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
			else process.env.PI_CODING_AGENT_DIR = originalPiAgentDir;
		}
	}
}

export function createScopedPiRuntime(env: NodeJS.ProcessEnv = process.env): ScopedPiRuntime {
	return withScopedPiAgentDir(env, () => {
		const authStorage = AuthStorage.create();
		const modelRegistry = new ModelRegistry(authStorage);
		return {
			authStorage,
			modelRegistry,
		};
	});
}
