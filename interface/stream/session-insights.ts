import { existsSync } from "node:fs";
import {
	calculateContextTokens,
	estimateContextTokens,
} from "../../node_modules/@mariozechner/pi-coding-agent/dist/core/compaction/compaction.js";
import {
	SessionManager,
	getLatestCompactionEntry,
	type SessionEntry,
	type SessionMessageEntry,
} from "../../node_modules/@mariozechner/pi-coding-agent/dist/core/session-manager.js";
import { readConversationHistorySync, type ConversationHistorySnapshot } from "./conversation-store.js";
import type { SessionModelBinding } from "./session-model.js";
import { buildSessionInsightsInfo, type SessionInsightsInfoNode } from "./session-insights-info.js";
import {
	buildSessionInsightsEditable,
	resolveEffectiveCompactionConfig,
	resolveSessionRuntimeLimits,
	type SessionConfigOverrides,
	type SessionInsightsConfig,
	type SessionInsightsEditable,
} from "./session-config.js";

type SessionMetadataLike = {
	threadId: string | null;
	agentType: string | null;
	agentUid?: string | null;
	agentSessionUid?: string | null;
	agentId?: string | null;
	agentSessionId?: string | null;
	startedAt: string | null;
	cwd: string | null;
	sessionModelBinding: SessionModelBinding | null;
	sessionConfigOverrides: SessionConfigOverrides | null;
};

type SessionModelIdentity = {
	provider: string | null;
	model: string | null;
};

export type SessionUsageResponse = {
	version: 1;
	session: {
		sessionUid: string;
		threadId: string | null;
		agentType: string | null;
		agentUid: string | null;
		agentSessionUid: string | null;
		status: "running" | "completed" | "error";
		startedAt: string | null;
		updatedAt: string | null;
		lastError: string | null;
	};
	model: {
		provider: string | null;
		model: string | null;
		reasoningEffort: string | null;
	};
	usage: {
		userMessages: number;
		assistantMessages: number;
		assistantTurns: number;
		toolCalls: number;
		toolResults: number;
		totalMessages: number;
		tokens: {
			input: number;
			output: number;
			cacheRead: number;
			cacheWrite: number;
			total: number;
		};
		estimatedCostUsd: number | null;
	};
	lastTurn: {
		completedAt: string | null;
		finishReason: string | null;
		errorMessage: string | null;
		model: SessionModelIdentity;
		tokens: {
			input: number;
			output: number;
			cacheRead: number;
			cacheWrite: number;
			total: number;
		};
	} | null;
};

export type SessionContextResponse = {
	version: 1;
	session: SessionUsageResponse["session"];
	model: {
		provider: string | null;
		model: string | null;
		reasoningEffort: string | null;
		contextWindow: number;
		maxOutputTokens: number;
	};
	context: {
		status: "known" | "unknown_after_compaction";
		source: "provider_usage" | "provider_usage_plus_estimate" | "estimated_messages";
		tokens: number | null;
		contextWindow: number;
		percentOfContextWindow: number | null;
		compactionEnabled: boolean;
		compactionReserveTokens: number;
		compactionThresholdTokens: number | null;
		tokensRemainingBeforeCompaction: number | null;
		tokensRemainingBeforeContextLimit: number | null;
		latestCompaction: {
			at: string;
			tokensBefore: number;
		} | null;
	};
};

export type SessionInsights = {
	usage: SessionUsageResponse;
	context: SessionContextResponse;
	config: SessionInsightsConfig;
	editable: SessionInsightsEditable;
	info: Record<string, SessionInsightsInfoNode>;
};

export type SessionInsightsResponse = {
	version: 1;
	session: SessionUsageResponse["session"];
	model: SessionContextResponse["model"];
	usage: SessionUsageResponse["usage"];
	context: SessionContextResponse["context"];
	lastTurn: SessionUsageResponse["lastTurn"];
	config: SessionInsightsConfig;
	editable: SessionInsightsEditable;
	info: Record<string, SessionInsightsInfoNode>;
};

type AssistantUsage = {
	input: number;
	output: number;
	cacheRead: number;
	cacheWrite: number;
	totalTokens: number;
	costTotal: number | null;
};

type AssistantTurnSummary = {
	completedAt: string | null;
	finishReason: string | null;
	errorMessage: string | null;
	model: SessionModelIdentity;
	usage: AssistantUsage;
};

function normalizeFiniteNumber(value: unknown): number | null {
	return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function getAssistantUsage(message: any): AssistantUsage {
	const usage = message?.usage ?? {};
	const input = normalizeFiniteNumber(usage.input) ?? 0;
	const output = normalizeFiniteNumber(usage.output) ?? 0;
	const cacheRead = normalizeFiniteNumber(usage.cacheRead) ?? 0;
	const cacheWrite = normalizeFiniteNumber(usage.cacheWrite) ?? 0;
	const totalTokens = normalizeFiniteNumber(usage.totalTokens) ?? input + output + cacheRead + cacheWrite;
	const costObject = usage.cost;
	const costTotal =
		normalizeFiniteNumber(costObject?.total) ?? normalizeFiniteNumber(costObject) ?? null;
	return {
		input,
		output,
		cacheRead,
		cacheWrite,
		totalTokens,
		costTotal,
	};
}

function isAssistantMessageEntry(entry: SessionEntry): entry is SessionMessageEntry {
	return entry.type === "message" && entry.message?.role === "assistant";
}

function buildSessionSummary(
	sessionKey: string,
	metadata: SessionMetadataLike,
	history: ConversationHistorySnapshot | null,
) {
	const metadataAgentUid = metadata.agentUid ?? metadata.agentId ?? null;
	const metadataAgentSessionUid = metadata.agentSessionUid ?? metadata.agentSessionId ?? null;
	return {
		sessionUid: sessionKey,
		threadId: history?.session.threadId ?? metadata.threadId,
		agentType: metadata.agentType ?? history?.session.agentType ?? null,
		agentUid: metadataAgentUid ?? history?.session.agentUid ?? null,
		agentSessionUid: metadataAgentSessionUid ?? history?.session.agentSessionUid ?? null,
		status: history?.session.status ?? "running",
		startedAt: history?.session.startedAt ?? metadata.startedAt,
		updatedAt: history?.session.updatedAt ?? metadata.startedAt,
		lastError: history?.session.error ?? null,
	};
}

function resolveActiveModel(
	sessionManager: SessionManager,
	sessionModelBinding: SessionModelBinding | null,
): SessionModelIdentity {
	const sessionContext = sessionManager.buildSessionContext();
	const contextModel = sessionContext.model;
	if (contextModel?.provider && contextModel?.modelId) {
		return {
			provider: contextModel.provider,
			model: contextModel.modelId,
		};
	}
	if (sessionModelBinding) {
		return {
			provider: sessionModelBinding.provider,
			model: sessionModelBinding.model,
		};
	}
	return {
		provider: null,
		model: null,
	};
}

function buildSessionInsightsConfig(input: {
	metadata: SessionMetadataLike;
	sessionManager: SessionManager;
}): SessionInsightsConfig {
	const { metadata, sessionManager } = input;
	const activeModel = resolveActiveModel(sessionManager, metadata.sessionModelBinding);
	const runtimeLimits = resolveSessionRuntimeLimits(metadata.sessionModelBinding);
	const compactionConfig = resolveEffectiveCompactionConfig({
		cwd: metadata.cwd,
		sessionConfigOverrides: metadata.sessionConfigOverrides,
		contextWindow: runtimeLimits.contextWindow,
	});

	return {
		compaction: compactionConfig,
		model: {
			provider: activeModel.provider,
			model: activeModel.model,
			reasoningEffort: metadata.sessionModelBinding?.runConfig.reasoning_effort ?? null,
			contextWindow: runtimeLimits.contextWindow,
			maxOutputTokens: runtimeLimits.maxOutputTokens,
		},
	};
}

function findLastAssistantTurn(branchEntries: SessionEntry[]): AssistantTurnSummary | null {
	for (let index = branchEntries.length - 1; index >= 0; index -= 1) {
		const entry = branchEntries[index];
		if (!isAssistantMessageEntry(entry)) continue;

		return {
			completedAt: entry.timestamp ?? null,
			finishReason: typeof entry.message.stopReason === "string" ? entry.message.stopReason : null,
			errorMessage: typeof entry.message.errorMessage === "string" ? entry.message.errorMessage : null,
			model: {
				provider: typeof entry.message.provider === "string" ? entry.message.provider : null,
				model: typeof entry.message.model === "string" ? entry.message.model : null,
			},
			usage: getAssistantUsage(entry.message),
		};
	}
	return null;
}

function hasSuccessfulAssistantUsageAfterCompaction(branchEntries: SessionEntry[], latestCompactionIndex: number): boolean {
	for (let index = branchEntries.length - 1; index > latestCompactionIndex; index -= 1) {
		const entry = branchEntries[index];
		if (!isAssistantMessageEntry(entry)) continue;
		const stopReason = entry.message.stopReason;
		if (stopReason === "aborted" || stopReason === "error") continue;
		return calculateContextTokens(entry.message.usage) > 0;
	}
	return false;
}

function buildUsageResponse(input: {
	sessionKey: string;
	metadata: SessionMetadataLike;
	history: ConversationHistorySnapshot | null;
	sessionManager: SessionManager;
	branchEntries: SessionEntry[];
}): SessionUsageResponse {
	const { sessionKey, metadata, history, sessionManager, branchEntries } = input;
	const sessionContext = sessionManager.buildSessionContext();
	const messages = sessionContext.messages;
	const activeModel = resolveActiveModel(sessionManager, metadata.sessionModelBinding);

	let userMessages = 0;
	let assistantMessages = 0;
	let toolResults = 0;
	let toolCalls = 0;
	let totalInput = 0;
	let totalOutput = 0;
	let totalCacheRead = 0;
	let totalCacheWrite = 0;
	let totalCost = 0;
	let hasCost = false;

	for (const message of messages as any[]) {
		if (message.role === "user") {
			userMessages += 1;
			continue;
		}
		if (message.role === "assistant") {
			assistantMessages += 1;
			const usage = getAssistantUsage(message);
			totalInput += usage.input;
			totalOutput += usage.output;
			totalCacheRead += usage.cacheRead;
			totalCacheWrite += usage.cacheWrite;
			if (usage.costTotal != null) {
				totalCost += usage.costTotal;
				hasCost = true;
			}
			if (Array.isArray(message.content)) {
				toolCalls += message.content.filter((part: any) => part?.type === "toolCall").length;
			}
			continue;
		}
		if (message.role === "toolResult") {
			toolResults += 1;
		}
	}

	const lastTurn = findLastAssistantTurn(branchEntries);

	return {
		version: 1,
		session: buildSessionSummary(sessionKey, metadata, history),
		model: {
			provider: activeModel.provider,
			model: activeModel.model,
			reasoningEffort: metadata.sessionModelBinding?.runConfig.reasoning_effort ?? null,
		},
		usage: {
			userMessages,
			assistantMessages,
			assistantTurns: assistantMessages,
			toolCalls,
			toolResults,
			totalMessages: messages.length,
			tokens: {
				input: totalInput,
				output: totalOutput,
				cacheRead: totalCacheRead,
				cacheWrite: totalCacheWrite,
				total: totalInput + totalOutput + totalCacheRead + totalCacheWrite,
			},
			estimatedCostUsd: hasCost ? totalCost : null,
		},
		lastTurn: lastTurn
			? {
					completedAt: lastTurn.completedAt,
					finishReason: lastTurn.finishReason,
					errorMessage: lastTurn.errorMessage,
					model: lastTurn.model,
					tokens: {
						input: lastTurn.usage.input,
						output: lastTurn.usage.output,
						cacheRead: lastTurn.usage.cacheRead,
						cacheWrite: lastTurn.usage.cacheWrite,
						total: lastTurn.usage.totalTokens,
					},
			  }
			: null,
	};
}

function buildContextResponse(input: {
	sessionKey: string;
	metadata: SessionMetadataLike;
	history: ConversationHistorySnapshot | null;
	sessionManager: SessionManager;
	branchEntries: SessionEntry[];
	config: SessionInsightsConfig;
}): SessionContextResponse {
	const { sessionKey, metadata, history, sessionManager, branchEntries, config } = input;
	const sessionContext = sessionManager.buildSessionContext();
	const latestCompaction = getLatestCompactionEntry(branchEntries);
	const latestCompactionIndex = latestCompaction
		? branchEntries.findIndex((entry) => entry.id === latestCompaction.id)
		: -1;
	const hasPostCompactionUsage =
		latestCompactionIndex >= 0
			? hasSuccessfulAssistantUsageAfterCompaction(branchEntries, latestCompactionIndex)
			: true;
	const compactionThresholdTokens = config.compaction.thresholdTokens;

	if (latestCompaction && !hasPostCompactionUsage) {
		return {
			version: 1,
			session: buildSessionSummary(sessionKey, metadata, history),
			model: config.model,
			context: {
				status: "unknown_after_compaction",
				source: "provider_usage",
				tokens: null,
				contextWindow: config.model.contextWindow,
				percentOfContextWindow: null,
				compactionEnabled: config.compaction.enabled,
				compactionReserveTokens: config.compaction.reserveTokens,
				compactionThresholdTokens,
				tokensRemainingBeforeCompaction: null,
				tokensRemainingBeforeContextLimit: null,
				latestCompaction: {
					at: latestCompaction.timestamp,
					tokensBefore: latestCompaction.tokensBefore,
				},
			},
		};
	}

	const estimate = estimateContextTokens(sessionContext.messages as any[]);
	const tokens = estimate.tokens;
	const percentOfContextWindow =
		config.model.contextWindow > 0
			? Number(((tokens / config.model.contextWindow) * 100).toFixed(1))
			: null;
	const tokensRemainingBeforeContextLimit = Math.max(config.model.contextWindow - tokens, 0);
	const tokensRemainingBeforeCompaction =
		compactionThresholdTokens == null ? null : Math.max(compactionThresholdTokens - tokens, 0);
	const source =
		estimate.lastUsageIndex == null
			? "estimated_messages"
			: estimate.trailingTokens > 0
				? "provider_usage_plus_estimate"
				: "provider_usage";

	return {
		version: 1,
		session: buildSessionSummary(sessionKey, metadata, history),
		model: config.model,
		context: {
			status: "known",
			source,
			tokens,
			contextWindow: config.model.contextWindow,
			percentOfContextWindow,
			compactionEnabled: config.compaction.enabled,
			compactionReserveTokens: config.compaction.reserveTokens,
			compactionThresholdTokens,
			tokensRemainingBeforeCompaction,
			tokensRemainingBeforeContextLimit,
			latestCompaction: latestCompaction
				? {
						at: latestCompaction.timestamp,
						tokensBefore: latestCompaction.tokensBefore,
				  }
				: null,
		},
	};
}

export function readSessionInsights(options: {
	sessionDir: string;
	sessionKey: string;
	metadata: SessionMetadataLike;
}): SessionInsights | null {
	const sessionPath = `${options.sessionDir}/${options.sessionKey}.jsonl`;
	if (!existsSync(sessionPath)) return null;

	const sessionManager = SessionManager.open(sessionPath, options.sessionDir);
	const branchEntries = sessionManager.getBranch();
	const history =
		readConversationHistorySync({
			sessionDir: options.sessionDir,
			sessionKey: options.sessionKey,
		}) ?? null;
	const config = buildSessionInsightsConfig({
		metadata: options.metadata,
		sessionManager,
	});
	const editable = buildSessionInsightsEditable({
		sessionModelBinding: options.metadata.sessionModelBinding,
		contextWindow: config.model.contextWindow,
		maxOutputTokens: config.model.maxOutputTokens,
	});
	const info = buildSessionInsightsInfo();

	return {
		usage: buildUsageResponse({
			sessionKey: options.sessionKey,
			metadata: options.metadata,
			history,
			sessionManager,
			branchEntries,
		}),
		config,
		editable,
		info,
		context: buildContextResponse({
			sessionKey: options.sessionKey,
			metadata: options.metadata,
			history,
			sessionManager,
			branchEntries,
			config,
		}),
	};
}

export function buildSessionInsightsResponse(insights: SessionInsights): SessionInsightsResponse {
	return {
		version: 1,
		session: insights.usage.session,
		model: insights.context.model,
		usage: insights.usage.usage,
		context: insights.context.context,
		lastTurn: insights.usage.lastTurn,
		config: insights.config,
		editable: insights.editable,
		info: insights.info,
	};
}
