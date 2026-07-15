export type SessionInsightsInfoSource = {
	kind: "astro_doc" | "pi_doc";
	package?: "astro" | "pi-coding-agent" | "pi-ai";
	path: string;
	section?: string;
};

export type SessionInsightsInfoNode = {
	label: string;
	description: string;
	source?: SessionInsightsInfoSource[];
	children?: Record<string, SessionInsightsInfoNode>;
};

const ASTRO_SESSION_SOURCE: SessionInsightsInfoSource = {
	kind: "astro_doc",
	package: "astro",
	path: "docs/interface/session-insights.md",
};

const PI_COMPACTION_SOURCE: SessionInsightsInfoSource = {
	kind: "pi_doc",
	package: "pi-coding-agent",
	path: "README.md",
	section: "Compaction",
};

const PI_CONTEXT_USAGE_SOURCE: SessionInsightsInfoSource = {
	kind: "pi_doc",
	package: "pi-coding-agent",
	path: "CHANGELOG.md",
	section: "ContextUsage after compaction",
};

const PI_FOOTER_SOURCE: SessionInsightsInfoSource = {
	kind: "pi_doc",
	package: "pi-coding-agent",
	path: "README.md",
	section: "Footer",
};

const PI_REASONING_SOURCE: SessionInsightsInfoSource = {
	kind: "pi_doc",
	package: "pi-ai",
	path: "README.md",
	section: "Thinking/Reasoning",
};

const PI_REASONING_LEVELS_SOURCE: SessionInsightsInfoSource = {
	kind: "pi_doc",
	package: "pi-coding-agent",
	path: "dist/modes/interactive/components/thinking-selector.js",
	section: "LEVEL_DESCRIPTIONS",
};

function leaf(
	label: string,
	description: string,
	source: SessionInsightsInfoSource[] = [ASTRO_SESSION_SOURCE],
): SessionInsightsInfoNode {
	return {
		label,
		description,
		source,
	};
}

function group(
	label: string,
	description: string,
	children: Record<string, SessionInsightsInfoNode>,
	source: SessionInsightsInfoSource[] = [ASTRO_SESSION_SOURCE],
): SessionInsightsInfoNode {
	return {
		label,
		description,
		source,
		children,
	};
}

export function buildSessionInsightsInfo(): Record<string, SessionInsightsInfoNode> {
	return {
		session: group(
			"Session",
			"Astro's local runtime-session identity and lifecycle summary.",
			{
				sessionUid: leaf("Session UID", "The backend session uid Astro uses as the runtime session identity."),
				threadId: leaf("Thread ID", "The frontend thread identifier currently bound to this runtime session."),
				agentType: leaf("Agent Type", "The backend Agent.agent_type classification for this session, such as astro-orchestrator or project-executor."),
				agentUid: leaf("Agent UID", "The backend Main Sequence agent uid resolved for this session."),
				agentSessionUid: leaf("Agent Session UID", "The backend Main Sequence agent-session uid associated with this runtime session."),
				status: leaf("Status", "Astro's current view of the session state: running, completed, or error."),
				startedAt: leaf("Started At", "When this runtime session started."),
				updatedAt: leaf("Updated At", "When Astro last updated the stored session snapshot."),
				lastError: leaf("Last Error", "The latest recorded session-level error, if one was captured."),
			},
		),
		model: group(
			"Model",
			"The effective model identity and model limits currently governing this session.",
			{
				provider: leaf("Provider", "The model provider currently used for this session, such as openai, github-copilot, or ollama.", [
					ASTRO_SESSION_SOURCE,
					PI_FOOTER_SOURCE,
				]),
				model: leaf("Model ID", "The effective model id currently used for this session.", [
					ASTRO_SESSION_SOURCE,
					PI_FOOTER_SOURCE,
				]),
				reasoningEffort: leaf("Reasoning Effort", "The simplified reasoning/thinking level currently selected for the model. Pi treats this as the high-level reasoning option when the model supports it.", [
					PI_REASONING_SOURCE,
					PI_REASONING_LEVELS_SOURCE,
				]),
				contextWindow: leaf("Context Window", "The model's total context capacity in tokens.", [
					PI_REASONING_SOURCE,
				]),
				maxOutputTokens: leaf("Max Output Tokens", "The maximum output-token budget Astro currently associates with this model.", [
					ASTRO_SESSION_SOURCE,
				]),
			},
		),
		usage: group(
			"Usage",
			"Cumulative message and token usage across the current runtime session branch.",
			{
				userMessages: leaf("User Messages", "The number of user messages currently represented in the active session context.", [
					PI_FOOTER_SOURCE,
				]),
				assistantMessages: leaf("Assistant Messages", "The number of assistant messages currently represented in the active session context.", [
					PI_FOOTER_SOURCE,
				]),
				assistantTurns: leaf("Assistant Turns", "The number of assistant turns counted for this session snapshot.", [
					PI_FOOTER_SOURCE,
				]),
				toolCalls: leaf("Tool Calls", "The number of tool-call content blocks emitted by assistant messages in this session snapshot.", [
					PI_FOOTER_SOURCE,
				]),
				toolResults: leaf("Tool Results", "The number of tool-result messages represented in the current session context.", [
					PI_FOOTER_SOURCE,
				]),
				totalMessages: leaf("Total Messages", "The total number of messages in the active session context that Astro used for this snapshot.", [
					PI_FOOTER_SOURCE,
				]),
				tokens: group(
					"Token Usage",
					"Cumulative token usage totals derived from assistant message usage objects.",
					{
						input: leaf("Input Tokens", "Total input tokens consumed across assistant turns in this session snapshot.", [
							PI_FOOTER_SOURCE,
						]),
						output: leaf("Output Tokens", "Total output tokens produced across assistant turns in this session snapshot.", [
							PI_FOOTER_SOURCE,
						]),
						cacheRead: leaf("Cache Read Tokens", "Total tokens read from provider-side prompt caches when the provider reported them.", [
							PI_FOOTER_SOURCE,
						]),
						cacheWrite: leaf("Cache Write Tokens", "Total tokens written into provider-side prompt caches when the provider reported them.", [
							PI_FOOTER_SOURCE,
						]),
						total: leaf("Total Tokens", "The sum of input, output, cache-read, and cache-write tokens for this session snapshot.", [
							PI_FOOTER_SOURCE,
						]),
					},
					[PI_FOOTER_SOURCE],
				),
				estimatedCostUsd: leaf("Estimated Cost (USD)", "The cumulative estimated cost when the provider supplied cost data for assistant turns.", [
					PI_FOOTER_SOURCE,
				]),
			},
			[PI_FOOTER_SOURCE],
		),
		context: group(
			"Context",
			"The current estimated context occupancy and compaction headroom for the active session branch.",
			{
				status: leaf("Context Status", "Whether Astro trusts the current context estimate. Pi reports context usage as unknown after compaction until the next LLM response provides fresh usage.", [
					PI_CONTEXT_USAGE_SOURCE,
				]),
				source: leaf("Context Source", "How Astro derived the current context estimate: directly from provider usage, provider usage plus estimation, or message-only estimation.", [
					ASTRO_SESSION_SOURCE,
				]),
				tokens: leaf("Context Tokens", "The current estimated number of tokens occupying the model context. Pi treats this value as unknown immediately after compaction until the next LLM response.", [
					PI_CONTEXT_USAGE_SOURCE,
				]),
				contextWindow: leaf("Context Window", "The model's total context capacity in tokens.", [
					PI_REASONING_SOURCE,
				]),
				percentOfContextWindow: leaf("Context Usage Percent", "The estimated context occupancy as a percentage of the model context window.", [
					PI_CONTEXT_USAGE_SOURCE,
				]),
				compactionEnabled: leaf("Compaction Enabled", "Whether Pi's automatic compaction is enabled for this session. Pi enables automatic compaction by default.", [
					PI_COMPACTION_SOURCE,
				]),
				compactionReserveTokens: leaf("Compaction Reserve Tokens", "The token headroom Pi keeps in reserve before the context limit. The compaction threshold is computed as contextWindow minus reserveTokens.", [
					PI_COMPACTION_SOURCE,
				]),
				compactionThresholdTokens: leaf("Compaction Threshold Tokens", "The token count at which Astro expects automatic compaction to trigger, based on the current context window and reserve-tokens setting.", [
					PI_COMPACTION_SOURCE,
				]),
				tokensRemainingBeforeCompaction: leaf("Tokens Remaining Before Compaction", "How many estimated tokens remain before the current session reaches Pi's automatic compaction threshold.", [
					PI_COMPACTION_SOURCE,
				]),
				tokensRemainingBeforeContextLimit: leaf("Tokens Remaining Before Context Limit", "How many estimated tokens remain before the model's hard context limit is reached.", [
					PI_REASONING_SOURCE,
				]),
				latestCompaction: group(
					"Latest Compaction",
					"The most recent compaction entry on the active session branch, if any.",
					{
						at: leaf("Compacted At", "When the latest compaction entry was written to the session history.", [
							PI_COMPACTION_SOURCE,
						]),
						tokensBefore: leaf("Tokens Before Compaction", "The token count Pi recorded just before the latest compaction was created.", [
							PI_COMPACTION_SOURCE,
						]),
					},
					[PI_COMPACTION_SOURCE],
				),
			},
			[PI_COMPACTION_SOURCE, PI_CONTEXT_USAGE_SOURCE],
		),
		config: group(
			"Config",
			"The effective session config Astro uses when building future turns for this session.",
			{
				compaction: group(
					"Compaction Config",
					"The effective automatic-compaction policy currently applied to this session.",
					{
						enabled: leaf("Compaction Enabled", "Whether automatic compaction is enabled for this session.", [
							PI_COMPACTION_SOURCE,
						]),
						reserveTokens: leaf("Reserve Tokens", "The amount of headroom Pi keeps before the context limit; compaction threshold is derived from this value.", [
							PI_COMPACTION_SOURCE,
						]),
						thresholdTokens: leaf("Threshold Tokens", "The derived token threshold where Astro expects automatic compaction to trigger for this session.", [
							PI_COMPACTION_SOURCE,
						]),
						thresholdPercent: leaf("Threshold Percent", "The derived automatic-compaction threshold expressed as a percent of the model context window.", [
							PI_COMPACTION_SOURCE,
						]),
					},
					[PI_COMPACTION_SOURCE],
				),
				model: group(
					"Model Config",
					"The effective model identity, reasoning setting, and token limits Astro uses for this session.",
					{
						provider: leaf("Provider", "The effective provider Astro is using for this session.", [
							ASTRO_SESSION_SOURCE,
							PI_FOOTER_SOURCE,
						]),
						model: leaf("Model ID", "The effective model id Astro is using for this session.", [
							ASTRO_SESSION_SOURCE,
							PI_FOOTER_SOURCE,
						]),
						reasoningEffort: leaf("Reasoning Effort", "The current simplified reasoning/thinking level applied to this session's model selection.", [
							PI_REASONING_SOURCE,
							PI_REASONING_LEVELS_SOURCE,
						]),
						contextWindow: leaf("Context Window", "The model's context capacity in tokens.", [
							PI_REASONING_SOURCE,
						]),
						maxOutputTokens: leaf("Max Output Tokens", "The output-token budget Astro currently associates with this session's selected model.", [
							ASTRO_SESSION_SOURCE,
						]),
					},
					[PI_REASONING_SOURCE],
				),
			},
		),
		editable: group(
			"Editable",
			"Field-level editability and UI constraints for the writable subset of session config.",
			{
				config: group(
					"Editable Config",
					"Editable metadata mirrored to the same config structure.",
					{
						compaction: group(
							"Editable Compaction Fields",
							"Edit rules for compaction-related session config.",
							{
								enabled: group(
									"Compaction Enabled Editability",
									"Editing contract for config.compaction.enabled.",
									{
										editable: leaf("Editable", "Whether the field may be changed through PATCH /api/chat/session-config."),
										type: leaf("Input Type", "The UI input type Astro expects for this field."),
									},
								),
								reserveTokens: group(
									"Reserve Tokens Editability",
									"Editing contract for config.compaction.reserveTokens.",
									{
										editable: leaf("Editable", "Whether the field may be changed through PATCH /api/chat/session-config."),
										type: leaf("Input Type", "The UI input type Astro expects for this field."),
										min: leaf("Minimum", "The minimum allowed integer value for this field."),
										max: leaf("Maximum", "The maximum allowed integer value for this field, derived from the current model context window."),
										step: leaf("Step", "The integer step Astro expects for this field."),
										unit: leaf("Unit", "The unit used for this field's numeric value."),
									},
								),
								thresholdTokens: group(
									"Threshold Tokens Editability",
									"Read-only editing metadata for the derived compaction threshold.",
									{
										editable: leaf("Editable", "Whether the field may be changed through PATCH /api/chat/session-config."),
										type: leaf("Input Type", "The UI input type Astro associates with this field."),
										unit: leaf("Unit", "The unit used for this field's numeric value."),
									},
								),
								thresholdPercent: group(
									"Threshold Percent Editability",
									"Read-only editing metadata for the derived compaction threshold percent.",
									{
										editable: leaf("Editable", "Whether the field may be changed through PATCH /api/chat/session-config."),
										type: leaf("Input Type", "The UI input type Astro associates with this field."),
										unit: leaf("Unit", "The unit used for this field's numeric value."),
									},
								),
							},
						),
						model: group(
							"Editable Model Fields",
							"Read-only editing metadata for the model-related session config fields.",
							{
								provider: group("Provider Editability", "Read-only editing metadata for config.model.provider.", {
									editable: leaf("Editable", "Whether the field may be changed through PATCH /api/chat/session-config."),
									type: leaf("Input Type", "The UI input type Astro associates with this field."),
								}),
								model: group("Model Editability", "Read-only editing metadata for config.model.model.", {
									editable: leaf("Editable", "Whether the field may be changed through PATCH /api/chat/session-config."),
									type: leaf("Input Type", "The UI input type Astro associates with this field."),
								}),
								reasoningEffort: group("Reasoning Effort Editability", "Read-only editing metadata for config.model.reasoningEffort.", {
									editable: leaf("Editable", "Whether the field may be changed through PATCH /api/chat/session-config."),
									type: leaf("Input Type", "The UI input type Astro associates with this field."),
									values: leaf("Allowed Values", "The reasoning values Astro currently associates with this field when they are known."),
								}),
								contextWindow: group("Context Window Editability", "Read-only editing metadata for config.model.contextWindow.", {
									editable: leaf("Editable", "Whether the field may be changed through PATCH /api/chat/session-config."),
									type: leaf("Input Type", "The UI input type Astro associates with this field."),
									unit: leaf("Unit", "The unit used for this field's numeric value."),
								}),
								maxOutputTokens: group("Max Output Tokens Editability", "Read-only editing metadata for config.model.maxOutputTokens.", {
									editable: leaf("Editable", "Whether the field may be changed through PATCH /api/chat/session-config."),
									type: leaf("Input Type", "The UI input type Astro associates with this field."),
									unit: leaf("Unit", "The unit used for this field's numeric value."),
								}),
							},
						),
					},
				),
			},
		),
		lastTurn: group(
			"Last Turn",
			"The latest assistant turn on the active session branch, when one exists.",
			{
				completedAt: leaf("Completed At", "When the latest assistant turn completed."),
				finishReason: leaf("Finish Reason", "The latest assistant stop reason reported by Pi or the provider."),
				errorMessage: leaf("Error Message", "The latest assistant error message, if the turn ended in an error."),
				model: group(
					"Last Turn Model",
					"The provider and model reported on the latest assistant turn.",
					{
						provider: leaf("Provider", "The provider reported on the latest assistant turn."),
						model: leaf("Model ID", "The model id reported on the latest assistant turn."),
					},
				),
				tokens: group(
					"Last Turn Tokens",
					"The token usage reported on the latest assistant turn.",
					{
						input: leaf("Input Tokens", "Input tokens reported for the latest assistant turn."),
						output: leaf("Output Tokens", "Output tokens reported for the latest assistant turn."),
						cacheRead: leaf("Cache Read Tokens", "Cache-read tokens reported for the latest assistant turn."),
						cacheWrite: leaf("Cache Write Tokens", "Cache-write tokens reported for the latest assistant turn."),
						total: leaf("Total Tokens", "The summed token usage for the latest assistant turn."),
					},
					[PI_FOOTER_SOURCE],
				),
			},
		),
	};
}
