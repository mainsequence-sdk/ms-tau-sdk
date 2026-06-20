import type {
	CheckpointBundle,
	CheckpointLatestResponse,
	CheckpointLeaseResponse,
	SessionCheckpointClient,
	SessionCheckpointClientResult,
} from "../interface/stream/session-checkpoint-client.js";
import type {
	AvailableModelContext,
	AvailableModelsResponse,
	ModelCatalogResponse,
	RunConfigReasoningEffort,
} from "../interface/stream/available-models.js";
import type { AuthBackedProviderDefinition } from "../interface/stream/model-provider-definitions.js";
import type {
	HydratedScopedProviderCredentials,
	ProviderCredentialOperationResult,
} from "../interface/stream/model-provider-scoped-auth.js";
import type {
	ProviderCredentialFlushResponse,
	ProviderCredentialHydrateResponse,
	ProviderCredentialClientResult,
	ProviderCredentialStatusResponse,
} from "../interface/stream/model-provider-credentials-client.js";
import type { ModelProviderAuthStatus } from "../interface/stream/model-provider-auth.js";
import type {
	SessionCapabilityMaterialization,
	SessionCapabilityMaterializationResult,
} from "../interface/stream/session-capabilities.js";
import type { SessionConfigOverrides } from "../interface/stream/session-config.js";

export type {
	AvailableModelsResponse,
	CheckpointBundle,
	CheckpointLatestResponse,
	CheckpointLeaseResponse,
	RunConfigReasoningEffort,
	SessionCapabilityMaterialization,
	SessionCheckpointClientResult,
};

export type BackendAdapterName = string;

export type BackendAuthHeaders = Record<string, string>;

export type BackendLogFn = (message: string) => void;

export type BackendLoopHandle = {
	stop(): void;
} | null;

export type BackendUserRef = {
	uid: string;
};

export type BackendAgentRef = {
	uid: string | null;
	type: string | null;
	uniqueId: string | null;
};

export type BackendSessionRef = {
	uid: string;
	threadId: string | null;
	agent: BackendAgentRef;
};

export type BackendModelBinding = {
	provider: string | null;
	model: string | null;
	reasoningEffort?: string | null;
	metadata?: Record<string, unknown>;
};

export type BackendProjectAttachment = {
	projectId: string | null;
	cwd: string | null;
	projectImageRef: string | null;
};

export type AdapterFailure = {
	code: string;
	message: string;
	status?: number | null;
	detail?: unknown;
};

export type AdapterResult<T> =
	| { ok: true; value: T }
	| { ok: false; error: AdapterFailure };

export type BackendSessionFetchResult = {
	ok: boolean;
	status: number | null;
	body: unknown;
	error: string | null;
	agentSessionUid: string | null;
	notFound: boolean;
	endpoint: string | null;
};

export type BackendSessionAgentCardFetchResult = {
	ok: boolean;
	status: number | null;
	body: unknown;
	error: string | null;
	agentSessionUid: string | null;
	agentUid: string | null;
	agentCard: Record<string, unknown> | null;
	notFound: boolean;
	endpoint: string | null;
};

export type BackendAuthCapability = {
	prepareRuntime(options?: {
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): Promise<void>;
	buildSubprocessEnv(env?: NodeJS.ProcessEnv): NodeJS.ProcessEnv;
	startCredentialRefreshLoop?(options?: {
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): BackendLoopHandle;
};

export type BackendIdentityCapability = {
	shouldRegisterAgents(env?: NodeJS.ProcessEnv): boolean;
	resolveUserId(options: {
		userId?: unknown;
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): string | null;
	buildAgentUniqueId(options: {
		agentType: string;
		userId: string;
		projectId?: string | number | null;
	}): string;
};

export type BackendSessionsCapability = {
	fetchByUid(options: {
		agentSessionUid: string;
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): Promise<BackendSessionFetchResult>;
	fetchAgentCard(options: {
		agentSessionUid: string;
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): Promise<BackendSessionAgentCardFetchResult>;
};

export type BackendCheckpointClient = Pick<
	SessionCheckpointClient,
	| "acquireLease"
	| "renewLease"
	| "releaseLease"
	| "restore"
	| "latest"
	| "flush"
	| "requestRuntimeCancel"
	| "updateInsights"
>;

export type BackendCheckpointsCapability = {
	createClient(options?: {
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): BackendCheckpointClient;
};

export type BackendProviderCredentialsCapability = {
	isScopedCacheEnabled(env?: NodeJS.ProcessEnv): boolean;
	cleanupScopedDir(scopedPiAgentDir: string | null | undefined): void;
	invalidateScopedCache(input: {
		sessionKey: string;
		provider?: string | null;
		env?: NodeJS.ProcessEnv;
	}): void;
	hydrateScoped(input: {
		createdByUser: string;
		agentSessionUid: string | null;
		sessionKey: string;
		provider: string;
		holderId: string;
		sessionConfigOverrides: SessionConfigOverrides | null;
		sessionSkillPaths?: string[];
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): Promise<ProviderCredentialOperationResult<HydratedScopedProviderCredentials>>;
	flushScoped(input: {
		scopedPiAgentDir: string;
		createdByUser: string;
		agentSessionUid: string | null;
		provider: string;
		reason: string;
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): Promise<ProviderCredentialOperationResult<ProviderCredentialFlushResponse & { noop?: boolean }>>;
	status(input: {
		createdByUser: string;
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): Promise<ProviderCredentialOperationResult<ProviderCredentialStatusResponse>>;
	hydrate(input: {
		createdByUser: string;
		agentSessionUid: string | null;
		providers: string[];
		holderId: string;
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): Promise<ProviderCredentialClientResult<ProviderCredentialHydrateResponse>>;
	listAuthStatuses(input: {
		createdByUser: string | null;
		env?: NodeJS.ProcessEnv;
		modelRegistry?: unknown;
	}): Promise<ProviderCredentialOperationResult<ModelProviderAuthStatus[]>>;
	startSignIn: typeof import("../interface/stream/model-provider-signin.js").startModelProviderSignIn;
	getSignInAttempt: typeof import("../interface/stream/model-provider-signin.js").getModelProviderSignInAttempt;
	submitSignInManualInput: typeof import("../interface/stream/model-provider-signin.js").submitModelProviderSignInManualInput;
	cancelSignInAttempt: typeof import("../interface/stream/model-provider-signin.js").cancelModelProviderSignInAttempt;
	signOff: typeof import("../interface/stream/model-provider-auth.js").signOffModelProvider;
};

export type BackendCapabilitiesCapability = {
	materializeSession(input: {
		agentSessionUid: string;
		sessionAssetsRoot: string;
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
	}): Promise<SessionCapabilityMaterializationResult>;
};

export type BackendModelCatalogCapability = {
	defaultOpenAiProvider: string;
	defaultOpenAiModel: string;
	collectAvailableModels(context?: AvailableModelContext): Promise<AvailableModelsResponse>;
	collectModelCatalog(context?: AvailableModelContext): Promise<ModelCatalogResponse>;
	resolveProviderDefinition(provider: string): AuthBackedProviderDefinition | null;
};

export type BackendBootstrapResult = Record<string, unknown> | void;

export type BackendBootstrapCapability = {
	prepareRuntime(options: {
		env?: NodeJS.ProcessEnv;
		log?: BackendLogFn;
		repoRoot?: string;
		piAgentDir?: string;
		homeDir?: string;
		containerDataRoot?: string | null;
	}): Promise<BackendBootstrapResult> | BackendBootstrapResult;
};

export type BackendAdapter = {
	name: BackendAdapterName;
	auth?: BackendAuthCapability;
	identity?: BackendIdentityCapability;
	sessions?: BackendSessionsCapability;
	checkpoints?: BackendCheckpointsCapability;
	providerCredentials?: BackendProviderCredentialsCapability;
	capabilities?: BackendCapabilitiesCapability;
	modelCatalog?: BackendModelCatalogCapability;
	projects?: unknown;
	a2a?: unknown;
	bootstrap?: BackendBootstrapCapability;
};

export function requireBackendCapability<T>(
	adapter: BackendAdapter,
	name: keyof BackendAdapter,
	capability: T | undefined,
): T {
	if (capability) return capability;
	throw new Error(`Backend adapter "${adapter.name}" does not provide required capability "${String(name)}".`);
}
