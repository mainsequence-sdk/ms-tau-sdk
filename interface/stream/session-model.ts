import {
	collectAvailableModels,
	resolveOllamaHost,
	type AvailableModel,
	type AvailableModelContext,
	type AvailableModelReasoningCapability,
	type RunConfigReasoningEffort,
} from "./available-models.js";

export type PiThinkingLevel = Exclude<RunConfigReasoningEffort, "on">;

export type SessionModelBinding = {
	source: string;
	provider: string;
	label: string;
	model: string;
	runConfig: {
		reasoning_effort: RunConfigReasoningEffort | null;
	};
	capabilities: {
		features?: string[];
		reasoning_effort: AvailableModelReasoningCapability;
	};
	metadata?: Record<string, unknown>;
	updatedAt: string;
	piThinkingLevel: PiThinkingLevel | null;
};

type SessionModelRequestPayload = {
	source?: unknown;
	provider?: unknown;
	model?: unknown;
	runConfig?: {
		reasoning_effort?: unknown;
	} | null;
};

type SessionModelRegistration = {
	provider: string;
	config: Record<string, unknown>;
};

type SessionModelResolutionError = {
	ok: false;
	statusCode: 400 | 404;
	error: string;
	message: string;
};

type SessionModelResolutionSuccess = {
	ok: true;
	binding: SessionModelBinding;
	selectedModel: AvailableModel;
};

export type ResolveSessionModelBindingResult =
	| SessionModelResolutionError
	| SessionModelResolutionSuccess;

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeNonEmptyString(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function normalizeReasoningEffort(value: unknown): RunConfigReasoningEffort | null {
	switch (value) {
		case "off":
		case "on":
		case "minimal":
		case "low":
		case "medium":
		case "high":
		case "xhigh":
			return value;
		default:
			return null;
	}
}

function extractObjectPropertyRecord(
	record: Record<string, unknown> | null | undefined,
	...keys: string[]
): Record<string, unknown> | null {
	if (!record) return null;
	for (const key of keys) {
		const value = record[key];
		if (isPlainObject(value)) return value;
	}
	return null;
}

function extractStringProperty(record: Record<string, unknown> | null | undefined, ...keys: string[]): string | null {
	if (!record) return null;
	for (const key of keys) {
		const normalized = normalizeNonEmptyString(record[key]);
		if (normalized) return normalized;
	}
	return null;
}

function normalizeFeatures(value: unknown): string[] | undefined {
	if (!Array.isArray(value)) return undefined;
	const features = value
		.map((entry) => normalizeNonEmptyString(entry))
		.filter((entry, index, array): entry is string => Boolean(entry) && array.indexOf(entry) === index);
	return features.length ? features : undefined;
}

function normalizeReasoningCapability(value: unknown): AvailableModelReasoningCapability | null {
	if (!isPlainObject(value)) return null;
	const supported = value.supported === true;
	const mode =
		value.mode === "unsupported" || value.mode === "toggle" || value.mode === "levels" ? value.mode : null;
	const defaultValue = normalizeReasoningEffort(value.default);
	if (!mode || !defaultValue) return null;
	const values = Array.isArray(value.values)
		? value.values
				.map((entry) => normalizeReasoningEffort(entry))
				.filter((entry, index, array): entry is RunConfigReasoningEffort =>
					Boolean(entry) && array.indexOf(entry) === index,
				)
		: [];
	return {
		supported,
		mode,
		values,
		default: defaultValue,
	};
}

function resolveReasoningConfig(
	requestedValue: RunConfigReasoningEffort | null,
	selectedModel: AvailableModel,
): {
	ok: true;
		runConfigValue: RunConfigReasoningEffort;
		piThinkingLevel: PiThinkingLevel;
	} | SessionModelResolutionError {
	const capability = selectedModel.capabilities.runConfig.reasoning_effort;
	const defaultValue = selectedModel.defaults.runConfig.reasoning_effort;
	const candidate = requestedValue ?? defaultValue;

	switch (capability.mode) {
		case "unsupported":
			if (candidate !== "off") {
				return {
					ok: false,
					statusCode: 400,
					error: "invalid_model_run_config",
					message: `Model ${selectedModel.provider}/${selectedModel.model} does not support reasoning controls.`,
				};
			}
			return {
				ok: true,
				runConfigValue: "off",
				piThinkingLevel: "off",
			};
		case "toggle":
			if (candidate !== "off" && candidate !== "on") {
				return {
					ok: false,
					statusCode: 400,
					error: "invalid_model_run_config",
					message: `Model ${selectedModel.provider}/${selectedModel.model} only supports reasoning values "off" and "on".`,
				};
			}
			return {
				ok: true,
				runConfigValue: candidate,
				piThinkingLevel: candidate === "on" ? "medium" : "off",
			};
		case "levels": {
			if (candidate === "on") {
				return {
					ok: false,
					statusCode: 400,
					error: "invalid_model_run_config",
					message: `Model ${selectedModel.provider}/${selectedModel.model} expects a named reasoning level, not "on".`,
				};
			}
			const supportedValues = new Set<RunConfigReasoningEffort>(["off", ...capability.values]);
			if (!supportedValues.has(candidate)) {
				return {
					ok: false,
					statusCode: 400,
					error: "invalid_model_run_config",
					message: `Model ${selectedModel.provider}/${selectedModel.model} does not support reasoning level "${candidate}".`,
				};
			}
			return {
				ok: true,
				runConfigValue: candidate,
				piThinkingLevel: candidate,
			};
		}
	}
}

function buildSessionModelBinding(
	selectedModel: AvailableModel,
	runConfigValue: RunConfigReasoningEffort,
	piThinkingLevel: PiThinkingLevel,
): SessionModelBinding {
	return {
		source: selectedModel.source,
		provider: selectedModel.provider,
		label: selectedModel.label,
		model: selectedModel.model,
		runConfig: {
			reasoning_effort: runConfigValue,
		},
		capabilities: {
			...(selectedModel.capabilities.features ? { features: [...selectedModel.capabilities.features] } : {}),
			reasoning_effort: selectedModel.capabilities.runConfig.reasoning_effort,
		},
		...(selectedModel.metadata ? { metadata: selectedModel.metadata } : {}),
		updatedAt: new Date().toISOString(),
		piThinkingLevel,
	};
}

export async function resolveSessionModelBinding(
	payload: SessionModelRequestPayload,
	context: AvailableModelContext = {},
): Promise<ResolveSessionModelBindingResult> {
	const requestedSource = normalizeNonEmptyString(payload.source);
	const requestedProvider = normalizeNonEmptyString(payload.provider);
	const requestedModel = normalizeNonEmptyString(payload.model);
	if (!requestedSource) {
		return {
			ok: false,
			statusCode: 400,
			error: "missing_model_source",
			message: "Session model selection requires a source.",
		};
	}
	if (!requestedModel) {
		return {
			ok: false,
			statusCode: 400,
			error: "missing_model_id",
			message: "Session model selection requires a model id.",
		};
	}

	const availableModels = await collectAvailableModels(context);
	const selectedModel = availableModels.providers
		.flatMap((group) => group.models)
		.find(
		(candidate) =>
			candidate.source === requestedSource &&
			candidate.model === requestedModel &&
			(!requestedProvider || candidate.provider === requestedProvider),
	);
	if (!selectedModel) {
		return {
			ok: false,
			statusCode: 404,
			error: "model_not_found",
			message: `No available model matched source "${requestedSource}" and model "${requestedModel}".`,
		};
	}

	const requestedRunConfig = isPlainObject(payload.runConfig) ? payload.runConfig : {};
	const requestedReasoningEffort =
		requestedRunConfig.reasoning_effort === undefined
			? null
			: normalizeReasoningEffort(requestedRunConfig.reasoning_effort);
	if (requestedRunConfig.reasoning_effort !== undefined && !requestedReasoningEffort) {
		return {
			ok: false,
			statusCode: 400,
			error: "invalid_model_run_config",
			message: "runConfig.reasoning_effort must be one of off, on, minimal, low, medium, high, or xhigh.",
		};
	}

	const reasoningResolution = resolveReasoningConfig(requestedReasoningEffort, selectedModel);
	if (reasoningResolution.ok === false) return reasoningResolution;

	return {
		ok: true,
		selectedModel,
		binding: buildSessionModelBinding(
			selectedModel,
			reasoningResolution.runConfigValue,
			reasoningResolution.piThinkingLevel,
		),
	};
}

export function normalizeSessionModelBinding(value: unknown): SessionModelBinding | null {
	if (!isPlainObject(value)) return null;
	const source = normalizeNonEmptyString(value.source);
	const provider = normalizeNonEmptyString(value.provider);
	const label = normalizeNonEmptyString(value.label);
	const model = normalizeNonEmptyString(value.model);
	const updatedAt = normalizeNonEmptyString(value.updatedAt);
	if (!source || !provider || !label || !model || !updatedAt) {
		return null;
	}

	const runConfig = isPlainObject(value.runConfig) ? value.runConfig : null;
	const reasoningEffort = normalizeReasoningEffort(runConfig?.reasoning_effort) ?? null;
	const rawPiThinkingLevel = normalizeReasoningEffort(value.piThinkingLevel);
	const piThinkingLevel =
		rawPiThinkingLevel && rawPiThinkingLevel !== "on"
			? rawPiThinkingLevel
			: derivePiThinkingLevel(reasoningEffort, null);

	const capabilities = isPlainObject(value.capabilities) ? value.capabilities : null;
	const reasoningCapability =
		normalizeReasoningCapability(capabilities?.reasoning_effort) ??
		(reasoningEffort ? buildGenericReasoningCapability(reasoningEffort) : buildUnknownReasoningCapability());

	const metadata = isPlainObject(value.metadata) ? value.metadata : undefined;

	return {
		source,
		provider,
		label,
		model,
		runConfig: {
			reasoning_effort: reasoningEffort,
		},
		capabilities: {
			...(normalizeFeatures(capabilities?.features) ? { features: normalizeFeatures(capabilities?.features) } : {}),
			reasoning_effort: reasoningCapability,
		},
		...(metadata ? { metadata } : {}),
		updatedAt,
		piThinkingLevel,
	};
}

function derivePiThinkingLevel(
	value: RunConfigReasoningEffort | null,
	fallback: PiThinkingLevel | null,
): PiThinkingLevel | null {
	if (value === "off") return "off";
	if (value === "minimal") return "minimal";
	if (value === "low") return "low";
	if (value === "medium" || value === "on") return "medium";
	if (value === "high") return "high";
	if (value === "xhigh") return "xhigh";
	return fallback ?? null;
}

function buildGenericReasoningCapability(
	value: RunConfigReasoningEffort,
): AvailableModelReasoningCapability {
	if (value === "on" || value === "off") {
		return {
			supported: true,
			mode: "toggle",
			values: ["on"],
			default: value === "on" ? "on" : "off",
		};
	}
	return {
		supported: true,
		mode: "levels",
		values: [value],
		default: value,
	};
}

function buildUnknownReasoningCapability(): AvailableModelReasoningCapability {
	return {
		supported: false,
		mode: "unsupported",
		values: ["off"],
		default: "off",
	};
}

export function rebindSessionModelBindingFromIdentity(options: {
	existingBinding?: SessionModelBinding | null;
	provider: string | null;
	model: string | null;
	reasoningEffort?: RunConfigReasoningEffort | null;
	source?: string | null;
	updatedAt?: string;
}): SessionModelBinding | null {
	const provider = normalizeNonEmptyString(options.provider);
	const model = normalizeNonEmptyString(options.model);
	const existingBinding = options.existingBinding ?? null;
	if (!provider || !model) return existingBinding;

	const effectiveReasoning = Object.prototype.hasOwnProperty.call(options, "reasoningEffort")
		? normalizeReasoningEffort(options.reasoningEffort)
		: existingBinding?.runConfig.reasoning_effort ?? null;
	const isSameIdentity =
		existingBinding?.provider === provider && existingBinding?.model === model;
	const updatedAt = options.updatedAt ?? new Date().toISOString();

	return {
		source:
			normalizeNonEmptyString(options.source) ??
			existingBinding?.source ??
			"session",
		provider,
		label: isSameIdentity ? existingBinding?.label ?? model : model,
		model,
		runConfig: {
			reasoning_effort: effectiveReasoning,
		},
		capabilities: {
			...(existingBinding?.capabilities.features
				? { features: [...existingBinding.capabilities.features] }
				: {}),
			reasoning_effort:
				existingBinding?.capabilities.reasoning_effort ??
				(effectiveReasoning
					? buildGenericReasoningCapability(effectiveReasoning)
					: buildUnknownReasoningCapability()),
		},
		...(existingBinding?.metadata ? { metadata: existingBinding.metadata } : {}),
		updatedAt,
		piThinkingLevel: derivePiThinkingLevel(
			effectiveReasoning,
			existingBinding?.piThinkingLevel ?? null,
		),
	};
}

export function deriveSessionModelBindingFromSessionPayload(options: {
	sessionPayload: Record<string, unknown> | null;
	existingBinding?: SessionModelBinding | null;
	now?: string;
}): SessionModelBinding | null {
	const sessionPayload = options.sessionPayload;
	const existingBinding = options.existingBinding ?? null;
	if (!sessionPayload) return existingBinding;

	const sessionMetadata = extractObjectPropertyRecord(sessionPayload, "session_metadata", "sessionMetadata");
	const explicitBinding = normalizeSessionModelBinding(
		sessionPayload.session_model_binding ??
			sessionPayload.sessionModelBinding ??
			sessionMetadata?.session_model_binding ??
			sessionMetadata?.sessionModelBinding,
	);
	const llmProvider = extractStringProperty(sessionPayload, "llm_provider", "llmProvider");
	const llmModel = extractStringProperty(sessionPayload, "llm_model", "llmModel");
	const runtimeConfigSnapshot =
		extractObjectPropertyRecord(sessionPayload, "runtime_config_snapshot", "runtimeConfigSnapshot") ??
		extractObjectPropertyRecord(sessionMetadata, "runtime_config_snapshot", "runtimeConfigSnapshot");
	const rawThinking =
		extractStringProperty(sessionPayload, "llm_thinking", "llmThinking") ??
		extractStringProperty(sessionMetadata, "llm_thinking", "llmThinking") ??
		extractStringProperty(
			runtimeConfigSnapshot,
			"reasoning_effort",
			"reasoningEffort",
			"thinking",
		);
	const runtimeReasoning = normalizeReasoningEffort(rawThinking) ?? null;
	const updatedAt =
		extractStringProperty(sessionPayload, "updated_at", "updatedAt") ??
		extractStringProperty(sessionPayload, "started_at", "startedAt") ??
		explicitBinding?.updatedAt ??
		existingBinding?.updatedAt ??
		options.now ??
		new Date().toISOString();

	if (!llmProvider || !llmModel) {
		if (!explicitBinding) return existingBinding;
		const explicitReasoning = runtimeReasoning ?? explicitBinding.runConfig.reasoning_effort;
		const refreshedBinding: SessionModelBinding = {
			...explicitBinding,
			runConfig: {
				reasoning_effort: explicitReasoning,
			},
			updatedAt,
			piThinkingLevel: derivePiThinkingLevel(explicitReasoning, explicitBinding.piThinkingLevel),
		};
		if (!rawThinking) return refreshedBinding;
		return {
			...refreshedBinding,
			metadata: {
				...(refreshedBinding.metadata ?? {}),
				backend_llm_thinking: rawThinking,
			},
		};
	}

	const baseBinding =
		explicitBinding && explicitBinding.provider === llmProvider && explicitBinding.model === llmModel
			? explicitBinding
			: existingBinding && existingBinding.provider === llmProvider && existingBinding.model === llmModel
				? existingBinding
				: null;

	const reboundBinding = rebindSessionModelBindingFromIdentity({
		existingBinding:
			baseBinding ??
			explicitBinding ??
			null,
		provider: llmProvider,
		model: llmModel,
		reasoningEffort: runtimeReasoning,
		source: baseBinding?.source ?? explicitBinding?.source ?? "session",
		updatedAt,
	});
	if (!reboundBinding || !rawThinking) return reboundBinding;
	return {
		...reboundBinding,
		metadata: {
			...(reboundBinding.metadata ?? {}),
			backend_llm_thinking: rawThinking,
		},
	};
}

export function buildPiModelArgument(binding: SessionModelBinding | null): string | null {
	if (!binding) return null;
	return binding.piThinkingLevel
		? `${binding.provider}/${binding.model}:${binding.piThinkingLevel}`
		: `${binding.provider}/${binding.model}`;
}

function ensureProviderBaseUrl(value: string): string {
	const trimmed = value.trim().replace(/\/+$/, "");
	if (!trimmed) return trimmed;
	return trimmed.endsWith("/v1") ? trimmed : `${trimmed}/v1`;
}

function buildSessionModelRegistration(
	binding: SessionModelBinding,
	env: NodeJS.ProcessEnv = process.env,
): SessionModelRegistration | null {
	if (binding.provider !== "ollama") return null;

	const configuredHost =
		normalizeNonEmptyString(binding.metadata?.ollama_host) ?? resolveOllamaHost(env) ?? normalizeNonEmptyString(env.OLLAMA_HOST);
	if (!configuredHost) return null;

	return {
		provider: binding.provider,
		config: {
			baseUrl: ensureProviderBaseUrl(configuredHost),
			api: "openai-completions",
			apiKey: "ollama",
			models: [
				{
					id: binding.model,
					name: binding.label,
					reasoning: binding.capabilities.reasoning_effort.supported,
					input: binding.provider === "ollama" && binding.model.toLowerCase().includes("vl") ? ["text", "image"] : ["text"],
					cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
					contextWindow: 128000,
					maxTokens: 8192,
				},
			],
		},
	};
}

export function buildSessionModelEnv(
	binding: SessionModelBinding | null,
	env: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
	if (!binding) return {};
	const registration = buildSessionModelRegistration(binding, env);
	const overrides: NodeJS.ProcessEnv = {
		ASTRO_SESSION_MODEL_BINDING: JSON.stringify(binding),
	};

	if (registration) {
		overrides.ASTRO_SESSION_MODEL_PROVIDER_REGISTRATION = JSON.stringify(registration);
		const ollamaHost = normalizeNonEmptyString(binding.metadata?.ollama_host) ?? resolveOllamaHost(env);
		if (ollamaHost) {
			overrides.OLLAMA_HOST = ensureProviderBaseUrl(ollamaHost);
		}
	}

	return overrides;
}
