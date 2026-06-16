export type A2AJsonMode = "none" | "json" | "json_object" | "json_schema";

export type A2AJsonRepairOptions = {
	attempts: number;
};

export type A2AOutputOptions = {
	omitReasoning: boolean;
	strictJson: boolean;
	jsonMode: A2AJsonMode;
	jsonSchema: Record<string, unknown> | null;
	responseFormat: string | Record<string, unknown> | null;
	jsonRepair: A2AJsonRepairOptions;
};

export type StrictJsonValidationResult =
	| { ok: true; value: unknown; canonicalText: string }
	| { ok: false; error: string };

export type A2AOutputContractChunk =
	| { type: "reasoning-start"; id: string }
	| { type: "reasoning-delta"; delta: string }
	| { type: "reasoning-end" }
	| { type: "text-start"; id: string }
	| { type: "text-delta"; textDelta: string }
	| { type: "text-end" }
	| { type: "finish"; finishReason: string; usage?: unknown }
	| {
			type: "error";
			error: string;
			error_source: "runtime";
			error_code: "a2a_invalid_json_response";
			error_detail: string;
			forensics: {
				json_repair_attempts: number;
				json_validation_error: string;
				json_mode: A2AJsonMode;
			};
	  };

export type A2AOutputContractEvent =
	| { type: "thinking_start" }
	| { type: "thinking_delta"; delta: string }
	| { type: "thinking_end" }
	| { type: "text_start" }
	| { type: "text_delta"; delta: string }
	| { type: "text_end" }
	| { type: "message_end_text"; text: string }
	| { type: "done"; reason?: string; usage?: unknown };

export type A2AOutputRepairAttemptInput = {
	attempt: number;
	maxAttempts: number;
	invalidText: string;
	validationError: string;
	responseFormat: string | Record<string, unknown> | null;
	jsonMode: A2AJsonMode;
	jsonSchema: Record<string, unknown> | null;
};

const DEFAULT_JSON_REPAIR_ATTEMPTS = 3;
const MAX_JSON_REPAIR_ATTEMPTS = 10;

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeResponseFormat(value: unknown): string | Record<string, unknown> | null {
	if (typeof value === "string" && value.trim()) return value.trim();
	if (isPlainObject(value)) return value;
	return null;
}

export function normalizeBooleanOption(value: unknown): boolean | null {
	if (typeof value === "boolean") return value;
	if (typeof value !== "string") return null;
	const normalized = value.trim().toLowerCase();
	if (["1", "true", "yes", "on"].includes(normalized)) return true;
	if (["0", "false", "no", "off"].includes(normalized)) return false;
	return null;
}

function normalizeIntegerOption(value: unknown): number | null {
	if (typeof value === "number" && Number.isInteger(value)) return value;
	if (typeof value === "string" && /^-?\d+$/.test(value.trim())) {
		return Number.parseInt(value.trim(), 10);
	}
	return null;
}

function clampRepairAttempts(value: number): number {
	if (value <= 0) return 0;
	return Math.min(value, MAX_JSON_REPAIR_ATTEMPTS);
}

function normalizeJsonRepairAttempts(...sources: unknown[]): number {
	for (const source of sources) {
		if (source == null) continue;
		const direct = normalizeIntegerOption(source);
		if (direct != null) return clampRepairAttempts(direct);
		if (isPlainObject(source)) {
			const attempts = normalizeIntegerOption(source.attempts);
			if (attempts != null) return clampRepairAttempts(attempts);
		}
	}
	return DEFAULT_JSON_REPAIR_ATTEMPTS;
}

function normalizeJsonModeValue(value: unknown): A2AJsonMode {
	if (typeof value !== "string") return "none";
	const normalized = value.trim().toLowerCase();
	if (["json", "application/json"].includes(normalized)) return "json";
	if (normalized === "json_object") return "json_object";
	if (normalized === "json_schema") return "json_schema";
	return "none";
}

function resolveStrictJsonFormat(
	responseFormat: string | Record<string, unknown> | null,
): { strictJson: boolean; jsonMode: A2AJsonMode; jsonSchema: Record<string, unknown> | null } {
	if (typeof responseFormat === "string") {
		const jsonMode = normalizeJsonModeValue(responseFormat);
		return {
			strictJson: jsonMode !== "none",
			jsonMode,
			jsonSchema: null,
		};
	}
	if (!isPlainObject(responseFormat)) {
		return {
			strictJson: false,
			jsonMode: "none",
			jsonSchema: null,
		};
	}
	if (responseFormat.strict !== true) {
		return {
			strictJson: false,
			jsonMode: "none",
			jsonSchema: null,
		};
	}
	let jsonMode = normalizeJsonModeValue(responseFormat.type);
	if (jsonMode === "none") {
		jsonMode = normalizeJsonModeValue(responseFormat.format);
	}
	if (jsonMode === "none") {
		return {
			strictJson: false,
			jsonMode: "none",
			jsonSchema: null,
		};
	}
	return {
		strictJson: true,
		jsonMode,
		jsonSchema: jsonMode === "json_schema" && isPlainObject(responseFormat.schema)
			? responseFormat.schema
			: null,
	};
}

export function resolveA2AResponseFormat(input: {
	body?: Record<string, unknown> | null;
	a2aContext?: Record<string, unknown> | null;
	context?: Record<string, unknown> | null;
	envelopeResponseFormat?: string | Record<string, unknown> | null;
}): string | Record<string, unknown> | null {
	return (
		normalizeResponseFormat(input.body?.response_format) ??
		normalizeResponseFormat(input.body?.responseFormat) ??
		normalizeResponseFormat(input.a2aContext?.response_format) ??
		normalizeResponseFormat(input.a2aContext?.responseFormat) ??
		normalizeResponseFormat(input.context?.response_format) ??
		normalizeResponseFormat(input.context?.responseFormat) ??
		normalizeResponseFormat(input.envelopeResponseFormat) ??
		null
	);
}

export function normalizeA2AOutputOptions(input: {
	enabled: boolean;
	body?: Record<string, unknown> | null;
	a2aContext?: Record<string, unknown> | null;
	context?: Record<string, unknown> | null;
	envelopeResponseFormat?: string | Record<string, unknown> | null;
}): A2AOutputOptions {
	const responseFormat = resolveA2AResponseFormat(input);
	const strictJson = resolveStrictJsonFormat(responseFormat);
	const jsonRepairAttempts = normalizeJsonRepairAttempts(
		input.body?.json_repair,
		input.body?.jsonRepair,
		input.a2aContext?.json_repair,
		input.a2aContext?.jsonRepair,
		input.context?.json_repair,
		input.context?.jsonRepair,
	);
	if (!input.enabled) {
		return {
			omitReasoning: false,
			strictJson: false,
			jsonMode: "none",
			jsonSchema: null,
			responseFormat,
			jsonRepair: { attempts: jsonRepairAttempts },
		};
	}
	const omitReasoning =
		normalizeBooleanOption(input.body?.omit_reasoning) ??
		normalizeBooleanOption(input.body?.omitReasoning) ??
		normalizeBooleanOption(input.a2aContext?.omit_reasoning) ??
		normalizeBooleanOption(input.a2aContext?.omitReasoning) ??
		normalizeBooleanOption(input.context?.omit_reasoning) ??
		normalizeBooleanOption(input.context?.omitReasoning) ??
		false;
	return {
		omitReasoning,
		strictJson: strictJson.strictJson,
		jsonMode: strictJson.jsonMode,
		jsonSchema: strictJson.jsonSchema,
		responseFormat,
		jsonRepair: { attempts: jsonRepairAttempts },
	};
}

export function validateStrictJsonText(
	text: string,
	options: Pick<A2AOutputOptions, "jsonMode">,
): StrictJsonValidationResult {
	const trimmed = text.trim();
	if (!trimmed) {
		return { ok: false, error: "Response text is empty." };
	}
	let value: unknown;
	try {
		value = JSON.parse(trimmed);
	} catch (error) {
		return {
			ok: false,
			error: error instanceof Error ? error.message : String(error),
		};
	}
	if (
		(options.jsonMode === "json_object" || options.jsonMode === "json_schema") &&
		(!value || typeof value !== "object" || Array.isArray(value))
	) {
		return {
			ok: false,
			error: "Response must be a JSON object.",
		};
	}
	return {
		ok: true,
		value,
		canonicalText: JSON.stringify(value),
	};
}

export function buildStrictJsonRepairPrompt(input: {
	invalidText: string;
	validationError: string;
	responseFormat: string | Record<string, unknown> | null;
	jsonMode: A2AJsonMode;
	jsonSchema: Record<string, unknown> | null;
	attempt: number;
	maxAttempts: number;
}): string {
	const responseFormatText =
		typeof input.responseFormat === "string"
			? input.responseFormat
			: input.responseFormat
				? JSON.stringify(input.responseFormat)
				: input.jsonMode;
	const schemaText = input.jsonSchema ? `\nJSON schema: ${JSON.stringify(input.jsonSchema)}` : "";
	return [
		"Repair this assistant response into valid JSON.",
		`Repair attempt: ${input.attempt} of ${input.maxAttempts}.`,
		`Required response format: ${responseFormatText}.`,
		`Validation error: ${input.validationError}.`,
		schemaText.trim(),
		"Return only corrected JSON.",
		"Do not include prose, markdown fences, explanations, or comments.",
		"Original invalid response:",
		input.invalidText,
	]
		.filter(Boolean)
		.join("\n");
}

function emitCanonicalText(
	chunks: A2AOutputContractChunk[],
	counters: { text: number },
	text: string,
) {
	const trimmed = text.trim();
	if (!trimmed) return;
	counters.text += 1;
	const id = `t${counters.text}`;
	chunks.push({ type: "text-start", id });
	chunks.push({ type: "text-delta", textDelta: trimmed });
	chunks.push({ type: "text-end" });
}

function buildInvalidJsonChunk(
	options: A2AOutputOptions,
	error: string,
): Extract<A2AOutputContractChunk, { type: "error" }> {
	return {
		type: "error",
		error: "The A2A response was not valid JSON after repair attempts.",
		error_source: "runtime",
		error_code: "a2a_invalid_json_response",
		error_detail: error,
		forensics: {
			json_repair_attempts: options.jsonRepair.attempts,
			json_validation_error: error,
			json_mode: options.jsonMode,
		},
	};
}

export async function runA2AOutputContractTurn(input: {
	options: A2AOutputOptions;
	events: A2AOutputContractEvent[];
	repair?: (attempt: A2AOutputRepairAttemptInput) => string | Promise<string>;
}): Promise<A2AOutputContractChunk[]> {
	const chunks: A2AOutputContractChunk[] = [];
	const counters = { reasoning: 0, text: 0 };
	let strictJsonBufferedText = "";
	let assistantTextSeen = false;

	const finalizeStrictJson = async (): Promise<boolean> => {
		if (!input.options.strictJson) return true;
		const originalText = strictJsonBufferedText;
		let validation = validateStrictJsonText(originalText, input.options);
		if (validation.ok === true) {
			emitCanonicalText(chunks, counters, validation.canonicalText);
			return true;
		}

		let lastError = validation.error;
		for (let attempt = 1; attempt <= input.options.jsonRepair.attempts; attempt += 1) {
			if (!input.repair) break;
			const repairedText = await input.repair({
				attempt,
				maxAttempts: input.options.jsonRepair.attempts,
				invalidText: originalText,
				validationError: lastError,
				responseFormat: input.options.responseFormat,
				jsonMode: input.options.jsonMode,
				jsonSchema: input.options.jsonSchema,
			});
			validation = validateStrictJsonText(repairedText, input.options);
			if (validation.ok === true) {
				emitCanonicalText(chunks, counters, validation.canonicalText);
				return true;
			}
			lastError = validation.error;
		}

		chunks.push(buildInvalidJsonChunk(input.options, lastError));
		return false;
	};

	for (const event of input.events) {
		switch (event.type) {
			case "thinking_start": {
				counters.reasoning += 1;
				if (!input.options.omitReasoning) {
					chunks.push({ type: "reasoning-start", id: `r${counters.reasoning}` });
				}
				break;
			}
			case "thinking_delta":
				if (!input.options.omitReasoning) {
					chunks.push({ type: "reasoning-delta", delta: event.delta });
				}
				break;
			case "thinking_end":
				if (!input.options.omitReasoning) {
					chunks.push({ type: "reasoning-end" });
				}
				break;
			case "text_start":
				assistantTextSeen = true;
				if (!input.options.strictJson) {
					counters.text += 1;
					chunks.push({ type: "text-start", id: `t${counters.text}` });
				}
				break;
			case "text_delta":
				assistantTextSeen = true;
				if (input.options.strictJson) {
					strictJsonBufferedText += event.delta;
				} else {
					chunks.push({ type: "text-delta", textDelta: event.delta });
				}
				break;
			case "text_end":
				if (!input.options.strictJson) {
					chunks.push({ type: "text-end" });
				}
				break;
			case "message_end_text":
				if (!assistantTextSeen) {
					if (input.options.strictJson) {
						assistantTextSeen = true;
						strictJsonBufferedText += event.text;
					} else {
						emitCanonicalText(chunks, counters, event.text);
					}
				}
				break;
			case "done": {
				const finishReason = event.reason ?? "stop";
				const valid = await finalizeStrictJson();
				if (valid) {
					chunks.push({ type: "finish", finishReason, usage: event.usage });
				}
				break;
			}
		}
	}

	return chunks;
}
