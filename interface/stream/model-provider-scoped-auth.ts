import { createHash } from "node:crypto";
import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
	ModelProviderCredentialClient,
	type PiCredential,
	type ProviderCredentialFlushResponse,
	type ProviderCredentialHydrateResponse,
	type ProviderCredentialStatusResponse,
} from "./model-provider-credentials-client.js";
import { resolveProviderDefinition } from "./model-provider-definitions.js";
import {
	ensureSessionScopedPiAgentDir,
	type SessionConfigOverrides,
} from "./session-config.js";

type ScopedCredentialManifest = {
	created_by_user: string;
	agent_session_uid: string | null;
	providers: Record<
		string,
		{
			version: number;
			credential_hash: string;
			local_hash: string;
			last_flushed_at: string;
		}
	>;
};

export type ProviderCredentialOperationResult<T> =
	| {
			ok: true;
			value: T;
	  }
	| {
			ok: false;
			statusCode: number;
			error: string;
			message: string;
			body?: unknown;
			responseText?: string | null;
			url?: string | null;
	  };

export type HydratedScopedProviderCredentials = {
	scopedPiAgentDir: string;
	credentials: Record<string, PiCredential>;
	hydrate: ProviderCredentialHydrateResponse;
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function manifestPath(scopedPiAgentDir: string): string {
	return path.join(scopedPiAgentDir, ".astro-provider-credentials.json");
}

function authPath(scopedPiAgentDir: string): string {
	return path.join(scopedPiAgentDir, "auth.json");
}

function readJsonObject(filePath: string): Record<string, unknown> {
	if (!existsSync(filePath)) return {};
	try {
		const parsed = JSON.parse(readFileSync(filePath, "utf8"));
		return isPlainObject(parsed) ? parsed : {};
	} catch {
		return {};
	}
}

function readScopedManifest(scopedPiAgentDir: string): ScopedCredentialManifest | null {
	const parsed = readJsonObject(manifestPath(scopedPiAgentDir));
	if (!isPlainObject(parsed.providers)) return null;
	return {
		created_by_user: typeof parsed.created_by_user === "string" ? parsed.created_by_user : "",
		agent_session_uid:
			typeof parsed.agent_session_uid === "string" && parsed.agent_session_uid.trim()
				? parsed.agent_session_uid.trim()
				: null,
		providers: parsed.providers as ScopedCredentialManifest["providers"],
	};
}

function writeScopedManifest(scopedPiAgentDir: string, manifest: ScopedCredentialManifest) {
	writeFileSync(manifestPath(scopedPiAgentDir), `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 });
}

function canonicalJson(value: unknown): string {
	if (!isPlainObject(value)) return JSON.stringify(value);
	const sorted: Record<string, unknown> = {};
	for (const key of Object.keys(value).sort()) {
		sorted[key] = value[key];
	}
	return JSON.stringify(sorted);
}

export function hashPiCredential(credential: PiCredential): string {
	return `sha256:${createHash("sha256").update(canonicalJson(credential)).digest("hex")}`;
}

export function readScopedPiCredential(
	scopedPiAgentDir: string,
	provider: string,
): PiCredential | null {
	const authJson = readJsonObject(authPath(scopedPiAgentDir));
	const credential = authJson[provider];
	if (!isPlainObject(credential)) return null;
	if (credential.type !== "api_key" && credential.type !== "oauth") return null;
	return credential as PiCredential;
}

export function removeScopedPiCredential(scopedPiAgentDir: string, provider: string) {
	const filePath = authPath(scopedPiAgentDir);
	const authJson = readJsonObject(filePath);
	delete authJson[provider];
	writeFileSync(filePath, `${JSON.stringify(authJson, null, 2)}\n`, { mode: 0o600 });
}

export function cleanupScopedPiAgentDir(scopedPiAgentDir: string | null | undefined) {
	if (!scopedPiAgentDir) return;
	rmSync(scopedPiAgentDir, { recursive: true, force: true });
}

export async function fetchBackendProviderCredentialStatus(input: {
	createdByUser: string;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): Promise<ProviderCredentialOperationResult<ProviderCredentialStatusResponse>> {
	const client = new ModelProviderCredentialClient({ env: input.env, log: input.log });
	const result = await client.status({ createdByUser: input.createdByUser });
	if (result.ok === false) {
		return {
			ok: false,
			statusCode: result.status ?? 503,
			error: "provider_credentials_status_failed",
			message: result.error,
			body: result.body,
			responseText: result.responseText,
			url: result.url,
		};
	}
	return { ok: true, value: result.body };
}

export async function hydrateScopedProviderCredentials(input: {
	createdByUser: string;
	agentSessionUid: string | null;
	sessionKey: string;
	provider: string;
	holderId: string;
	sessionConfigOverrides: SessionConfigOverrides | null;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): Promise<ProviderCredentialOperationResult<HydratedScopedProviderCredentials>> {
	if (!resolveProviderDefinition(input.provider)) {
		return {
			ok: false,
			statusCode: 404,
			error: "provider_not_supported",
			message: `Provider "${input.provider}" is not supported by model-provider auth controls.`,
		};
	}

	const env = input.env ?? process.env;
	const client = new ModelProviderCredentialClient({ env, log: input.log });
	const result = await client.hydrate({
		createdByUser: input.createdByUser,
		agentSessionUid: input.agentSessionUid,
		providers: [input.provider],
		holderId: input.holderId,
	});
	if (result.ok === false) {
		return {
			ok: false,
			statusCode: result.status ?? 503,
			error: "provider_credentials_hydrate_failed",
			message: result.error,
			body: result.body,
			responseText: result.responseText,
			url: result.url,
		};
	}
	const hydrated = result.body.credentials[input.provider];
	if (!hydrated) {
		return {
			ok: false,
			statusCode: 409,
			error: "provider_not_authenticated",
			message: `The selected model provider "${input.provider}" is not currently authenticated.`,
		};
	}

	const credentials: Record<string, PiCredential> = {
		[input.provider]: hydrated.pi_credential,
	};
	const scopedPiAgentDir = ensureSessionScopedPiAgentDir({
		sessionKey: input.sessionKey,
		sessionConfigOverrides: input.sessionConfigOverrides,
		providerCredentials: credentials,
		env,
	});
	if (!scopedPiAgentDir) {
		return {
			ok: false,
			statusCode: 500,
			error: "provider_credentials_hydrate_failed",
			message: "Astro could not create a scoped Pi auth directory.",
		};
	}

	writeScopedManifest(scopedPiAgentDir, {
		created_by_user: input.createdByUser,
		agent_session_uid: input.agentSessionUid,
		providers: {
			[input.provider]: {
				version: hydrated.version,
				credential_hash: hydrated.credential_hash,
				local_hash: hashPiCredential(hydrated.pi_credential),
				last_flushed_at: new Date().toISOString(),
			},
		},
	});

	return {
		ok: true,
		value: {
			scopedPiAgentDir,
			credentials,
			hydrate: result.body,
		},
	};
}

export function createScopedProviderAuthDir(input: {
	scopeKey: string;
	sessionConfigOverrides?: SessionConfigOverrides | null;
	providerCredentials?: Record<string, PiCredential> | null;
	env?: NodeJS.ProcessEnv;
}): string | null {
	return ensureSessionScopedPiAgentDir({
		sessionKey: input.scopeKey,
		sessionConfigOverrides: input.sessionConfigOverrides ?? null,
		providerCredentials: input.providerCredentials ?? {},
		forceProviderAuthDir: true,
		env: input.env,
	});
}

export async function flushScopedProviderCredential(input: {
	scopedPiAgentDir: string;
	createdByUser: string;
	agentSessionUid: string | null;
	provider: string;
	reason: string;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): Promise<ProviderCredentialOperationResult<ProviderCredentialFlushResponse & { noop?: boolean }>> {
	const credential = readScopedPiCredential(input.scopedPiAgentDir, input.provider);
	if (!credential) {
		return {
			ok: false,
			statusCode: 409,
			error: "provider_credential_missing",
			message: `No scoped credential exists for provider "${input.provider}".`,
		};
	}

	const manifest = readScopedManifest(input.scopedPiAgentDir) ?? {
		created_by_user: input.createdByUser,
		agent_session_uid: input.agentSessionUid,
		providers: {},
	};
	const providerManifest = manifest.providers[input.provider];
	const localHash = hashPiCredential(credential);
	if (providerManifest?.local_hash === localHash && input.reason !== "signin_completed") {
		return {
			ok: true,
			value: {
				provider: input.provider,
				status: "active",
				version: providerManifest.version,
				credential_hash: providerManifest.credential_hash,
				accepted: true,
				noop: true,
			},
		};
	}

	const client = new ModelProviderCredentialClient({ env: input.env, log: input.log });
	const result = await client.flush({
			createdByUser: input.createdByUser,
			agentSessionUid: input.agentSessionUid,
			provider: input.provider,
			baseVersion: providerManifest?.version ?? 0,
			reason: input.reason,
			piCredential: credential,
		});
	if (result.ok === false) {
		return {
			ok: false,
			statusCode: result.status ?? 503,
			error: "provider_credentials_flush_failed",
			message: result.error,
			body: result.body,
			responseText: result.responseText,
			url: result.url,
		};
	}

	writeScopedManifest(input.scopedPiAgentDir, {
		...manifest,
		created_by_user: input.createdByUser,
		agent_session_uid: input.agentSessionUid,
		providers: {
			...manifest.providers,
			[input.provider]: {
				version: result.body.version,
				credential_hash: result.body.credential_hash,
				local_hash: localHash,
				last_flushed_at: new Date().toISOString(),
			},
		},
	});

	return { ok: true, value: result.body };
}
