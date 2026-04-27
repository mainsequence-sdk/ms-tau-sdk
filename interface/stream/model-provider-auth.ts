import { AuthStorage, ModelRegistry } from "../../node_modules/@mariozechner/pi-coding-agent/dist/index.js";
import {
	listAuthBackedProviderDefinitions,
	resolveProviderDefinition,
	shouldExposeAuthBackedProviderInAstroUi,
	type AuthBackedProviderDefinition,
} from "./model-provider-definitions.js";
import { ModelProviderCredentialClient } from "./model-provider-credentials-client.js";
import {
	fetchBackendProviderCredentialStatus,
	type ProviderCredentialOperationResult,
} from "./model-provider-scoped-auth.js";
import { cancelActiveProviderSignIn } from "./model-provider-signin.js";

export type ModelProviderAuthSource = "backend" | null;

export type ModelProviderAuthMetadata = {
	required: true;
	authKind: "api_key" | "oauth";
	signInAvailable: boolean;
	authenticated: boolean;
	usable: boolean;
	authSource: ModelProviderAuthSource;
};

export type ModelProviderAuthStatus = {
	provider: string;
	authKind: "api_key" | "oauth";
	signInAvailable: boolean;
	authenticated: boolean;
	authSource: ModelProviderAuthSource;
	knownModelCount: number;
	usableModelCount: number;
	lastValidatedAt: string;
	version: number | null;
	credentialHash: string | null;
};

export type ModelProviderAuthResponse = {
	version: 1;
	providers: ModelProviderAuthStatus[];
	backendCredentialStatus?: {
		ok: boolean;
		error?: string;
	};
};

function buildProviderAuthStatus(options: {
	definition: AuthBackedProviderDefinition;
	knownModelCount: number;
	backendStatus: {
		status: "active" | "revoked";
		version: number;
		credential_hash: string;
	} | null;
	env: NodeJS.ProcessEnv;
}): ModelProviderAuthStatus {
	const authenticated = options.backendStatus?.status === "active";
	const signInAvailable =
		!authenticated &&
		(options.definition.signInMode === "oauth" ||
			(options.definition.signInMode === "api_key_sync" && options.definition.isConfiguredFromEnv(options.env)));

	return {
		provider: options.definition.provider,
		authKind: options.definition.authKind,
		signInAvailable,
		authenticated,
		authSource: authenticated ? "backend" : null,
		knownModelCount: options.knownModelCount,
		usableModelCount: authenticated ? options.knownModelCount : 0,
		lastValidatedAt: new Date().toISOString(),
		version: options.backendStatus?.version ?? null,
		credentialHash: options.backendStatus?.credential_hash ?? null,
	};
}

function collectKnownModelCounts(modelRegistry: ModelRegistry): Map<string, number> {
	const counts = new Map<string, number>();
	for (const model of modelRegistry.getAll()) {
		counts.set(model.provider, (counts.get(model.provider) ?? 0) + 1);
	}
	return counts;
}

function shouldIncludeProviderCandidate(options: {
	definition: AuthBackedProviderDefinition;
	knownModelCounts: Map<string, number>;
	hasBackendStatus: boolean;
	env: NodeJS.ProcessEnv;
}): boolean {
	if ((options.knownModelCounts.get(options.definition.provider) ?? 0) > 0) return true;
	if (options.hasBackendStatus) return true;
	if (options.definition.signInMode === "oauth") return true;
	if (options.definition.signInMode === "api_key_sync" && options.definition.isConfiguredFromEnv(options.env)) {
		return true;
	}
	return false;
}

export function createUnauthenticatedPiRuntime(): { authStorage: AuthStorage; modelRegistry: ModelRegistry } {
	const authStorage = AuthStorage.inMemory();
	const modelRegistry = new ModelRegistry(authStorage);
	return { authStorage, modelRegistry };
}

export async function listModelProviderAuthStatuses(options: {
	createdByUser: string | null;
	env?: NodeJS.ProcessEnv;
	modelRegistry?: ModelRegistry;
}): Promise<ProviderCredentialOperationResult<ModelProviderAuthStatus[]>> {
	const env = options.env ?? process.env;
	const runtime = options.modelRegistry ? { modelRegistry: options.modelRegistry } : createUnauthenticatedPiRuntime();
	const knownModelCounts = collectKnownModelCounts(runtime.modelRegistry);
	const backendStatus =
		options.createdByUser == null
			? {
					ok: true as const,
					value: {
						providers: {},
					},
			  }
			: await fetchBackendProviderCredentialStatus({
					createdByUser: options.createdByUser,
					env,
			  });
	if (backendStatus.ok === false) {
		return backendStatus;
	}

	const statuses = listAuthBackedProviderDefinitions()
		.filter((definition) => definition.exposeInAstroUi)
		.filter((definition) =>
			shouldIncludeProviderCandidate({
				definition,
				knownModelCounts,
				hasBackendStatus: Boolean(backendStatus.value.providers[definition.provider]),
				env,
			}),
		)
		.map((definition) =>
			buildProviderAuthStatus({
				definition,
				knownModelCount: knownModelCounts.get(definition.provider) ?? 0,
				backendStatus: backendStatus.value.providers[definition.provider] ?? null,
				env,
			}),
		)
		.sort((left, right) => left.provider.localeCompare(right.provider));

	return { ok: true, value: statuses };
}

export async function getModelProviderAuthMetadata(
	provider: string,
	options: {
		createdByUser: string | null;
		env?: NodeJS.ProcessEnv;
		modelRegistry?: ModelRegistry;
	},
): Promise<ModelProviderAuthMetadata | null> {
	const statuses = await listModelProviderAuthStatuses(options);
	if (statuses.ok === false) return null;
	const status = statuses.value.find((entry) => entry.provider === provider);
	if (!status) return null;
	return {
		required: true,
		authKind: status.authKind,
		signInAvailable: status.signInAvailable,
		authenticated: status.authenticated,
		usable: status.authenticated,
		authSource: status.authSource,
	};
}

export async function shouldExposeProviderModelsInAvailableList(
	provider: string,
	options: {
		createdByUser: string | null;
		env?: NodeJS.ProcessEnv;
		modelRegistry?: ModelRegistry;
	},
): Promise<boolean> {
	const definition = resolveProviderDefinition(provider);
	if (definition && !definition.exposeInAstroUi) return false;
	if (!definition) return false;
	const statuses = await listModelProviderAuthStatuses(options);
	if (statuses.ok === false) return false;
	const status = statuses.value.find((entry) => entry.provider === provider);
	return status?.authenticated === true;
}

export async function isProviderUsableForExecution(
	provider: string,
	options: {
		createdByUser: string;
		env?: NodeJS.ProcessEnv;
	},
): Promise<ProviderCredentialOperationResult<boolean>> {
	const definition = resolveProviderDefinition(provider);
	if (!definition) return { ok: true, value: true };
	if (!shouldExposeAuthBackedProviderInAstroUi(provider)) return { ok: true, value: false };
	const statuses = await listModelProviderAuthStatuses({
		createdByUser: options.createdByUser,
		env: options.env,
	});
	if (statuses.ok === false) return statuses;
	const status = statuses.value.find((entry) => entry.provider === provider);
	return { ok: true, value: status?.authenticated === true };
}

export async function getModelProviderAuthResponse(options: {
	createdByUser: string | null;
	env?: NodeJS.ProcessEnv;
}): Promise<ModelProviderAuthResponse> {
	const statuses = await listModelProviderAuthStatuses({
		createdByUser: options.createdByUser,
		env: options.env,
	});
	if (statuses.ok === false) {
		const fallbackStatuses = await listModelProviderAuthStatuses({
			createdByUser: null,
			env: options.env,
		});
		return {
			version: 1,
			providers: fallbackStatuses.ok ? fallbackStatuses.value : [],
			backendCredentialStatus: {
				ok: false,
				error: statuses.message,
			},
		};
	}
	return {
		version: 1,
		providers: statuses.value,
		backendCredentialStatus: {
			ok: true,
		},
	};
}

export async function signOffModelProvider(
	provider: string,
	options: {
		createdByUser: string;
		env?: NodeJS.ProcessEnv;
	},
):
	Promise<
		| { ok: true; statusCode: 200; provider: string; authenticated: false; updatedAt: string }
		| { ok: false; statusCode: 404 | 503; error: string; message: string }
	> {
	const definition = resolveProviderDefinition(provider);
	if (!definition) {
		return {
			ok: false,
			statusCode: 404,
			error: "provider_not_supported",
			message: `Provider "${provider}" is not supported by model-provider auth controls.`,
		};
	}

	cancelActiveProviderSignIn(provider, options.env ?? process.env, {
		reason: "Provider signed off during active signin.",
		markAttemptCancelled: true,
	});
	const client = new ModelProviderCredentialClient({ env: options.env });
	const revoke = await client.revoke({
		createdByUser: options.createdByUser,
		provider,
		reason: "user_signoff",
	});
	if (revoke.ok === false) {
		return {
			ok: false,
			statusCode: revoke.status === 404 ? 404 : 503,
			error: "provider_credential_revoke_failed",
			message: revoke.error,
		};
	}

	return {
		ok: true,
		statusCode: 200,
		provider,
		authenticated: false,
		updatedAt: new Date().toISOString(),
	};
}
