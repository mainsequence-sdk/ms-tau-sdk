import { AuthStorage, ModelRegistry } from "../../node_modules/@mariozechner/pi-coding-agent/dist/index.js";
import {
	getModelProviderAuthMetadata,
	shouldExposeProviderModelsInAvailableList,
	type ModelProviderAuthMetadata,
} from "./model-provider-auth.js";
import { shouldExposeAuthBackedProviderInAstroUi } from "./model-provider-definitions.js";
import { createScopedPiRuntime } from "./model-provider-runtime.js";

export const DEFAULT_OPENAI_PROVIDER = "openai";
export const DEFAULT_OPENAI_MODEL = "gpt-5.4";

export type RunConfigReasoningEffort =
	| "off"
	| "on"
	| "minimal"
	| "low"
	| "medium"
	| "high"
	| "xhigh";

export type AvailableModelReasoningMode = "unsupported" | "toggle" | "levels";

export type AvailableModelReasoningCapability = {
	supported: boolean;
	mode: AvailableModelReasoningMode;
	values: RunConfigReasoningEffort[];
	default: RunConfigReasoningEffort;
};

export type AvailableModel = {
	source: string;
	provider: string;
	label: string;
	model: string;
	available: true;
	defaults: {
		runConfig: {
			reasoning_effort: RunConfigReasoningEffort;
		};
	};
	capabilities: {
		features?: string[];
		runConfig: {
			reasoning_effort: AvailableModelReasoningCapability;
		};
	};
	auth?: ModelProviderAuthMetadata | { required: false; authenticated: true; usable: true };
	metadata?: Record<string, unknown>;
};

export type AvailableModelSourceStatus = {
	source: string;
	ok: boolean;
	count: number;
	details?: Record<string, unknown>;
	error?: string;
};

export type AvailableModelsResponse = {
	version: 1;
	providers: AvailableModelProviderGroup[];
	sources: AvailableModelSourceStatus[];
};

export type AvailableModelProviderGroup = {
	provider: string;
	models: AvailableModel[];
};

export type ModelCatalogResponse = {
	version: 1;
	models: AvailableModel[];
	sources: AvailableModelSourceStatus[];
};

export type AvailableModelContext = {
	env?: NodeJS.ProcessEnv;
	fetchFn?: typeof fetch;
	defaultOpenAiProvider?: string;
	defaultOpenAiModel?: string;
};

export type AvailableModelCollectionResult = {
	source: string;
	ok: boolean;
	models: AvailableModel[];
	details?: Record<string, unknown>;
	error?: string;
};

export interface AvailableModelCollector {
	source: string;
	collect(context: Required<AvailableModelContext>): Promise<AvailableModelCollectionResult>;
}

function buildReasoningSupport(options: {
	supported: boolean;
	mode: AvailableModelReasoningMode;
	values: RunConfigReasoningEffort[];
	defaultValue: RunConfigReasoningEffort;
}): AvailableModelReasoningCapability {
	return {
		supported: options.supported,
		mode: options.mode,
		values: options.values,
		default: options.defaultValue,
	};
}

function normalizeUrlRoot(value: string): string {
	const trimmed = value.trim().replace(/\/+$/, "");
	if (!trimmed) return trimmed;
	return trimmed.replace(/\/v1$/i, "");
}

function normalizeModelName(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function normalizeCapability(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const normalized = value.trim().toLowerCase();
	return normalized ? normalized : null;
}

function normalizeCapabilityList(value: unknown): string[] {
	if (!Array.isArray(value)) return [];
	return value
		.map((entry) => normalizeCapability(entry))
		.filter((entry, index, array): entry is string => Boolean(entry) && array.indexOf(entry) === index)
		.sort((left, right) => left.localeCompare(right));
}

function buildReasoningCapabilityFromFeatures(features: string[]): AvailableModelReasoningCapability {
	const featureSet = new Set(features);
	if (featureSet.has("thinking") || featureSet.has("reasoning")) {
		return buildReasoningSupport({
			supported: true,
			mode: "toggle",
			values: ["off", "on"],
			defaultValue: "off",
		});
	}

	return buildReasoningSupport({
		supported: false,
		mode: "unsupported",
		values: [],
		defaultValue: "off",
	});
}

function toErrorMessage(error: unknown): string {
	if (error instanceof Error && error.message.trim()) return error.message.trim();
	return String(error);
}

async function readResponseError(response: Response): Promise<string> {
	try {
		const text = (await response.text()).trim();
		if (text) return text;
	} catch {
		// ignore response parsing failures
	}
	return `HTTP ${response.status}`;
}

export function resolveOllamaHost(env: NodeJS.ProcessEnv = process.env): string | null {
	const configured = env.OLLAMA_HOST?.trim();
	if (!configured) return null;
	const normalized = normalizeUrlRoot(configured);
	return normalized || null;
}

function buildPiRegistryReasoningCapability(model: {
	id: string;
	reasoning?: boolean;
}): AvailableModelReasoningCapability {
	if (!model.reasoning) {
		return buildReasoningSupport({
			supported: false,
			mode: "unsupported",
			values: [],
			defaultValue: "off",
		});
	}

	return buildReasoningSupport({
		supported: true,
		mode: "toggle",
		values: ["off", "on"],
		defaultValue: "on",
	});
}

type PiRegistryModelLike = {
	provider: string;
	id: string;
	name?: string;
	api?: string;
	baseUrl?: string;
	reasoning?: boolean;
	contextWindow?: number;
	maxTokens?: number;
};

function buildAvailableModelFromPiRegistry(
	model: PiRegistryModelLike,
	auth: ModelProviderAuthMetadata | null,
): AvailableModel {
	return {
		source: "pi-model-registry",
		provider: model.provider,
		label: typeof model.name === "string" && model.name.trim() ? model.name.trim() : model.id,
		model: model.id,
		available: true,
		defaults: {
			runConfig: {
				reasoning_effort: model.reasoning ? "on" : "off",
			},
		},
		capabilities: {
			runConfig: {
				reasoning_effort: buildPiRegistryReasoningCapability(model),
			},
		},
		metadata: {
			...(typeof model.api === "string" && model.api.trim() ? { api: model.api } : {}),
			...(typeof model.baseUrl === "string" && model.baseUrl.trim() ? { baseUrl: model.baseUrl } : {}),
			...(typeof model.contextWindow === "number" ? { contextWindow: model.contextWindow } : {}),
			...(typeof model.maxTokens === "number" ? { maxOutputTokens: model.maxTokens, maxTokens: model.maxTokens } : {}),
		},
		...(auth ? { auth } : {}),
	};
}

function shouldExposePiRegistryProviderInAstroUi(provider: string): boolean {
	return shouldExposeAuthBackedProviderInAstroUi(provider);
}

export class PiAvailableModelCollector implements AvailableModelCollector {
	readonly source = "pi-model-registry";

	async collect(context: Required<AvailableModelContext>): Promise<AvailableModelCollectionResult> {
		const originalPiAgentDir = process.env.PI_CODING_AGENT_DIR;
		const scopedPiAgentDir = context.env.PI_CODING_AGENT_DIR;
		if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
			process.env.PI_CODING_AGENT_DIR = scopedPiAgentDir;
		}

		let authStorage: AuthStorage;
		let modelRegistry: ModelRegistry;
		try {
			authStorage = AuthStorage.create();
			modelRegistry = new ModelRegistry(authStorage);
		} finally {
			if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
				if (originalPiAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
				else process.env.PI_CODING_AGENT_DIR = originalPiAgentDir;
			}
		}

		const allModels = modelRegistry.getAll();
		const availableModels = allModels
			.filter((model) => shouldExposePiRegistryProviderInAstroUi(model.provider))
			.filter((model) =>
				shouldExposeProviderModelsInAvailableList(model.provider, context.env, {
					authStorage,
					modelRegistry,
				}),
			)
			.map((model) =>
				buildAvailableModelFromPiRegistry(
					model,
					getModelProviderAuthMetadata(model.provider, context.env, {
						authStorage,
						modelRegistry,
					}),
				),
			)
			.sort((left, right) => {
				if (left.provider !== right.provider) return left.provider.localeCompare(right.provider);
				return left.label.localeCompare(right.label);
			});
		const loadError = modelRegistry.getError();

		return {
			source: this.source,
			ok: !loadError,
			models: availableModels,
			details: {
				auth_providers: authStorage.list().sort((left, right) => left.localeCompare(right)),
				available_model_count: availableModels.length,
				total_model_count: allModels.length,
			},
			...(loadError ? { error: toErrorMessage(loadError) } : {}),
		};
	}
}

type OllamaTagEntry = {
	model?: unknown;
	name?: unknown;
};

type OllamaTagsResponse = {
	models?: OllamaTagEntry[];
};

type OllamaShowResponse = {
	capabilities?: unknown;
};

type OllamaModelInspectionResult = {
	model: AvailableModel;
	inspectionSucceeded: boolean;
};

async function inspectOllamaModelCapabilities(options: {
	host: string;
	model: string;
	fetchFn: typeof fetch;
}): Promise<OllamaModelInspectionResult> {
	const { host, model, fetchFn } = options;

	const baseModel: AvailableModel = {
		source: "ollama",
		provider: "ollama",
		label: model,
		model,
		available: true,
		defaults: {
			runConfig: {
				reasoning_effort: "off",
			},
		},
		capabilities: {
			runConfig: {
				reasoning_effort: buildReasoningCapabilityFromFeatures([]),
			},
		},
		auth: {
			required: false,
			authenticated: true,
			usable: true,
		},
		metadata: {
			ollama_host: host,
		},
	};

	let response: Response;
	try {
		response = await fetchFn(`${host}/api/show`, {
			method: "POST",
			headers: {
				Accept: "application/json",
				"Content-Type": "application/json",
			},
			body: JSON.stringify({ model }),
		});
	} catch (error) {
		return {
			model: {
				...baseModel,
				metadata: {
					...baseModel.metadata,
					ollama_show_error: `Failed to inspect model at ${host}: ${toErrorMessage(error)}`,
				},
			},
			inspectionSucceeded: false,
		};
	}

	if (!response.ok) {
		return {
			model: {
				...baseModel,
				metadata: {
					...baseModel.metadata,
					ollama_show_error: `Ollama model inspection failed at ${host}: ${await readResponseError(
						response,
					)}`,
				},
			},
			inspectionSucceeded: false,
		};
	}

	let payload: OllamaShowResponse;
	try {
		payload = (await response.json()) as OllamaShowResponse;
	} catch (error) {
		return {
			model: {
				...baseModel,
				metadata: {
					...baseModel.metadata,
					ollama_show_error: `Ollama returned invalid model details JSON: ${toErrorMessage(error)}`,
				},
			},
			inspectionSucceeded: false,
		};
	}

	const features = normalizeCapabilityList(payload.capabilities);
	return {
		model: {
			...baseModel,
			capabilities: {
				features,
				runConfig: {
					reasoning_effort: buildReasoningCapabilityFromFeatures(features),
				},
			},
		},
		inspectionSucceeded: true,
	};
}

export class OllamaModelCollector implements AvailableModelCollector {
	readonly source = "ollama";

	async collect(context: Required<AvailableModelContext>): Promise<AvailableModelCollectionResult> {
		const host = resolveOllamaHost(context.env);
		if (!host) {
			return {
				source: this.source,
				ok: false,
				models: [],
				error: "OLLAMA_HOST is not configured.",
			};
		}

		let response: Response;
		try {
			response = await context.fetchFn(`${host}/api/tags`, {
				headers: {
					Accept: "application/json",
				},
			});
		} catch (error) {
			return {
				source: this.source,
				ok: false,
				models: [],
				details: { host },
				error: `Failed to reach Ollama at ${host}: ${toErrorMessage(error)}`,
			};
		}

		if (!response.ok) {
			return {
				source: this.source,
				ok: false,
				models: [],
				details: { host },
				error: `Ollama model discovery failed at ${host}: ${await readResponseError(response)}`,
			};
		}

		let payload: OllamaTagsResponse;
		try {
			payload = (await response.json()) as OllamaTagsResponse;
		} catch (error) {
			return {
				source: this.source,
				ok: false,
				models: [],
				details: { host },
				error: `Ollama returned invalid JSON: ${toErrorMessage(error)}`,
			};
		}

		const modelNames = (Array.isArray(payload.models) ? payload.models : [])
			.map((entry) => normalizeModelName(entry.model) ?? normalizeModelName(entry.name))
			.filter((value): value is string => Boolean(value))
			.filter((value, index, array) => array.indexOf(value) === index)
			.sort((left, right) => left.localeCompare(right));

		const inspectionResults = await Promise.all(
			modelNames.map((model) =>
				inspectOllamaModelCapabilities({
					host,
					model,
					fetchFn: context.fetchFn,
				}),
			),
		);

		const models = inspectionResults.map((result) => result.model);
		const inspectionErrorCount = inspectionResults.filter((result) => !result.inspectionSucceeded).length;

		return {
			source: this.source,
			ok: true,
			models,
			details: {
				host,
				listed_model_count: modelNames.length,
				inspected_model_count: modelNames.length - inspectionErrorCount,
				inspection_error_count: inspectionErrorCount,
			},
		};
	}
}

export function getAvailableModelCollectors(): AvailableModelCollector[] {
	return [new PiAvailableModelCollector(), new OllamaModelCollector()];
}

export async function collectAvailableModels(
	context: AvailableModelContext = {},
	collectors: AvailableModelCollector[] = getAvailableModelCollectors(),
): Promise<AvailableModelsResponse> {
	const resolvedContext: Required<AvailableModelContext> = {
		env: context.env ?? process.env,
		fetchFn: context.fetchFn ?? fetch,
		defaultOpenAiProvider: context.defaultOpenAiProvider ?? DEFAULT_OPENAI_PROVIDER,
		defaultOpenAiModel: context.defaultOpenAiModel ?? DEFAULT_OPENAI_MODEL,
	};

	const results = await Promise.all(
		collectors.map(async (collector): Promise<AvailableModelCollectionResult> => {
			try {
				return await collector.collect(resolvedContext);
			} catch (error) {
				return {
					source: collector.source,
					ok: false,
					models: [],
					error: toErrorMessage(error),
				};
			}
		}),
	);

	const models = results
		.flatMap((result) => result.models)
		.sort((left, right) => {
			if (left.provider !== right.provider) return left.provider.localeCompare(right.provider);
			if (left.source !== right.source) return left.source.localeCompare(right.source);
			return left.label.localeCompare(right.label);
		});

	const providers = models.reduce<AvailableModelProviderGroup[]>((groups, model) => {
		const existingGroup = groups.find((group) => group.provider === model.provider);
		if (existingGroup) {
			existingGroup.models.push(model);
			return groups;
		}
		groups.push({
			provider: model.provider,
			models: [model],
		});
		return groups;
	}, []);

	const sources = results.map((result) => ({
		source: result.source,
		ok: result.ok,
		count: result.models.length,
		...(result.details ? { details: result.details } : {}),
		...(result.error ? { error: result.error } : {}),
	}));

	return {
		version: 1,
		providers,
		sources,
	};
}

export function collectModelCatalog(context: AvailableModelContext = {}): ModelCatalogResponse {
	const resolvedContext: Required<AvailableModelContext> = {
		env: context.env ?? process.env,
		fetchFn: context.fetchFn ?? fetch,
		defaultOpenAiProvider: context.defaultOpenAiProvider ?? DEFAULT_OPENAI_PROVIDER,
		defaultOpenAiModel: context.defaultOpenAiModel ?? DEFAULT_OPENAI_MODEL,
	};

	const { authStorage, modelRegistry } = createScopedPiRuntime(resolvedContext.env);
	const allModels = modelRegistry
		.getAll()
		.filter((model) => shouldExposePiRegistryProviderInAstroUi(model.provider))
		.map((model) =>
			buildAvailableModelFromPiRegistry(
				model,
				getModelProviderAuthMetadata(model.provider, resolvedContext.env, {
					authStorage,
					modelRegistry,
				}),
			),
		)
		.sort((left, right) => {
			if (left.provider !== right.provider) return left.provider.localeCompare(right.provider);
			return left.label.localeCompare(right.label);
		});
	const loadError = modelRegistry.getError();

	return {
		version: 1,
		models: allModels,
		sources: [
			{
				source: "pi-model-registry",
				ok: !loadError,
				count: allModels.length,
				details: {
					auth_providers: authStorage.list().sort((left, right) => left.localeCompare(right)),
					total_model_count: allModels.length,
				},
				...(loadError ? { error: toErrorMessage(loadError) } : {}),
			},
		],
	};
}
