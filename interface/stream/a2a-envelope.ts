function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeString(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value.trim() : null;
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
	callerAgentSessionUid: string | null;
	targetAgentSessionUid: string | null;
	targetAgentUid: string | null;
};

export type UserMessageProvenance = {
	origin: "agent";
	channel: "a2a";
	callerAgentType: string | null;
	handleUniqueId: string | null;
	callerAgentSessionUid: string | null;
	targetAgentSessionUid: string | null;
	targetAgentUid: string | null;
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
	const callerAgentSessionUid =
		normalizeString(value.callerAgentSessionUid) ??
		normalizeString(value.caller_agent_session_uid) ??
		normalizeString(callerMetadata?.agentSessionUid) ??
		normalizeString(callerMetadata?.agent_session_uid);
	const targetAgentUid =
		normalizeString(value.targetAgentUid) ?? normalizeString(value.target_agent_uid);
	const targetAgentSessionUid =
		normalizeString(value.targetAgentSessionUid) ??
		normalizeString(value.target_agent_session_uid) ??
		normalizeString(value.runtimeSessionUid) ??
		normalizeString(value.runtime_session_uid);

	return {
		version: 1,
		enabled: true,
		userOrigin: "agent",
		callerAgentType: callerAgentType ?? null,
		callerMetadata,
		responseFormat,
		handleUniqueId: handleUniqueId ?? null,
		callerAgentSessionUid,
		targetAgentSessionUid,
		targetAgentUid,
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
		callerAgentSessionUid: overlay.callerAgentSessionUid ?? base.callerAgentSessionUid,
		targetAgentSessionUid: overlay.targetAgentSessionUid ?? base.targetAgentSessionUid,
		targetAgentUid: overlay.targetAgentUid ?? base.targetAgentUid,
	};
}

export function a2aEnvelopeToUserProvenance(value: A2AEnvelope | null): UserMessageProvenance | null {
	if (!value || value.userOrigin !== "agent") return null;
	return {
		origin: "agent",
		channel: "a2a",
		callerAgentType: value.callerAgentType,
		handleUniqueId: value.handleUniqueId,
		callerAgentSessionUid: value.callerAgentSessionUid,
		targetAgentSessionUid: value.targetAgentSessionUid,
		targetAgentUid: value.targetAgentUid,
	};
}

export function cloneUserMessageProvenance(
	value: UserMessageProvenance | null | undefined,
): UserMessageProvenance | undefined {
	if (!value) return undefined;
	return { ...value };
}
