import type { BackendAdapter } from "../types.js";
import { prepareMainsequenceBootstrap } from "./bootstrap.js";
import {
	buildMainsequenceStoredAuthEnv,
	bootstrapMainsequenceCliAuth,
	startMainsequenceCredentialExchangeLoop,
} from "./runtime-auth.js";
import {
	collectAvailableModels,
	collectModelCatalog,
	DEFAULT_OPENAI_MODEL,
	DEFAULT_OPENAI_PROVIDER,
} from "../../interface/stream/available-models.js";
import {
	buildAgentUniqueId,
	fetchBackendAgentSession,
	fetchBackendAgentSessionAgentCard,
	resolveMainsequenceUserId,
	shouldRegisterAgents,
} from "../../interface/stream/mainsequence-agent-registration.js";
import { resolveProviderDefinition } from "../../interface/stream/model-provider-definitions.js";
import {
	listModelProviderAuthStatuses,
	signOffModelProvider,
} from "../../interface/stream/model-provider-auth.js";
import {
	ModelProviderCredentialClient,
} from "../../interface/stream/model-provider-credentials-client.js";
import {
	cleanupScopedPiAgentDir,
	fetchBackendProviderCredentialStatus,
	flushScopedProviderCredential,
	hydrateScopedProviderCredentials,
	invalidateScopedProviderCredentialCache,
	isScopedProviderCredentialCacheEnabled,
} from "../../interface/stream/model-provider-scoped-auth.js";
import {
	cancelModelProviderSignInAttempt,
	getModelProviderSignInAttempt,
	startModelProviderSignIn,
	submitModelProviderSignInManualInput,
} from "../../interface/stream/model-provider-signin.js";
import { materializeSessionCapabilities } from "../../interface/stream/session-capabilities.js";
import { SessionCheckpointClient } from "../../interface/stream/session-checkpoint-client.js";

export const mainsequenceBackendAdapter: BackendAdapter = {
	name: "mainsequence",
	bootstrap: {
		prepareRuntime: prepareMainsequenceBootstrap,
	},
	auth: {
		prepareRuntime: bootstrapMainsequenceCliAuth,
		buildSubprocessEnv: buildMainsequenceStoredAuthEnv,
		startCredentialRefreshLoop: startMainsequenceCredentialExchangeLoop,
	},
	identity: {
		shouldRegisterAgents,
		resolveUserId: resolveMainsequenceUserId,
		buildAgentUniqueId,
	},
	sessions: {
		fetchByUid: fetchBackendAgentSession,
		fetchAgentCard: fetchBackendAgentSessionAgentCard,
	},
	checkpoints: {
		createClient: (options) => new SessionCheckpointClient(options),
	},
	providerCredentials: {
		isScopedCacheEnabled: isScopedProviderCredentialCacheEnabled,
		cleanupScopedDir: cleanupScopedPiAgentDir,
		invalidateScopedCache: invalidateScopedProviderCredentialCache,
		hydrateScoped: hydrateScopedProviderCredentials,
		flushScoped: flushScopedProviderCredential,
		status: fetchBackendProviderCredentialStatus,
		hydrate: (input) =>
			new ModelProviderCredentialClient({
				env: input.env,
				log: input.log,
			}).hydrate(input),
		listAuthStatuses: listModelProviderAuthStatuses,
		startSignIn: startModelProviderSignIn,
		getSignInAttempt: getModelProviderSignInAttempt,
		submitSignInManualInput: submitModelProviderSignInManualInput,
		cancelSignInAttempt: cancelModelProviderSignInAttempt,
		signOff: signOffModelProvider,
	},
	capabilities: {
		materializeSession: materializeSessionCapabilities,
	},
	modelCatalog: {
		defaultOpenAiProvider: DEFAULT_OPENAI_PROVIDER,
		defaultOpenAiModel: DEFAULT_OPENAI_MODEL,
		collectAvailableModels,
		collectModelCatalog,
		resolveProviderDefinition,
	},
};
