function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeString(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value.trim() : null;
}

function normalizeNumericId(value: unknown): number | null {
	if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
	if (typeof value === "string" && /^\d+$/.test(value.trim())) {
		const parsed = Number.parseInt(value.trim(), 10);
		return Number.isFinite(parsed) ? parsed : null;
	}
	return null;
}

function cloneRecord(value: Record<string, unknown> | null): Record<string, unknown> | null {
	if (!value) return null;
	return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
}

export type A2AResponseFormat = string | Record<string, unknown> | null;

export type A2AEnvelope = {
	version: 1;
	enabled: true;
	userOrigin: "agent";
	callerAgentType: string | null;
	callerMetadata: Record<string, unknown> | null;
	responseFormat: A2AResponseFormat;
	handleUniqueId: string | null;
	callerAgentSessionId: number | null;
	targetAgentId: number | null;
};

export type UserMessageProvenance = {
	origin: "agent";
	channel: "a2a";
	callerAgentType: string | null;
	handleUniqueId: string | null;
	callerAgentSessionId: number | null;
	targetAgentId: number | null;
};

export function normalizeA2AResponseFormat(value: unknown): A2AResponseFormat {
	if (typeof value === "string" && value.trim()) return value.trim();
	if (isPlainObject(value)) return cloneRecord(value);
	return null;
}

export function cloneA2AEnvelope(value: A2AEnvelope | null): A2AEnvelope | null {
	if (!value) return null;
	return {
		...value,
		callerMetadata: cloneRecord(value.callerMetadata),
		responseFormat:
			typeof value.responseFormat === "string"
				? value.responseFormat
				: isPlainObject(value.responseFormat)
					? cloneRecord(value.responseFormat)
					: null,
	};
}

export function normalizeA2AEnvelope(value: unknown): A2AEnvelope | null {
	if (!isPlainObject(value)) return null;
	const enabled = value.enabled === true || value.isA2A === true || value.agentOriginated === true;
	const userOrigin =
		value.userOrigin === "agent" || value.origin === "agent" || value.agentOriginated === true
			? "agent"
			: null;
	if (!enabled && userOrigin !== "agent") return null;

	const callerMetadata =
		isPlainObject(value.callerMetadata) ? cloneRecord(value.callerMetadata) :
		isPlainObject(value.caller_metadata) ? cloneRecord(value.caller_metadata) :
		isPlainObject(value.caller) ? cloneRecord(value.caller) :
		null;
	const callerAgentType =
		normalizeString(value.callerAgentType) ??
		normalizeString(value.caller_agent_type) ??
		normalizeString(callerMetadata?.agentType) ??
		normalizeString(callerMetadata?.agent_type);
	const responseFormat = normalizeA2AResponseFormat(value.responseFormat ?? value.response_format);
	const handleUniqueId =
		normalizeString(value.handleUniqueId) ?? normalizeString(value.handle_unique_id);
	const callerAgentSessionId =
		normalizeNumericId(value.callerAgentSessionId) ??
		normalizeNumericId(value.caller_agent_session_id) ??
		normalizeNumericId(callerMetadata?.agentSessionId) ??
		normalizeNumericId(callerMetadata?.agent_session_id) ??
		normalizeNumericId(callerMetadata?.sessionId) ??
		normalizeNumericId(callerMetadata?.session_id);
	const targetAgentId =
		normalizeNumericId(value.targetAgentId) ?? normalizeNumericId(value.target_agent_id);

	return {
		version: 1,
		enabled: true,
		userOrigin: "agent",
		callerAgentType: callerAgentType ?? null,
		callerMetadata,
		responseFormat,
		handleUniqueId: handleUniqueId ?? null,
		callerAgentSessionId,
		targetAgentId,
	};
}

export function mergeA2AEnvelopes(
	base: A2AEnvelope | null,
	overlay: A2AEnvelope | null,
): A2AEnvelope | null {
	if (!base) return cloneA2AEnvelope(overlay);
	if (!overlay) return cloneA2AEnvelope(base);
	return {
		version: 1,
		enabled: true,
		userOrigin: "agent",
		callerAgentType: overlay.callerAgentType ?? base.callerAgentType,
		callerMetadata: overlay.callerMetadata ?? base.callerMetadata,
		responseFormat: overlay.responseFormat ?? base.responseFormat,
		handleUniqueId: overlay.handleUniqueId ?? base.handleUniqueId,
		callerAgentSessionId: overlay.callerAgentSessionId ?? base.callerAgentSessionId,
		targetAgentId: overlay.targetAgentId ?? base.targetAgentId,
	};
}

export function a2aEnvelopeToUserProvenance(value: A2AEnvelope | null): UserMessageProvenance | null {
	if (!value || value.userOrigin !== "agent") return null;
	return {
		origin: "agent",
		channel: "a2a",
		callerAgentType: value.callerAgentType,
		handleUniqueId: value.handleUniqueId,
		callerAgentSessionId: value.callerAgentSessionId,
		targetAgentId: value.targetAgentId,
	};
}

export function cloneUserMessageProvenance(
	value: UserMessageProvenance | null | undefined,
): UserMessageProvenance | undefined {
	if (!value) return undefined;
	return { ...value };
}
