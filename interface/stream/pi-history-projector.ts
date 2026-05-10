import {
	migrateSessionEntries,
	type CompactionEntry,
	type FileEntry,
	type SessionEntry,
	type SessionMessageEntry,
} from "../../node_modules/@mariozechner/pi-coding-agent/dist/core/session-manager.js";
import {
	a2aEnvelopeToUserProvenance,
	cloneA2AEnvelope,
	cloneUserMessageProvenance,
	normalizeA2AEnvelope,
} from "./a2a-envelope.js";
import type {
	ConversationHistorySnapshot,
	ConversationMessage,
	ConversationMessagePart,
} from "./conversation-store.js";

type HistorySessionEnvelope = ConversationHistorySnapshot["session"];
type ProjectedMessage = {
	message: ConversationMessage;
	piEntryId: string | null;
};
type HistoryAnnotation = {
	assistantOrdinal: number;
	piEntryId: string | null;
	hadReasoning: boolean;
	reasoningTextPersisted: boolean;
};
export type PiSessionJsonlRepair =
	| {
			kind: "synthetic_tool_call";
			orphanedEntryId: string;
			syntheticEntryId: string;
			toolCallId: string;
			toolName: string;
	  }
	| {
			kind: "missing_parent_root_rewire";
			entryId: string;
			missingParentId: string;
	  };
const LATEST_USER_MESSAGE_MARKER = "Latest user message:";
const ACTIVE_SESSION_CONTEXT_INSTRUCTION =
	"Use the active session for prior conversation context when the same backend agent session is reused.";

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parsePiJsonl(content: string): FileEntry[] {
	if (!content.trim()) return [];
	if (!content.endsWith("\n")) {
		throw new Error("incomplete_pi_session_jsonl_trailing_line");
	}

	const entries: FileEntry[] = [];
	const lines = content.split("\n");
	for (let index = 0; index < lines.length; index += 1) {
		const line = lines[index];
		if (!line.trim()) continue;

		let parsed: unknown;
		try {
			parsed = JSON.parse(line);
		} catch {
			throw new Error(`invalid_pi_session_jsonl_line_${index + 1}`);
		}
		if (!isRecord(parsed)) {
			throw new Error(`invalid_pi_session_jsonl_line_${index + 1}_not_object`);
		}
		entries.push(parsed as unknown as FileEntry);
	}

	return entries;
}

function isSessionEntry(entry: FileEntry): entry is SessionEntry {
	return entry.type !== "session" && typeof (entry as { id?: unknown }).id === "string";
}

function getCurrentBranch(entries: FileEntry[]): SessionEntry[] {
	const sessionEntries = entries.filter(isSessionEntry);
	if (sessionEntries.length === 0) return [];

	const byId = new Map<string, SessionEntry>();
	for (const entry of sessionEntries) {
		if (byId.has(entry.id)) {
			throw new Error(`duplicate_pi_session_entry_id:${entry.id}`);
		}
		byId.set(entry.id, entry);
	}

	const branch: SessionEntry[] = [];
	const seen = new Set<string>();
	let current: SessionEntry | undefined = sessionEntries[sessionEntries.length - 1];
	while (current) {
		if (seen.has(current.id)) {
			throw new Error(`cyclic_pi_session_branch:${current.id}`);
		}
		seen.add(current.id);
		branch.push(current);

		if (!current.parentId) break;
		current = byId.get(current.parentId);
		if (!current) {
			throw new Error(`missing_pi_session_parent:${branch[branch.length - 1].parentId}`);
		}
	}

	return branch.reverse();
}

function isVisibleMessageEntry(entry: SessionEntry): entry is SessionMessageEntry {
	if (entry.type !== "message") return false;
	const role = (entry.message as { role?: unknown } | undefined)?.role;
	return role === "user" || role === "assistant";
}

function extractAssistantToolCallIds(content: unknown): string[] {
	if (!Array.isArray(content)) return [];
	const toolCallIds: string[] = [];
	for (const part of content) {
		if (!isRecord(part) || part.type !== "toolCall") continue;
		if (typeof part.id !== "string" || !part.id.trim()) {
			throw new Error("invalid_pi_session_tool_call_missing_id");
		}
		toolCallIds.push(part.id.trim());
	}
	return toolCallIds;
}

function createZeroUsage() {
	return {
		input: 0,
		output: 0,
		cacheRead: 0,
		cacheWrite: 0,
		totalTokens: 0,
		cost: {
			input: 0,
			output: 0,
			cacheRead: 0,
			cacheWrite: 0,
			total: 0,
		},
	};
}

function deriveSyntheticTimestamps(entry: SessionEntry): { entryTimestamp: string; messageTimestamp: number } {
	const message = isRecord(entry) && isRecord((entry as { message?: unknown }).message)
		? ((entry as { message: Record<string, unknown> }).message)
		: null;
	const messageTimestamp =
		typeof message?.timestamp === "number" && Number.isFinite(message.timestamp)
			? Math.max(0, message.timestamp - 1)
			: Date.now();
	const parsedEntryTimestamp =
		typeof entry.timestamp === "string" && entry.timestamp.trim()
			? Date.parse(entry.timestamp)
			: typeof entry.timestamp === "number" && Number.isFinite(entry.timestamp)
				? (entry.timestamp < 10_000_000_000 ? entry.timestamp * 1000 : entry.timestamp)
				: Number.NaN;
	const entryMillis = Number.isFinite(parsedEntryTimestamp)
		? Math.max(0, parsedEntryTimestamp - 1)
		: messageTimestamp;
	return {
		entryTimestamp: new Date(entryMillis).toISOString(),
		messageTimestamp,
	};
}

function generateSyntheticEntryId(existingIds: Set<string>, orphanedEntryId: string): string {
	let candidate = `repair_${orphanedEntryId}`;
	let suffix = 1;
	while (existingIds.has(candidate)) {
		candidate = `repair_${orphanedEntryId}_${suffix}`;
		suffix += 1;
	}
	existingIds.add(candidate);
	return candidate;
}

function buildSyntheticAssistantToolCallEntry(input: {
	orphanedEntry: SessionEntry;
	syntheticEntryId: string;
	toolCallId: string;
	toolName: string;
}): SessionEntry {
	const timestamps = deriveSyntheticTimestamps(input.orphanedEntry);
	return {
		type: "message",
		id: input.syntheticEntryId,
		parentId: input.orphanedEntry.parentId ?? null,
		timestamp: timestamps.entryTimestamp,
		message: {
			role: "assistant",
			content: [
				{
					type: "toolCall",
					id: input.toolCallId,
					name: input.toolName,
					arguments: {},
				},
			],
			api: "astro-session-repair",
			provider: "astro-session-repair",
			model: "synthetic-tool-call",
			usage: createZeroUsage(),
			stopReason: "toolUse",
			timestamp: timestamps.messageTimestamp,
		},
	} as SessionEntry;
}

function stringifyPiSessionEntries(entries: FileEntry[]): string {
	if (entries.length === 0) return "";
	return `${entries.map((entry) => JSON.stringify(entry)).join("\n")}\n`;
}

function extractMissingParentId(error: unknown): string | null {
	const raw = error instanceof Error ? error.message : String(error);
	if (!raw.startsWith("missing_pi_session_parent:")) return null;
	return raw.slice("missing_pi_session_parent:".length) || null;
}

function repairCurrentBranchOrphanedToolResults(entries: FileEntry[]): PiSessionJsonlRepair[] {
	let currentBranch: SessionEntry[];
	try {
		currentBranch = getCurrentBranch(entries);
	} catch (error) {
		if (!extractMissingParentId(error)) throw error;
		const parentRepairs = repairCurrentBranchMissingParent(entries);
		if (parentRepairs.length === 0) throw error;
		return [...parentRepairs, ...repairCurrentBranchOrphanedToolResults(entries)];
	}
	const seenToolCallIds = new Set<string>();
	const orphanedRepairs = new Map<
		string,
		{
			toolCallId: string;
			toolName: string;
			orphanedEntryId: string;
			syntheticEntryId: string;
		}
	>();

	for (const entry of currentBranch) {
		if (entry.type !== "message") continue;
		if (!isRecord(entry.message)) continue;

		const role = entry.message.role;
		if (role === "assistant") {
			for (const toolCallId of extractAssistantToolCallIds(entry.message.content)) {
				if (!seenToolCallIds.has(toolCallId)) {
					seenToolCallIds.add(toolCallId);
				}
			}
			continue;
		}

		if (role !== "toolResult") continue;
		const toolCallId =
			typeof entry.message.toolCallId === "string" && entry.message.toolCallId.trim()
				? entry.message.toolCallId.trim()
				: null;
		if (!toolCallId || seenToolCallIds.has(toolCallId)) continue;
		orphanedRepairs.set(entry.id, {
			toolCallId,
			toolName:
				typeof entry.message.toolName === "string" && entry.message.toolName.trim()
					? entry.message.toolName.trim()
					: "unknown",
			orphanedEntryId: entry.id,
			syntheticEntryId: "",
		});
		seenToolCallIds.add(toolCallId);
	}

	if (orphanedRepairs.size === 0) return [];

	const existingIds = new Set(entries.filter(isSessionEntry).map((entry) => entry.id));
	const repairedEntries: FileEntry[] = [];
	const appliedRepairs: PiSessionJsonlRepair[] = [];

	for (const fileEntry of entries) {
		if (!isSessionEntry(fileEntry)) {
			repairedEntries.push(fileEntry);
			continue;
		}

		const repair = orphanedRepairs.get(fileEntry.id);
		if (!repair) {
			repairedEntries.push(fileEntry);
			continue;
		}

		const syntheticEntryId = generateSyntheticEntryId(existingIds, repair.orphanedEntryId);
		repair.syntheticEntryId = syntheticEntryId;
		repairedEntries.push(
			buildSyntheticAssistantToolCallEntry({
				orphanedEntry: fileEntry,
				syntheticEntryId,
				toolCallId: repair.toolCallId,
				toolName: repair.toolName,
			}),
		);
		repairedEntries.push({
			...fileEntry,
			parentId: syntheticEntryId,
		} as SessionEntry);
		appliedRepairs.push({
			kind: "synthetic_tool_call",
			orphanedEntryId: repair.orphanedEntryId,
			syntheticEntryId,
			toolCallId: repair.toolCallId,
			toolName: repair.toolName,
		});
	}

	entries.splice(0, entries.length, ...repairedEntries);
	return appliedRepairs;
}

function repairCurrentBranchMissingParent(entries: FileEntry[]): PiSessionJsonlRepair[] {
	const sessionEntries = entries.filter(isSessionEntry);
	if (sessionEntries.length === 0) return [];

	const byId = new Map<string, SessionEntry>();
	for (const entry of sessionEntries) {
		if (byId.has(entry.id)) {
			throw new Error(`duplicate_pi_session_entry_id:${entry.id}`);
		}
		byId.set(entry.id, entry);
	}

	const seen = new Set<string>();
	let current: SessionEntry | undefined = sessionEntries[sessionEntries.length - 1];
	while (current) {
		if (seen.has(current.id)) {
			throw new Error(`cyclic_pi_session_branch:${current.id}`);
		}
		seen.add(current.id);
		if (!current.parentId) return [];
		const parentId = current.parentId;
		const parent = byId.get(parentId);
		if (parent) {
			current = parent;
			continue;
		}
		current.parentId = null;
		return [
			{
				kind: "missing_parent_root_rewire",
				entryId: current.id,
				missingParentId: parentId,
			},
		];
	}

	return [];
}

function validateCurrentBranchToolCallConsistency(entries: SessionEntry[]): void {
	const seenToolCallIds = new Set<string>();

	for (const entry of entries) {
		if (entry.type !== "message") continue;
		if (!isRecord(entry.message)) continue;

		const role = entry.message.role;
		if (role === "assistant") {
			for (const toolCallId of extractAssistantToolCallIds(entry.message.content)) {
				if (seenToolCallIds.has(toolCallId)) {
					throw new Error(`duplicate_pi_session_tool_call_id:${toolCallId}`);
				}
				seenToolCallIds.add(toolCallId);
			}
			continue;
		}

		if (role !== "toolResult") continue;
		const toolCallId =
			typeof entry.message.toolCallId === "string" && entry.message.toolCallId.trim()
				? entry.message.toolCallId.trim()
				: null;
		if (!toolCallId) {
			throw new Error(`invalid_pi_session_tool_result_missing_tool_call_id:${entry.id}`);
		}
		if (!seenToolCallIds.has(toolCallId)) {
			throw new Error(`orphaned_pi_session_tool_result:${entry.id}:${toolCallId}`);
		}
	}
}

function isCompactionEntry(entry: SessionEntry): entry is CompactionEntry {
	return (
		entry.type === "compaction" &&
		typeof (entry as { id?: unknown }).id === "string" &&
		typeof (entry as { summary?: unknown }).summary === "string"
	);
}

function appendMessagePart(
	parts: ConversationMessagePart[],
	type: ConversationMessagePart["type"],
	text: string,
): void {
	if (!text) return;
	const lastPart = parts[parts.length - 1];
	if (lastPart?.type === type) {
		lastPart.text += text;
		return;
	}
	parts.push({ type, text });
}

function splitThinkingTaggedText(text: string): ConversationMessagePart[] {
	const parts: ConversationMessagePart[] = [];
	const tagPattern = /<\/?think(?:ing)?>/gi;
	let cursor = 0;
	let inReasoning = false;
	let matched = false;

	for (const match of text.matchAll(tagPattern)) {
		matched = true;
		const index = match.index ?? 0;
		appendMessagePart(parts, inReasoning ? "reasoning" : "text", text.slice(cursor, index));
		const tag = match[0].toLowerCase();
		inReasoning = !tag.startsWith("</");
		cursor = index + match[0].length;
	}

	if (!matched) return [{ type: "text", text }];
	appendMessagePart(parts, inReasoning ? "reasoning" : "text", text.slice(cursor));
	return parts;
}

function extractMessageParts(
	content: unknown,
	role: "user" | "assistant",
): ConversationMessagePart[] {
	if (typeof content === "string") {
		return role === "assistant" ? splitThinkingTaggedText(content) : [{ type: "text", text: content }];
	}
	if (!Array.isArray(content)) return [];
	const parts: ConversationMessagePart[] = [];
	for (const part of content) {
		if (!isRecord(part)) continue;
		if (part.type === "text" && typeof part.text === "string") {
			const textParts =
				role === "assistant" ? splitThinkingTaggedText(part.text) : [{ type: "text" as const, text: part.text }];
			for (const textPart of textParts) {
				appendMessagePart(parts, textPart.type, textPart.text);
			}
			continue;
		}
		if (role === "assistant" && part.type === "thinking" && typeof part.thinking === "string") {
			appendMessagePart(parts, "reasoning", part.thinking);
			continue;
		}
		if (role === "assistant" && part.type === "reasoning" && typeof part.text === "string") {
			appendMessagePart(parts, "reasoning", part.text);
		}
	}
	return parts;
}

function stripAstroPromptWrapperFromUserParts(
	parts: ConversationMessagePart[],
): ConversationMessagePart[] {
	return parts.map((part) => {
		if (part.type !== "text") return part;
		return { ...part, text: stripAstroPromptWrapperFromUserText(part.text) };
	});
}

function messagePartsEqual(a: ConversationMessagePart[], b: ConversationMessagePart[]): boolean {
	if (a.length !== b.length) return false;
	for (let index = 0; index < a.length; index += 1) {
		if (a[index].type !== b[index].type || a[index].text !== b[index].text) return false;
	}
	return true;
}

export function stripAstroPromptWrapperFromUserText(text: string): string {
	const markerIndex = text.indexOf(LATEST_USER_MESSAGE_MARKER);
	if (markerIndex === -1) return text;

	const messageStart = markerIndex + LATEST_USER_MESSAGE_MARKER.length;
	let visibleText = text.slice(messageStart);
	const instructionIndex = visibleText.indexOf(ACTIVE_SESSION_CONTEXT_INSTRUCTION);
	if (instructionIndex !== -1) {
		visibleText = visibleText.slice(0, instructionIndex);
	}
	return visibleText.trim();
}

export function sanitizeConversationHistorySnapshot(
	snapshot: ConversationHistorySnapshot,
): { snapshot: ConversationHistorySnapshot; changed: boolean } {
	let changed = false;
	const sanitizeMessage = (message: ConversationMessage): ConversationMessage => {
		const normalizedContent = extractMessageParts(message.content, message.role);
		const nextContent =
			message.role === "user"
				? stripAstroPromptWrapperFromUserParts(normalizedContent)
				: normalizedContent;

		if (messagePartsEqual(message.content, nextContent)) return message;
		changed = true;
		return { ...message, content: nextContent };
	};

	const messages = snapshot.messages.map(sanitizeMessage);
	const inProgressMessage = snapshot.inProgressMessage
		? sanitizeMessage(snapshot.inProgressMessage)
		: null;

	if (!changed) return { snapshot, changed: false };
	return {
		changed: true,
		snapshot: {
			...snapshot,
			messages,
			inProgressMessage,
		},
	};
}

export function applyReasoningAnnotationsToConversationHistorySnapshot(
	snapshot: ConversationHistorySnapshot,
	metadata: unknown,
): { snapshot: ConversationHistorySnapshot; changed: boolean } {
	const messages = applyHistoryAnnotations(
		snapshot.messages.map((message) => ({
			message,
			piEntryId: null,
		})),
		metadata,
		{
			allowOrdinalFallback: true,
		},
	);
	const changed = messages.some((message, index) => message !== snapshot.messages[index]);
	if (!changed) return { snapshot, changed: false };
	return {
		changed: true,
		snapshot: {
			...snapshot,
			messages,
		},
	};
}

function toIsoString(value: unknown): string | null {
	if (typeof value === "string" && value.trim()) {
		const timestamp = Date.parse(value);
		return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : value;
	}
	if (typeof value === "number" && Number.isFinite(value)) {
		const millis = value < 10_000_000_000 ? value * 1000 : value;
		return new Date(millis).toISOString();
	}
	return null;
}

function buildCompactionHistoryMessage(entry: CompactionEntry): ConversationMessage {
	const timestamp = toIsoString(entry.timestamp) ?? new Date(0).toISOString();
	return {
		id: `compaction_${entry.id}`,
		role: "assistant",
		createdAt: timestamp,
		completedAt: timestamp,
		content: [
			{
				type: "text",
				text: `Previous conversation was compacted. Summary:\n\n${entry.summary}`,
			},
		],
	};
}

function resolveA2AEnvelopeFromMetadata(metadata: unknown, session: HistorySessionEnvelope) {
	const sessionEnvelope = normalizeA2AEnvelope(session.a2a);
	if (sessionEnvelope) return sessionEnvelope;
	if (!isRecord(metadata)) return null;
	return normalizeA2AEnvelope(metadata.a2a ?? metadata.a2a_envelope);
}

function normalizeHistoryAnnotations(metadata: unknown): HistoryAnnotation[] {
	if (!isRecord(metadata)) return [];
	const annotations = metadata.history_annotations;
	if (!isRecord(annotations) || annotations.version !== 1 || !Array.isArray(annotations.assistant_messages)) {
		return [];
	}

	const normalized: HistoryAnnotation[] = [];
	for (const rawAnnotation of annotations.assistant_messages) {
		if (!isRecord(rawAnnotation)) continue;
		const assistantOrdinal =
			typeof rawAnnotation.assistant_ordinal === "number" && Number.isFinite(rawAnnotation.assistant_ordinal)
				? rawAnnotation.assistant_ordinal
				: typeof rawAnnotation.assistant_ordinal === "string" && rawAnnotation.assistant_ordinal.trim()
					? Number.parseInt(rawAnnotation.assistant_ordinal, 10)
					: null;
		if (assistantOrdinal == null || assistantOrdinal <= 0 || rawAnnotation.had_reasoning !== true) continue;
		const piEntryId =
			typeof rawAnnotation.pi_entry_id === "string" && rawAnnotation.pi_entry_id.trim()
				? rawAnnotation.pi_entry_id.trim()
				: null;
		normalized.push({
			assistantOrdinal,
			piEntryId,
			hadReasoning: true,
			reasoningTextPersisted: rawAnnotation.reasoning_text_persisted === true,
		});
	}
	return normalized;
}

function messageHasReasoningPart(message: ConversationMessage): boolean {
	return message.content.some((part) => part.type === "reasoning");
}

function applyHistoryAnnotations(
	projectedMessages: ProjectedMessage[],
	metadata: unknown,
	options: { allowOrdinalFallback: boolean },
): ConversationMessage[] {
	const annotations = normalizeHistoryAnnotations(metadata);
	if (annotations.length === 0) return projectedMessages.map((projected) => projected.message);

	const unmatchedAnnotations = new Set(annotations);
	const byPiEntryId = new Map<string, HistoryAnnotation>();
	for (const annotation of annotations) {
		if (annotation.piEntryId) byPiEntryId.set(annotation.piEntryId, annotation);
	}

	let assistantOrdinal = 0;
	return projectedMessages.map((projected) => {
		if (projected.message.role !== "assistant") return projected.message;
		assistantOrdinal += 1;
		const piAnnotation = projected.piEntryId ? byPiEntryId.get(projected.piEntryId) : null;
		const ordinalAnnotation = options.allowOrdinalFallback
			? annotations.find(
					(annotation) =>
						unmatchedAnnotations.has(annotation) && annotation.assistantOrdinal === assistantOrdinal,
			  )
			: null;
		const annotation = piAnnotation ?? ordinalAnnotation ?? null;
		if (!annotation) return projected.message;
		unmatchedAnnotations.delete(annotation);
		if (!annotation.hadReasoning || annotation.reasoningTextPersisted || messageHasReasoningPart(projected.message)) {
			return projected.message;
		}
		return {
			...projected.message,
			content: [
				{
					type: "reasoning",
					text: "",
				},
				...projected.message.content,
			],
		};
	});
}

function projectMessages(
	entries: SessionEntry[],
	options?: { agentUserProvenance?: ConversationMessage["provenance"] },
): ProjectedMessage[] {
	const messages: ProjectedMessage[] = [];
	let userCount = 0;
	let assistantCount = 0;
	const insertedCompactions = new Set<string>();
	const compactionsByFirstKeptEntryId = new Map<string, CompactionEntry[]>();

	for (const entry of entries) {
		if (!isCompactionEntry(entry) || !entry.firstKeptEntryId) continue;
		const existing = compactionsByFirstKeptEntryId.get(entry.firstKeptEntryId) ?? [];
		existing.push(entry);
		compactionsByFirstKeptEntryId.set(entry.firstKeptEntryId, existing);
	}

	for (const entry of entries) {
		const compactionsBeforeEntry = compactionsByFirstKeptEntryId.get(entry.id);
		if (compactionsBeforeEntry) {
			for (const compaction of compactionsBeforeEntry) {
				if (insertedCompactions.has(compaction.id)) continue;
				insertedCompactions.add(compaction.id);
				messages.push({
					message: buildCompactionHistoryMessage(compaction),
					piEntryId: compaction.id,
				});
			}
		}

		if (isCompactionEntry(entry)) {
			if (!insertedCompactions.has(entry.id)) {
				insertedCompactions.add(entry.id);
				messages.push({
					message: buildCompactionHistoryMessage(entry),
					piEntryId: entry.id,
				});
			}
			continue;
		}

		if (!isVisibleMessageEntry(entry)) continue;

		const role = entry.message.role;
		const rawContent = extractMessageParts(
			(entry.message as { content?: unknown }).content,
			role,
		);
		const content =
			role === "user" ? stripAstroPromptWrapperFromUserParts(rawContent) : rawContent;
		if (!content.some((part) => part.text.trim())) continue;

		const timestamp =
			toIsoString(entry.timestamp) ??
			toIsoString((entry.message as { timestamp?: unknown }).timestamp) ??
			new Date(0).toISOString();
		const id = role === "user" ? `u_${(userCount += 1)}` : `a_${(assistantCount += 1)}`;

		messages.push({
			message: {
				id,
				role,
				createdAt: timestamp,
				completedAt: role === "assistant" ? timestamp : undefined,
				content,
				...(role === "user" && options?.agentUserProvenance
					? { provenance: cloneUserMessageProvenance(options.agentUserProvenance) }
					: {}),
			},
			piEntryId: entry.id,
		});
	}

	return messages;
}

export function rebuildConversationHistoryFromPiJsonl(input: {
	piSessionJsonl: string;
	session: HistorySessionEnvelope;
	metadata?: unknown;
}): ConversationHistorySnapshot {
	const repaired = repairPiSessionJsonlCurrentBranch(input.piSessionJsonl);
	const entries = parsePiJsonl(repaired.piSessionJsonl);
	migrateSessionEntries(entries);

	const currentBranch = getCurrentBranch(entries);
	validateCurrentBranchToolCallConsistency(currentBranch);
	const a2aEnvelope = resolveA2AEnvelopeFromMetadata(input.metadata, input.session);
	const messages = applyHistoryAnnotations(
		projectMessages(currentBranch, {
			agentUserProvenance: a2aEnvelopeToUserProvenance(a2aEnvelope),
		}),
		input.metadata,
		{
		allowOrdinalFallback: !currentBranch.some(isCompactionEntry),
		},
	);
	const lastMessage = messages[messages.length - 1] ?? null;
	const updatedAt =
		input.session.updatedAt ??
		lastMessage?.completedAt ??
		lastMessage?.createdAt ??
		input.session.startedAt;

	return {
		version: 1,
		session: {
			...input.session,
			updatedAt,
			...(a2aEnvelope ? { a2a: cloneA2AEnvelope(a2aEnvelope) } : {}),
		},
		messages,
		inProgressMessage: null,
	};
}

export function validatePiSessionJsonlCurrentBranch(piSessionJsonl: string): void {
	const entries = parsePiJsonl(piSessionJsonl);
	migrateSessionEntries(entries);
	validateCurrentBranchToolCallConsistency(getCurrentBranch(entries));
}

export function repairPiSessionJsonlCurrentBranch(piSessionJsonl: string): {
	piSessionJsonl: string;
	repairs: PiSessionJsonlRepair[];
	changed: boolean;
} {
	const entries = parsePiJsonl(piSessionJsonl);
	migrateSessionEntries(entries);
	const repairs = [
		...repairCurrentBranchMissingParent(entries),
		...repairCurrentBranchOrphanedToolResults(entries),
	];
	if (repairs.length === 0) {
		return {
			piSessionJsonl,
			repairs,
			changed: false,
		};
	}
	return {
		piSessionJsonl: stringifyPiSessionEntries(entries),
		repairs,
		changed: true,
	};
}
