import type { AuthStorage, ModelRegistry } from "../../node_modules/@mariozechner/pi-coding-agent/dist/index.js";
import {
	listAuthBackedProviderDefinitions,
	resolveProviderDefinition,
	shouldExposeAuthBackedProviderInAstroUi,
	type AuthBackedProviderDefinition,
} from "./model-provider-definitions.js";
import {
	createScopedPiRuntime,
	getSignedOffRecord,
	readRuntimeAuthState,
	setProviderSignedOff,
	withScopedPiAgentDir,
} from "./model-provider-runtime.js";
import { cancelActiveProviderSignIn } from "./model-provider-signin.js";

export type ModelProviderAuthSource = "runtime_store" | null;

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
};

function buildProviderAuthStatus(options: {
	provider: string;
	env: NodeJS.ProcessEnv;
	authStorage: AuthStorage;
	knownModelCount: number;
}): ModelProviderAuthStatus | null {
	const definition = resolveProviderDefinition(options.provider);
	if (!definition || !definition.exposeInAstroUi) return null;

	const runtimeCredentialPresent = options.authStorage.has(options.provider);
	const signedOffRecord = getSignedOffRecord(options.provider, options.env);
	const authenticated = !signedOffRecord && runtimeCredentialPresent;
	const signInAvailable =
		!authenticated &&
		(definition.signInMode === "oauth" ||
			(definition.signInMode === "api_key_sync" && definition.isConfiguredFromEnv(options.env)));
	const authSource: ModelProviderAuthSource = authenticated ? "runtime_store" : null;

	return {
		provider: options.provider,
		authKind: definition.authKind,
		signInAvailable,
		authenticated,
		authSource,
		knownModelCount: options.knownModelCount,
		usableModelCount: authenticated ? options.knownModelCount : 0,
		lastValidatedAt: new Date().toISOString(),
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
	runtimeAuthProviders: Set<string>;
	signedOffProviders: Set<string>;
	env: NodeJS.ProcessEnv;
}): boolean {
	if ((options.knownModelCounts.get(options.definition.provider) ?? 0) > 0) return true;
	if (options.runtimeAuthProviders.has(options.definition.provider)) return true;
	if (options.signedOffProviders.has(options.definition.provider)) return true;
	if (options.definition.signInMode === "oauth") return true;
	if (options.definition.signInMode === "api_key_sync" && options.definition.isConfiguredFromEnv(options.env)) {
		return true;
	}
	return false;
}

export function listModelProviderAuthStatuses(
	env: NodeJS.ProcessEnv = process.env,
	options?: { authStorage?: AuthStorage; modelRegistry?: ModelRegistry },
): ModelProviderAuthStatus[] {
	const runtime = options?.authStorage && options?.modelRegistry ? options : createScopedPiRuntime(env);
	const knownModelCounts = collectKnownModelCounts(runtime.modelRegistry);
	const runtimeAuthProviders = new Set(runtime.authStorage.list());
	const signedOffProviders = new Set(Object.keys(readRuntimeAuthState(env).providers ?? {}));
	const candidateProviders = new Set<string>();

	for (const provider of knownModelCounts.keys()) candidateProviders.add(provider);
	for (const provider of runtimeAuthProviders) candidateProviders.add(provider);
	for (const provider of signedOffProviders) candidateProviders.add(provider);
	for (const definition of listAuthBackedProviderDefinitions()) {
		if (!definition.exposeInAstroUi) continue;
		if (
			shouldIncludeProviderCandidate({
				definition,
				knownModelCounts,
				runtimeAuthProviders,
				signedOffProviders,
				env,
			})
		) {
			candidateProviders.add(definition.provider);
		}
	}

	return [...candidateProviders]
		.map((provider) =>
			buildProviderAuthStatus({
				provider,
				env,
				authStorage: runtime.authStorage,
				knownModelCount: knownModelCounts.get(provider) ?? 0,
			}),
		)
		.filter((entry): entry is ModelProviderAuthStatus => Boolean(entry))
		.sort((left, right) => left.provider.localeCompare(right.provider));
}

export function getModelProviderAuthMetadata(
	provider: string,
	env: NodeJS.ProcessEnv = process.env,
	options?: { authStorage?: AuthStorage; modelRegistry?: ModelRegistry },
): ModelProviderAuthMetadata | null {
	const status = listModelProviderAuthStatuses(env, options).find((entry) => entry.provider === provider);
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

export function shouldExposeProviderModelsInAvailableList(
	provider: string,
	env: NodeJS.ProcessEnv = process.env,
	options?: { authStorage?: AuthStorage; modelRegistry?: ModelRegistry },
): boolean {
	const definition = resolveProviderDefinition(provider);
	if (definition && !definition.exposeInAstroUi) return false;
	if (!definition) {
		return false;
	}
	const status = listModelProviderAuthStatuses(env, options).find((entry) => entry.provider === provider);
	return status?.authenticated === true;
}

export function isProviderUsableForExecution(provider: string, env: NodeJS.ProcessEnv = process.env): boolean {
	const definition = resolveProviderDefinition(provider);
	if (!definition) return true;
	if (!shouldExposeAuthBackedProviderInAstroUi(provider)) return false;
	const status = listModelProviderAuthStatuses(env).find((entry) => entry.provider === provider);
	return status?.authenticated === true;
}

export function getModelProviderAuthResponse(env: NodeJS.ProcessEnv = process.env): {
	version: 1;
	providers: ModelProviderAuthStatus[];
} {
	return {
		version: 1,
		providers: listModelProviderAuthStatuses(env),
	};
}

export function signOffModelProvider(
	provider: string,
	env: NodeJS.ProcessEnv = process.env,
):
	| { ok: true; statusCode: 200; provider: string; authenticated: false; updatedAt: string }
	| { ok: false; statusCode: 404; error: string; message: string } {
	const definition = resolveProviderDefinition(provider);
	if (!definition) {
		return {
			ok: false,
			statusCode: 404,
			error: "provider_not_supported",
			message: `Provider "${provider}" is not supported by model-provider auth controls.`,
		};
	}

	cancelActiveProviderSignIn(provider, env, {
		reason: "Provider signed off during active signin.",
		markAttemptCancelled: true,
	});
	withScopedPiAgentDir(env, () => {
		const runtime = createScopedPiRuntime(env);
		runtime.authStorage.logout(provider);
	});
	setProviderSignedOff(provider, true, env);

	return {
		ok: true,
		statusCode: 200,
		provider,
		authenticated: false,
		updatedAt: new Date().toISOString(),
	};
}
