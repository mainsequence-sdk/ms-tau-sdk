import { randomUUID } from "node:crypto";
import {
	buildStrictJsonRepairPrompt,
	normalizeA2AOutputOptions,
	validateStrictJsonText,
	type A2AOutputOptions,
} from "./a2a-output.js";
import type { PiCredential } from "./model-provider-credentials-client.js";
import type {
	BackendModelCatalogCapability,
	BackendProviderCredentialsCapability,
} from "../../adapters/types.js";

export type StatelessLlmMessage = {
	role: "system" | "user" | "assistant";
	content: string;
};

export type StatelessLlmChatSuccess = {
	ok: true;
	provider: string;
	model: string;
	message: {
		role: "assistant";
		content: string;
	};
	json?: unknown;
	finish_reason: string | null;
	usage: {
		input_tokens: number | null;
		output_tokens: number | null;
		total_tokens: number | null;
	};
};

export type StatelessLlmChatFailure = {
	ok: false;
	error: string;
	message: string;
	error_detail?: string | null;
	field_errors?: Record<string, string>;
	backend_status?: number | null;
	backend_response_text?: string | null;
	backend_response_body?: unknown;
};

export type StatelessLlmChatResult = {
	statusCode: number;
	body: StatelessLlmChatSuccess | StatelessLlmChatFailure;
};

export type StatelessLlmLogEvent = {
	event: string;
	message: string;
	severity?: "INFO" | "WARNING" | "ERROR";
	data?: Record<string, unknown>;
};

type ProviderCredential =
	| { ok: true; token: string; source: "env" | "backend"; credentialKind: "api_key" | "oauth" }
	| { ok: false; result: StatelessLlmChatResult };

type ProviderCallResult =
	| {
			ok: true;
			text: string;
			finishReason: string | null;
			usage: StatelessLlmChatSuccess["usage"];
	  }
	| {
			ok: false;
			statusCode: number;
			body: StatelessLlmChatFailure;
	  };

type OpenAiCompatibleResponse = {
	choices?: Array<{
		finish_reason?: string | null;
		message?: {
			content?: unknown;
		};
	}>;
	usage?: {
		prompt_tokens?: unknown;
		completion_tokens?: unknown;
		total_tokens?: unknown;
		input_tokens?: unknown;
		output_tokens?: unknown;
	};
};

const DEFAULT_PROVIDER = "openai";
const DEFAULT_TIMEOUT_SECONDS = 120;
const MAX_TIMEOUT_SECONDS = 900;
const ASTRO_METADATA_KEY = "astro";
const ASTRO_METADATA_FIELDS = new Set(["provider", "json_repair", "omit_reasoning", "timeout_seconds"]);
const SESSION_RUNTIME_FIELDS = [
	"agent_session_uid",
	"agentSessionUid",
	"thread_id",
	"threadId",
	"agent_type",
	"agentType",
	"runtime_turn_timeout_seconds",
	"runtimeTurnTimeoutSeconds",
];
const NON_CANONICAL_REQUEST_FIELDS = [
	"provider",
	"message",
	"prompt",
	"input",
	"responseFormat",
	"json_repair",
	"jsonRepair",
	"omit_reasoning",
	"omitReasoning",
	"timeout_seconds",
	"timeoutSeconds",
	"base_url",
	"baseUrl",
	"topP",
	"maxOutputTokens",
	"max_output_tokens",
	"maxTokens",
];

const OPENAI_COMPATIBLE_BASE_URLS: Record<string, string> = {
	openai: "https://api.openai.com/v1",
	"openai-codex": "https://api.openai.com/v1",
	openrouter: "https://openrouter.ai/api/v1",
	groq: "https://api.groq.com/openai/v1",
	cerebras: "https://api.cerebras.ai/v1",
	xai: "https://api.x.ai/v1",
	mistral: "https://api.mistral.ai/v1",
	"vercel-ai-gateway": "https://ai-gateway.vercel.sh/v1",
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function nonEmptyString(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function integerOption(value: unknown): number | null {
	if (typeof value === "number" && Number.isInteger(value)) return value;
	if (typeof value === "string" && /^-?\d+$/.test(value.trim())) {
		return Number.parseInt(value.trim(), 10);
	}
	return null;
}

function numberOption(value: unknown): number | null {
	if (typeof value === "number" && Number.isFinite(value)) return value;
	if (typeof value === "string" && value.trim()) {
		const parsed = Number(value);
		return Number.isFinite(parsed) ? parsed : null;
	}
	return null;
}

function extractAstroMetadata(body: Record<string, unknown>): {
	ok: true;
	metadata: Record<string, unknown>;
} | {
	ok: false;
	result: StatelessLlmChatResult;
} {
	if (body.metadata === undefined) return { ok: true, metadata: {} };
	if (!isPlainObject(body.metadata)) {
		return {
			ok: false,
			result: {
				statusCode: 400,
				body: {
					ok: false,
					error: "invalid_llm_passthrough_request",
					message: "metadata must be an object when provided.",
					field_errors: {
						metadata: "Provide an object. Astro-specific controls belong in metadata.astro.",
					},
				},
			},
		};
	}
	const astro = body.metadata[ASTRO_METADATA_KEY];
	if (astro === undefined) return { ok: true, metadata: {} };
	if (!isPlainObject(astro)) {
		return {
			ok: false,
			result: {
				statusCode: 400,
				body: {
					ok: false,
					error: "invalid_llm_passthrough_request",
					message: "metadata.astro must be an object when provided.",
					field_errors: {
						"metadata.astro": "Provide an object containing Astro passthrough controls.",
					},
				},
			},
		};
	}
	return { ok: true, metadata: astro };
}

function rejectUnsupportedAstroMetadataFields(astroMetadata: Record<string, unknown>): StatelessLlmChatResult | null {
	const fieldErrors: Record<string, string> = {};
	for (const field of Object.keys(astroMetadata)) {
		if (!ASTRO_METADATA_FIELDS.has(field)) {
			fieldErrors[`metadata.astro.${field}`] = "Unsupported Astro passthrough control.";
		}
	}
	if (Object.keys(fieldErrors).length === 0) return null;
	return {
		statusCode: 400,
		body: {
			ok: false,
			error: "invalid_llm_passthrough_request",
			message: "metadata.astro contains unsupported Astro passthrough controls.",
			field_errors: fieldErrors,
		},
	};
}

function normalizeTimeoutSeconds(astroMetadata: Record<string, unknown>): number {
	const configured = numberOption(astroMetadata.timeout_seconds);
	if (configured == null || configured <= 0) return DEFAULT_TIMEOUT_SECONDS;
	return Math.min(Math.trunc(configured), MAX_TIMEOUT_SECONDS);
}

function extractTextContent(content: unknown): string | null {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return null;
	const parts: string[] = [];
	for (const part of content) {
		if (typeof part === "string") {
			parts.push(part);
			continue;
		}
		if (!isPlainObject(part)) continue;
		const text = nonEmptyString(part.text) ?? nonEmptyString(part.content);
		if (text) parts.push(text);
	}
	const joined = parts.join("\n").trim();
	return joined ? joined : null;
}

function normalizeMessages(body: Record<string, unknown>): StatelessLlmMessage[] | null {
	const messages = Array.isArray(body.messages) ? body.messages : null;
	if (!messages) return null;
	const normalized: StatelessLlmMessage[] = [];
	for (const message of messages) {
		if (!isPlainObject(message)) continue;
		const role = nonEmptyString(message.role);
		if (role !== "system" && role !== "user" && role !== "assistant") continue;
		const content = extractTextContent(message.content);
		if (!content) continue;
		normalized.push({ role, content });
	}
	return normalized.length > 0 ? normalized : null;
}

function rejectSessionRuntimeFields(body: Record<string, unknown>): StatelessLlmChatResult | null {
	const fieldErrors: Record<string, string> = {};
	for (const field of SESSION_RUNTIME_FIELDS) {
		if (body[field] !== undefined) {
			fieldErrors[field] = "Session/runtime fields are not allowed on stateless LLM passthrough requests.";
		}
	}
	if (Object.keys(fieldErrors).length === 0) return null;
	return {
		statusCode: 400,
		body: {
			ok: false,
			error: "invalid_llm_passthrough_request",
			message: "Stateless LLM passthrough requests must not include session or runtime fields.",
			field_errors: fieldErrors,
		},
	};
}

function rejectNonCanonicalRequestFields(body: Record<string, unknown>): StatelessLlmChatResult | null {
	const fieldErrors: Record<string, string> = {};
	for (const field of NON_CANONICAL_REQUEST_FIELDS) {
		if (body[field] !== undefined) {
			fieldErrors[field] = "Use the canonical stateless LLM request shape.";
		}
	}
	if (Object.keys(fieldErrors).length === 0) return null;
	return {
		statusCode: 400,
		body: {
			ok: false,
			error: "invalid_llm_passthrough_request",
			message:
				"Stateless LLM passthrough requests use the OpenAI-compatible chat completions body shape. Astro-specific controls belong in metadata.astro.",
			field_errors: fieldErrors,
		},
	};
}

function buildProviderBaseUrl(provider: string, env: NodeJS.ProcessEnv): string | null {
	const envProviderKey = `ASTRO_LLM_PASSTHROUGH_${provider.toUpperCase().replace(/[^A-Z0-9]+/g, "_")}_BASE_URL`;
	const configured = nonEmptyString(env[envProviderKey]) ?? nonEmptyString(env.ASTRO_LLM_PASSTHROUGH_BASE_URL);
	if (configured) return configured.replace(/\/+$/, "");
	return OPENAI_COMPATIBLE_BASE_URLS[provider] ?? null;
}

function extractCredentialToken(credential: PiCredential): string | null {
	if (credential.type === "api_key") return nonEmptyString(credential.key);
	const record = credential as Record<string, unknown>;
	return (
		nonEmptyString(record.access_token) ??
		nonEmptyString(record.accessToken) ??
		nonEmptyString(record.token) ??
		nonEmptyString(record.bearer_token) ??
		nonEmptyString(record.bearerToken) ??
		nonEmptyString(record.api_key) ??
		nonEmptyString(record.apiKey) ??
		nonEmptyString(record.key)
	);
}

async function resolveProviderCredential(input: {
	provider: string;
	userUid: string | null;
	env: NodeJS.ProcessEnv;
	providerCredentials: BackendProviderCredentialsCapability;
	modelCatalog: BackendModelCatalogCapability;
	log?: (event: StatelessLlmLogEvent) => void;
}): Promise<ProviderCredential> {
	const providerDefinition = input.modelCatalog.resolveProviderDefinition(input.provider);
	const envApiKey = providerDefinition?.readEnvApiKey(input.env) ?? null;
	if (envApiKey) {
		return {
			ok: true,
			token: envApiKey,
			source: "env",
			credentialKind: "api_key",
		};
	}

	if (!input.userUid) {
		return {
			ok: false,
			result: {
				statusCode: 400,
				body: {
					ok: false,
					error: "provider_credentials_unavailable",
					message:
						"No provider API key is configured and no user_uid was provided for backend credential hydration.",
				},
			},
		};
	}
	if (!providerDefinition) {
		return {
			ok: false,
			result: {
				statusCode: 404,
				body: {
					ok: false,
					error: "provider_not_supported",
					message: `Provider "${input.provider}" is not supported by model-provider credential hydration.`,
				},
			},
		};
	}

	const hydrated = await input.providerCredentials.hydrate({
		env: input.env,
		log: (message) => input.log?.({
			event: "llm_passthrough_credentials_log",
			message,
		}),
		createdByUser: input.userUid,
		agentSessionUid: null,
		providers: [input.provider],
		holderId: `llm-passthrough/${randomUUID()}`,
	});
	if (hydrated.ok === false) {
		return {
			ok: false,
			result: {
				statusCode: hydrated.status ?? 503,
				body: {
					ok: false,
					error: "provider_credentials_unavailable",
					message: hydrated.error,
					backend_status: hydrated.status,
					backend_response_text: hydrated.responseText,
					backend_response_body: hydrated.body,
				},
			},
		};
	}
	const credential = hydrated.body.credentials[input.provider]?.pi_credential;
	if (!credential) {
		return {
			ok: false,
			result: {
				statusCode: 409,
				body: {
					ok: false,
					error: "provider_credentials_unavailable",
					message: `No usable credentials are available for provider ${input.provider}.`,
				},
			},
		};
	}
	const token = extractCredentialToken(credential);
	if (!token) {
		return {
			ok: false,
			result: {
				statusCode: 409,
				body: {
					ok: false,
					error: "provider_credentials_unavailable",
					message: `Provider ${input.provider} credentials cannot be used by the stateless passthrough fast path.`,
				},
			},
		};
	}
	return {
		ok: true,
		token,
		source: "backend",
		credentialKind: credential.type === "oauth" ? "oauth" : "api_key",
	};
}

async function readResponseBody(response: Response): Promise<{ text: string; json: unknown }> {
	const text = await response.text();
	if (!text.trim()) return { text, json: null };
	try {
		return { text, json: JSON.parse(text) };
	} catch {
		return { text, json: null };
	}
}

function extractProviderError(body: { text: string; json: unknown }, fallback: string): string {
	if (isPlainObject(body.json)) {
		const rawError = body.json.error;
		if (typeof rawError === "string" && rawError.trim()) return rawError.trim();
		if (isPlainObject(rawError)) {
			const message = nonEmptyString(rawError.message) ?? nonEmptyString(rawError.code);
			if (message) return message;
		}
		const message = nonEmptyString(body.json.message) ?? nonEmptyString(body.json.detail);
		if (message) return message;
	}
	return body.text.trim() || fallback;
}

function normalizeUsage(usage: OpenAiCompatibleResponse["usage"]): StatelessLlmChatSuccess["usage"] {
	return {
		input_tokens: integerOption(usage?.prompt_tokens ?? usage?.input_tokens),
		output_tokens: integerOption(usage?.completion_tokens ?? usage?.output_tokens),
		total_tokens: integerOption(usage?.total_tokens),
	};
}

function providerResponseFormat(options: A2AOutputOptions): Record<string, unknown> | undefined {
	if (!options.strictJson) return undefined;
	if (options.jsonMode === "json_schema" && options.responseFormat && typeof options.responseFormat === "object") {
		return options.responseFormat;
	}
	if (options.jsonMode === "json_object" || options.jsonMode === "json") {
		return { type: "json_object" };
	}
	return undefined;
}

function buildProviderPayload(input: {
	model: string;
	messages: StatelessLlmMessage[];
	body: Record<string, unknown>;
	options: A2AOutputOptions;
}): Record<string, unknown> {
	const payload: Record<string, unknown> = {
		model: input.model,
		messages: input.messages,
	};
	const temperature = numberOption(input.body.temperature);
	if (temperature != null) payload.temperature = temperature;
	const topP = numberOption(input.body.top_p);
	if (topP != null) payload.top_p = topP;
	const maxTokens = integerOption(input.body.max_tokens);
	if (maxTokens != null && maxTokens > 0) payload.max_tokens = maxTokens;
	const responseFormat = providerResponseFormat(input.options);
	if (responseFormat) payload.response_format = responseFormat;
	if (isPlainObject(input.body.metadata)) {
		const { [ASTRO_METADATA_KEY]: _astro, ...providerMetadata } = input.body.metadata;
		if (Object.keys(providerMetadata).length > 0) {
			payload.metadata = providerMetadata;
		}
	}
	return payload;
}

async function callOpenAiCompatibleProvider(input: {
	provider: string;
	model: string;
	messages: StatelessLlmMessage[];
	body: Record<string, unknown>;
	options: A2AOutputOptions;
	baseUrl: string;
	token: string;
	timeoutSeconds: number;
	fetchFn: typeof fetch;
}): Promise<ProviderCallResult> {
	const controller = new AbortController();
	const timeout = setTimeout(() => controller.abort(), input.timeoutSeconds * 1000);
	timeout.unref?.();
	let response: Response;
	try {
		response = await input.fetchFn(`${input.baseUrl}/chat/completions`, {
			method: "POST",
			headers: {
				Authorization: `Bearer ${input.token}`,
				"Content-Type": "application/json",
			},
			body: JSON.stringify(
				buildProviderPayload({
					model: input.model,
					messages: input.messages,
					body: input.body,
					options: input.options,
				}),
			),
			signal: controller.signal,
		});
	} catch (error) {
		const aborted = error instanceof Error && error.name === "AbortError";
		return {
			ok: false,
			statusCode: aborted ? 504 : 502,
			body: {
				ok: false,
				error: aborted ? "llm_passthrough_timeout" : "llm_provider_request_failed",
				message: aborted
					? "The provider call exceeded the configured timeout."
					: error instanceof Error
						? error.message
						: String(error),
			},
		};
	} finally {
		clearTimeout(timeout);
	}

	const responseBody = await readResponseBody(response);
	if (!response.ok) {
		return {
			ok: false,
			statusCode: response.status || 502,
			body: {
				ok: false,
				error: "llm_provider_response_error",
				message: extractProviderError(
					responseBody,
					`Provider call failed with status ${response.status}.`,
				),
				backend_status: response.status,
				backend_response_text: responseBody.text,
				backend_response_body: responseBody.json,
			},
		};
	}
	if (!isPlainObject(responseBody.json)) {
		return {
			ok: false,
			statusCode: 502,
			body: {
				ok: false,
				error: "llm_provider_invalid_response",
				message: "Provider returned a non-JSON response.",
				backend_response_text: responseBody.text,
			},
		};
	}

	const parsed = responseBody.json as OpenAiCompatibleResponse;
	const choice = Array.isArray(parsed.choices) ? parsed.choices[0] : null;
	const content = choice?.message?.content;
	const text = typeof content === "string" ? content : "";
	if (!text.trim()) {
		return {
			ok: false,
			statusCode: 502,
			body: {
				ok: false,
				error: "llm_provider_empty_response",
				message: "Provider returned an empty assistant message.",
				backend_response_body: responseBody.json,
			},
		};
	}

	return {
		ok: true,
		text,
		finishReason: choice?.finish_reason ?? null,
		usage: normalizeUsage(parsed.usage),
	};
}

async function validateOrRepairJson(input: {
	text: string;
	provider: string;
	model: string;
	messages: StatelessLlmMessage[];
	body: Record<string, unknown>;
	options: A2AOutputOptions;
	baseUrl: string;
	token: string;
	timeoutSeconds: number;
	fetchFn: typeof fetch;
	log?: (event: StatelessLlmLogEvent) => void;
}): Promise<
	| { ok: true; text: string; json: unknown }
	| { ok: false; statusCode: number; body: StatelessLlmChatFailure }
> {
	let validation = validateStrictJsonText(input.text, input.options);
	if (validation.ok === true) {
		return { ok: true, text: validation.canonicalText, json: validation.value };
	}
	let lastError = validation.error;
	for (let attempt = 1; attempt <= input.options.jsonRepair.attempts; attempt += 1) {
		input.log?.({
			event: "llm_passthrough_json_repair_attempted",
			message: "Astro is attempting to repair invalid stateless LLM JSON output.",
			data: {
				attempt,
				maxAttempts: input.options.jsonRepair.attempts,
				jsonMode: input.options.jsonMode,
				validationError: lastError,
			},
		});
		const repairPrompt = buildStrictJsonRepairPrompt({
			invalidText: input.text,
			validationError: lastError,
			responseFormat: input.options.responseFormat,
			jsonMode: input.options.jsonMode,
			jsonSchema: input.options.jsonSchema,
			attempt,
			maxAttempts: input.options.jsonRepair.attempts,
		});
		const repair = await callOpenAiCompatibleProvider({
			provider: input.provider,
			model: input.model,
			messages: [{ role: "user", content: repairPrompt }],
			body: input.body,
			options: input.options,
			baseUrl: input.baseUrl,
			token: input.token,
			timeoutSeconds: input.timeoutSeconds,
			fetchFn: input.fetchFn,
		});
		if (repair.ok === false) {
			return repair;
		}
		validation = validateStrictJsonText(repair.text, input.options);
		if (validation.ok === true) {
			return { ok: true, text: validation.canonicalText, json: validation.value };
		}
		lastError = validation.error;
	}

	return {
		ok: false,
		statusCode: 422,
		body: {
			ok: false,
			error: "llm_invalid_json_response",
			message: "The model did not produce valid JSON after repair attempts.",
			error_detail: lastError,
		},
	};
}

export async function handleStatelessLlmChat(input: {
	body: Record<string, unknown>;
	userUid?: string | null;
	env?: NodeJS.ProcessEnv;
	fetchFn?: typeof fetch;
	providerCredentials: BackendProviderCredentialsCapability;
	modelCatalog: BackendModelCatalogCapability;
	log?: (event: StatelessLlmLogEvent) => void;
}): Promise<StatelessLlmChatResult> {
	const env = input.env ?? process.env;
	const fetchFn = input.fetchFn ?? fetch;
	const body = input.body;
	const startedAt = Date.now();
	const invalidSessionFields = rejectSessionRuntimeFields(body);
	if (invalidSessionFields) return invalidSessionFields;
	const nonCanonicalFields = rejectNonCanonicalRequestFields(body);
	if (nonCanonicalFields) return nonCanonicalFields;
	const astroMetadataResult = extractAstroMetadata(body);
	if (astroMetadataResult.ok === false) return astroMetadataResult.result;
	const astroMetadata = astroMetadataResult.metadata;
	const invalidAstroMetadataFields = rejectUnsupportedAstroMetadataFields(astroMetadata);
	if (invalidAstroMetadataFields) return invalidAstroMetadataFields;

	const provider = nonEmptyString(astroMetadata.provider) ?? DEFAULT_PROVIDER;
	const model = nonEmptyString(body.model);
	if (!model) {
		return {
			statusCode: 400,
			body: {
				ok: false,
				error: "invalid_llm_passthrough_request",
				message: "Stateless LLM passthrough requests require a model.",
				field_errors: {
					model: "Provide the standard chat completions model field.",
				},
			},
		};
	}
	const messages = normalizeMessages(body);
	if (!messages) {
		return {
			statusCode: 400,
			body: {
				ok: false,
				error: "invalid_llm_passthrough_request",
				message: "Stateless LLM passthrough requests require a non-empty messages array.",
				field_errors: {
					messages: "Provide a non-empty messages array.",
				},
			},
		};
	}

	const baseUrl = buildProviderBaseUrl(provider, env);
	if (!baseUrl) {
		return {
			statusCode: 404,
			body: {
				ok: false,
				error: "provider_not_supported_for_passthrough",
				message: `Provider "${provider}" does not have an OpenAI-compatible passthrough base URL configured.`,
			},
		};
	}

	const timeoutSeconds = normalizeTimeoutSeconds(astroMetadata);
	const options = normalizeA2AOutputOptions({
		enabled: true,
		body: {
			response_format: body.response_format,
			json_repair: astroMetadata.json_repair,
			omit_reasoning: astroMetadata.omit_reasoning,
		},
		a2aContext: {},
		context: {},
	});

	input.log?.({
		event: "llm_passthrough_model_resolved",
		message: "Astro resolved a stateless LLM passthrough model.",
		data: {
			provider,
			model,
			baseUrl,
			strictJson: options.strictJson,
			jsonMode: options.jsonMode,
			timeoutSeconds,
		},
	});

	const credentialStartedAt = Date.now();
	const credential = await resolveProviderCredential({
		provider,
		userUid: input.userUid ?? null,
		env,
		providerCredentials: input.providerCredentials,
		modelCatalog: input.modelCatalog,
		log: input.log,
	});
	if (credential.ok === false) return credential.result;
	input.log?.({
		event: "llm_passthrough_credentials_resolved",
		message: "Astro resolved credentials for a stateless LLM passthrough request.",
		data: {
			provider,
			source: credential.source,
			credentialKind: credential.credentialKind,
			durationMs: Date.now() - credentialStartedAt,
		},
	});

	const providerStartedAt = Date.now();
	const providerResult = await callOpenAiCompatibleProvider({
		provider,
		model,
		messages,
		body,
		options,
		baseUrl,
		token: credential.token,
		timeoutSeconds,
		fetchFn,
	});
	if (providerResult.ok === false) return providerResult;
	input.log?.({
		event: "llm_passthrough_provider_first_output",
		message: "Provider returned output for a stateless LLM passthrough request.",
		data: {
			provider,
			model,
			durationMs: Date.now() - providerStartedAt,
		},
	});

	let text = providerResult.text.trim();
	let parsedJson: unknown = undefined;
	if (options.strictJson) {
		const validation = await validateOrRepairJson({
			text,
			provider,
			model,
			messages,
			body,
			options,
			baseUrl,
			token: credential.token,
			timeoutSeconds,
			fetchFn,
			log: input.log,
		});
		if (validation.ok === false) return validation;
		text = validation.text;
		parsedJson = validation.json;
	}

	input.log?.({
		event: "llm_passthrough_request_completed",
		message: "Astro completed a stateless LLM passthrough request.",
		data: {
			provider,
			model,
			durationMs: Date.now() - startedAt,
			strictJson: options.strictJson,
		},
	});

	return {
		statusCode: 200,
		body: {
			ok: true,
			provider,
			model,
			message: {
				role: "assistant",
				content: text,
			},
			...(options.strictJson ? { json: parsedJson } : {}),
			finish_reason: providerResult.finishReason,
			usage: providerResult.usage,
		},
	};
}
