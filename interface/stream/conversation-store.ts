import {
	appendFileSync,
	existsSync,
	mkdirSync,
	readFileSync,
	renameSync,
	writeFileSync,
} from "node:fs";
import path from "node:path";
import type { StreamChunk } from "./protocol.js";

export type ConversationMessagePart = {
	type: "text";
	text: string;
};

export type ConversationMessage = {
	id: string;
	role: "user" | "assistant";
	createdAt: string;
	completedAt?: string;
	content: ConversationMessagePart[];
};

export type ConversationHistorySnapshot = {
	version: 1;
	session: {
		sessionId: string;
		threadId: string;
		agentName: string;
		agentId: number | null;
		agentSessionId: number | null;
		status: "running" | "completed" | "error";
		startedAt: string | null;
		updatedAt: string | null;
		error: string | null;
	};
	messages: ConversationMessage[];
	inProgressMessage: ConversationMessage | null;
};

type ConversationEvent =
	| {
			type: "user_message_added";
			at: string;
			message: ConversationMessage;
	  }
	| {
			type: "stream_chunk_recorded";
			at: string;
			chunk: StreamChunk;
	  }
	| {
			type: "stream_done";
			at: string;
	  };

type ConversationStoreMetadata = {
	sessionDir: string;
	sessionKey: string;
	threadId: string;
	agentName: string;
	agentId: number | null;
	agentSessionId: number | null;
	startedAt: string | null;
};

export type ConversationStore = {
	recordUserMessageSync(input: { text: string; createdAt?: string }): ConversationHistorySnapshot;
	recordStreamChunkSync(chunk: StreamChunk): ConversationHistorySnapshot;
	recordStreamDoneSync(): ConversationHistorySnapshot;
	getSnapshot(): ConversationHistorySnapshot;
};

function getConversationEventLogPath(sessionDir: string, sessionKey: string): string {
	return path.join(sessionDir, `${sessionKey}.conversation.jsonl`);
}

function getConversationHistoryPath(sessionDir: string, sessionKey: string): string {
	return path.join(sessionDir, `${sessionKey}.history.json`);
}

function cloneMessage(message: ConversationMessage): ConversationMessage {
	return {
		...message,
		content: message.content.map((part) => ({ ...part })),
	};
}

function cloneSnapshot(snapshot: ConversationHistorySnapshot): ConversationHistorySnapshot {
	return {
		version: snapshot.version,
		session: { ...snapshot.session },
		messages: snapshot.messages.map(cloneMessage),
		inProgressMessage: snapshot.inProgressMessage ? cloneMessage(snapshot.inProgressMessage) : null,
	};
}

function nextMessageId(snapshot: ConversationHistorySnapshot, role: "user" | "assistant"): string {
	const prefix = role === "user" ? "u" : "a";
	let count = 0;
	for (const message of snapshot.messages) {
		if (message.role === role) count += 1;
	}
	if (snapshot.inProgressMessage?.role === role) {
		count += 1;
	}
	return `${prefix}_${count + 1}`;
}

function createDefaultSnapshot(metadata: ConversationStoreMetadata): ConversationHistorySnapshot {
	return {
		version: 1,
		session: {
			sessionId: metadata.sessionKey,
			threadId: metadata.threadId,
			agentName: metadata.agentName,
			agentId: metadata.agentId,
			agentSessionId: metadata.agentSessionId,
			status: "running",
			startedAt: metadata.startedAt,
			updatedAt: metadata.startedAt,
			error: null,
		},
		messages: [],
		inProgressMessage: null,
	};
}

function syncSnapshotMetadata(
	snapshot: ConversationHistorySnapshot,
	metadata: ConversationStoreMetadata,
): ConversationHistorySnapshot {
	const next = cloneSnapshot(snapshot);
	next.session.sessionId = metadata.sessionKey;
	next.session.threadId = metadata.threadId;
	next.session.agentName = metadata.agentName;
	next.session.agentId = metadata.agentId;
	next.session.agentSessionId = metadata.agentSessionId;
	if (!next.session.startedAt) {
		next.session.startedAt = metadata.startedAt;
	}
	return next;
}

function ensureAssistantMessage(
	snapshot: ConversationHistorySnapshot,
	at: string,
): ConversationHistorySnapshot {
	if (snapshot.inProgressMessage) return snapshot;

	const next = cloneSnapshot(snapshot);
	next.inProgressMessage = {
		id: nextMessageId(next, "assistant"),
		role: "assistant",
		createdAt: at,
		content: [{ type: "text", text: "" }],
	};
	return next;
}

function finalizeInProgressMessage(
	snapshot: ConversationHistorySnapshot,
	at: string,
): ConversationHistorySnapshot {
	if (!snapshot.inProgressMessage) return snapshot;

	const next = cloneSnapshot(snapshot);
	const finalized = cloneMessage(next.inProgressMessage);
	finalized.completedAt = finalized.completedAt ?? at;
	next.messages.push(finalized);
	next.inProgressMessage = null;
	return next;
}

function applyConversationEvent(
	snapshot: ConversationHistorySnapshot,
	event: ConversationEvent,
): ConversationHistorySnapshot {
	let next = cloneSnapshot(snapshot);
	next.session.updatedAt = event.at;

	switch (event.type) {
		case "user_message_added": {
			if (next.inProgressMessage) {
				next = finalizeInProgressMessage(next, event.at);
			}
			next.messages.push(cloneMessage(event.message));
			next.session.status = "running";
			next.session.error = null;
			return next;
		}

		case "stream_done":
			return next;

		case "stream_chunk_recorded": {
			const chunk = event.chunk;
			switch (chunk.type) {
				case "new_session":
					next.session.agentSessionId = chunk.new_session.agent_session_id;
					next.session.agentId = chunk.agent_id;
					next.session.threadId = chunk.new_session.thread_id;
					return next;
				case "session_switch":
					return next;
				case "start":
				case "reasoning-start":
				case "reasoning-delta":
				case "reasoning-end":
				case "tool-call-start":
				case "tool-call-delta":
				case "tool-call-end":
				case "tool-result":
					return next;
				case "text-start":
					return ensureAssistantMessage(next, event.at);
				case "text-delta": {
					next = ensureAssistantMessage(next, event.at);
					if (!next.inProgressMessage) return next;
					const firstPart = next.inProgressMessage.content[0];
					firstPart.text += chunk.textDelta;
					return next;
				}
				case "text-end":
					return next;
				case "finish":
					next = finalizeInProgressMessage(next, event.at);
					next.session.status = "completed";
					next.session.error = null;
					return next;
				case "error":
					next.session.status = "error";
					next.session.error = chunk.error;
					return next;
				default:
					return next;
			}
		}
	}
}

function appendConversationEventSync(
	eventLogPath: string,
	historyPath: string,
	snapshot: ConversationHistorySnapshot,
	event: ConversationEvent,
): ConversationHistorySnapshot {
	appendFileSync(eventLogPath, `${JSON.stringify(event)}\n`);
	const nextSnapshot = applyConversationEvent(snapshot, event);
	const tempPath = `${historyPath}.${process.pid}.${Date.now()}.tmp`;
	writeFileSync(tempPath, JSON.stringify(nextSnapshot, null, 2));
	renameSync(tempPath, historyPath);
	return nextSnapshot;
}

export function readConversationHistorySync(input: {
	sessionDir: string;
	sessionKey: string;
}): ConversationHistorySnapshot | null {
	const historyPath = getConversationHistoryPath(input.sessionDir, input.sessionKey);
	if (!existsSync(historyPath)) return null;

	try {
		const parsed = JSON.parse(readFileSync(historyPath, "utf8"));
		if (!parsed || typeof parsed !== "object") return null;
		return parsed as ConversationHistorySnapshot;
	} catch {
		return null;
	}
}

export function createConversationStore(metadata: ConversationStoreMetadata): ConversationStore {
	mkdirSync(metadata.sessionDir, { recursive: true });

	const eventLogPath = getConversationEventLogPath(metadata.sessionDir, metadata.sessionKey);
	const historyPath = getConversationHistoryPath(metadata.sessionDir, metadata.sessionKey);

	let snapshot =
		readConversationHistorySync({
			sessionDir: metadata.sessionDir,
			sessionKey: metadata.sessionKey,
		}) ?? createDefaultSnapshot(metadata);

	snapshot = syncSnapshotMetadata(snapshot, metadata);
	const initTempPath = `${historyPath}.${process.pid}.${Date.now()}.init.tmp`;
	writeFileSync(initTempPath, JSON.stringify(snapshot, null, 2));
	renameSync(initTempPath, historyPath);

	return {
		recordUserMessageSync(input) {
			const at = input.createdAt ?? new Date().toISOString();
			const nextMessage: ConversationMessage = {
				id: nextMessageId(snapshot, "user"),
				role: "user",
				createdAt: at,
				content: [{ type: "text", text: input.text }],
			};
			snapshot = appendConversationEventSync(eventLogPath, historyPath, snapshot, {
				type: "user_message_added",
				at,
				message: nextMessage,
			});
			return snapshot;
		},
		recordStreamChunkSync(chunk) {
			snapshot = appendConversationEventSync(eventLogPath, historyPath, snapshot, {
				type: "stream_chunk_recorded",
				at: new Date().toISOString(),
				chunk,
			});
			return snapshot;
		},
		recordStreamDoneSync() {
			snapshot = appendConversationEventSync(eventLogPath, historyPath, snapshot, {
				type: "stream_done",
				at: new Date().toISOString(),
			});
			return snapshot;
		},
		getSnapshot() {
			return cloneSnapshot(snapshot);
		},
	};
}
