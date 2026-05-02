import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import {
	A2A_DEV_PROJECT_ENV,
	type A2ADiscoveryCandidate,
	buildA2ADiscoveryPrompt,
	normalizeA2AResponseFormat,
	readA2ADevCandidate,
	resolveA2ADevBaseUrls,
	resolveCurrentAstroAgentName,
	selectBestA2ACandidate,
	isOrchestratorAgentName,
} from "../../shared/a2a.js";
import { resolveMainsequenceUserId } from "../../shared/agent-registration.js";

type A2AStreamResult = {
	text: string;
	finishReason: string | null;
	sessionKey: string | null;
	agentSessionId: number | null;
	eventsSeen: number;
	error: {
		message: string;
		source: string | null;
		code: string | null;
		detail: string | null;
	} | null;
};

function formatDiscoveredCandidates(candidates: A2ADiscoveryCandidate[]): string {
	if (candidates.length === 0) {
		return "No A2A candidates were discovered.";
	}

	return [
		"Discovered A2A candidates:",
		...candidates.map((candidate) => `- \`${candidate.agent_id}\`: ${candidate.agent_description}`),
	].join("\n");
}

function extractSelectedAgentName(candidate: A2ADiscoveryCandidate): string | null {
	const card = candidate.a2a_card;
	for (const key of ["agent_name", "agentName"]) {
		const value = card[key];
		if (typeof value === "string" && value.trim()) return value.trim();
	}
	return null;
}

function resolveA2AUserId(): string {
	return (
		resolveMainsequenceUserId({ env: process.env }) ??
		process.env.ASTRO_MAINSEQUENCE_USER_ID?.trim() ??
		process.env.MAINSEQUENCE_RUNTIME_CREDENTIAL_ID?.trim() ??
		"local-a2a-dev"
	);
}

function readCurrentSessionModelBinding():
	| Record<string, unknown>
	| null {
	const raw = process.env.ASTRO_SESSION_MODEL_BINDING?.trim();
	if (!raw) return null;
	try {
		const parsed = JSON.parse(raw);
		return parsed && typeof parsed === "object" && !Array.isArray(parsed)
			? (parsed as Record<string, unknown>)
			: null;
	} catch {
		return null;
	}
}

async function maybeConfirmA2A(
	ctx: any,
	currentAgentName: string,
	candidate: A2ADiscoveryCandidate,
	request: string,
	responseFormat: string | Record<string, unknown> | null,
	confirmedByUser: boolean,
): Promise<{ ok: true } | { ok: false; message: string }> {
	if (!isOrchestratorAgentName(currentAgentName)) return { ok: true };
	if (confirmedByUser) return { ok: true };

	const prompt = `Contact ${candidate.agent_id} through A2A?\n\nCandidate: ${candidate.agent_description}\n\nRequest: ${request}${
		responseFormat ? `\n\nRequired response format: ${typeof responseFormat === "string" ? responseFormat : JSON.stringify(responseFormat)}` : ""
	}`;

	if (ctx.hasUI) {
		const approved = await ctx.ui.confirm("Use A2A?", prompt);
		return approved ? { ok: true } : { ok: false, message: "Canceled: user did not approve A2A collaboration." };
	}

	return {
		ok: false,
		message:
			"astro-orchestrator requires explicit user confirmation before initiating A2A. Ask the user first, then retry with confirmedByUser=true.",
	};
}

async function consumeA2AStream(response: Response): Promise<A2AStreamResult> {
	if (!response.body) {
		throw new Error("A2A stream response did not include a readable body.");
	}

	const reader = response.body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";
	let text = "";
	let finishReason: string | null = null;
	let sessionKey: string | null = null;
	let agentSessionId: number | null = null;
	let eventsSeen = 0;
	let error: A2AStreamResult["error"] = null;

	const processFrame = (frame: string) => {
		const dataLines = frame
			.split(/\r?\n/)
			.filter((line) => line.startsWith("data:"))
			.map((line) => line.slice(5).trim());
		if (dataLines.length === 0) return false;
		const payload = dataLines.join("\n");
		if (payload === "[DONE]") return true;

		eventsSeen += 1;
		let parsed: any;
		try {
			parsed = JSON.parse(payload);
		} catch {
			return false;
		}

		switch (parsed?.type) {
			case "text-delta":
				if (typeof parsed.textDelta === "string") text += parsed.textDelta;
				break;
			case "new_session":
				sessionKey =
					typeof parsed.new_session?.runtime_session_id === "string"
						? parsed.new_session.runtime_session_id
						: sessionKey;
				agentSessionId =
					typeof parsed.new_session?.agent_session_id === "number"
						? parsed.new_session.agent_session_id
						: agentSessionId;
				break;
			case "finish":
				finishReason = typeof parsed.finishReason === "string" ? parsed.finishReason : null;
				break;
			case "error":
				error = {
					message: typeof parsed.error === "string" ? parsed.error : "Unknown A2A stream error.",
					source: typeof parsed.error_source === "string" ? parsed.error_source : null,
					code: typeof parsed.error_code === "string" ? parsed.error_code : null,
					detail: typeof parsed.error_detail === "string" ? parsed.error_detail : null,
				};
				break;
		}

		return false;
	};

	for (;;) {
		const { value, done } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });

		let separatorIndex = buffer.indexOf("\n\n");
		while (separatorIndex >= 0) {
			const frame = buffer.slice(0, separatorIndex);
			buffer = buffer.slice(separatorIndex + 2);
			const isDone = processFrame(frame);
			if (isDone) {
				return { text, finishReason, sessionKey, agentSessionId, eventsSeen, error };
			}
			separatorIndex = buffer.indexOf("\n\n");
		}
	}

	if (buffer.trim()) {
		processFrame(buffer.trim());
	}

	return { text, finishReason, sessionKey, agentSessionId, eventsSeen, error };
}

async function sendA2AToLocalDevRuntime(options: {
	candidate: A2ADiscoveryCandidate;
	request: string;
	responseFormat: string | Record<string, unknown> | null;
	currentAgentName: string;
	discoveryPrompt: string;
}): Promise<{
	targetUrl: string;
	stream: A2AStreamResult;
	responseStatus: number;
}> {
	const urls = resolveA2ADevBaseUrls(process.env);
	const payload = {
		newChat: true,
		userId: resolveA2AUserId(),
		agentName: extractSelectedAgentName(options.candidate) ?? undefined,
		task: options.request,
		model: readCurrentSessionModelBinding() ?? undefined,
		response_format: options.responseFormat,
		caller: {
			agent_name: options.currentAgentName,
			discovery_prompt: options.discoveryPrompt,
			mode: "local_debug",
		},
	};

	const errors: string[] = [];

	for (const baseUrl of urls) {
		const targetUrl = `${baseUrl.replace(/\/+$/, "")}/api/a2a/chat`;
		try {
			const response = await fetch(targetUrl, {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify(payload),
			});

			if (!response.ok) {
				const bodyText = await response.text();
				errors.push(`${targetUrl} -> ${response.status}: ${bodyText.trim() || "(empty response)"}`);
				continue;
			}

			const stream = await consumeA2AStream(response);
			return {
				targetUrl,
				stream,
				responseStatus: response.status,
			};
		} catch (error) {
			errors.push(`${targetUrl} -> ${error instanceof Error ? error.message : String(error)}`);
		}
	}

	throw new Error(
		`Could not reach the local A2A dev runtime. Tried: ${errors.join(" | ") || "no targets"}`,
	);
}

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "a2a_discover_agents",
		label: "A2A Discover Agents",
		description:
			"Discover A2A-capable agent candidates without sending them a request. Use this when the user asks which executor or A2A agents are available, or when you need to inspect candidates before deciding whether to communicate.",
		promptSnippet:
			"a2a_discover_agents: discover available A2A-capable agents without sending them a request",
		promptGuidelines: [
			"Use this instead of inspecting local .pi/agents when the question is about available executor or A2A agents.",
			"astro-orchestrator may discover candidates without user confirmation; confirmation is required only before sending a user-originated A2A request.",
			"Use the discovered candidates as the authoritative A2A catalog for user-facing answers about available agents.",
		],
		parameters: Type.Object({
			intent: Type.Optional(
				Type.String({
					description:
						"Optional summary of the kind of help or agent you are looking for. Leave unset to list the currently discoverable A2A candidates.",
				}),
			),
			agentHint: Type.Optional(
				Type.String({
					description: "Optional short hint about the kind of agent you expect to discover.",
				}),
			),
		}),
		async execute(_toolCallId, params) {
			const request =
				typeof params.intent === "string" && params.intent.trim()
					? params.intent.trim()
					: "List the currently discoverable A2A-capable agents and what they are best suited for.";
			const agentHint =
				typeof params.agentHint === "string" && params.agentHint.trim()
					? params.agentHint.trim()
					: null;
			const discoveryPrompt = buildA2ADiscoveryPrompt({
				request,
				agentHint,
			});

			const devProjectEnabled = Boolean(process.env[A2A_DEV_PROJECT_ENV]?.trim());
			if (!devProjectEnabled) {
				return {
					content: [
						{
							type: "text",
							text:
								"A2A backend discovery is not implemented yet. Production A2A discovery should raise not implemented until the backend routes are defined.",
						},
					],
					details: {
						ok: false,
						mode: "production",
						discoveryPrompt,
						error: "a2a_backend_discovery_not_implemented",
					},
					isError: true,
				};
			}

			try {
				const localCandidate = readA2ADevCandidate(process.env);
				const candidates = [localCandidate.candidate];
				return {
					content: [{ type: "text", text: formatDiscoveredCandidates(candidates) }],
					details: {
						ok: true,
						mode: "local_debug",
						discoveryPrompt,
						candidates,
						cardPath: localCandidate.cardPath,
					},
				};
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				return {
					content: [{ type: "text", text: `A2A discovery failed: ${message}` }],
					details: {
						ok: false,
						mode: "local_debug",
						discoveryPrompt,
						error: message,
					},
					isError: true,
				};
			}
		},
	});

	pi.registerTool({
		name: "a2a_request",
		label: "A2A Request",
		description:
			"Discover another agent candidate and send a bounded request through Astro's A2A collaboration flow. In local debug mode, discovery is mocked from A2A_DEV_PROJECT and communication routes directly to the dev executor runtime.",
		promptSnippet:
			"a2a_request: discover a candidate agent and send a bounded A2A request without switching session",
		promptGuidelines: [
			"Use this only for bounded help that stays inside your current role and task scope.",
			"Use a2a_discover_agents first when you need to inspect available A2A candidates before choosing one.",
			"astro-orchestrator must confirm with the user before initiating A2A for user-originated requests.",
			"If a response format is required, pass it so the target agent can follow it exactly.",
		],
		parameters: Type.Object({
			request: Type.String({
				description: "Bounded task or question to send through A2A.",
			}),
			responseFormat: Type.Optional(
				Type.Unknown({
					description: "Required response format or output schema for the target agent.",
				}),
			),
			agentHint: Type.Optional(
				Type.String({
					description: "Optional short hint about the kind of agent you expect to help.",
				}),
			),
			confirmedByUser: Type.Optional(
				Type.Boolean({
					description:
						"Set this to true only when the user has already confirmed that astro-orchestrator may use A2A for this request.",
					default: false,
				}),
			),
		}),
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const request = typeof params.request === "string" ? params.request.trim() : "";
			if (!request) {
				return {
					content: [{ type: "text", text: "a2a_request requires a non-empty request." }],
					isError: true,
				};
			}

			const responseFormat = normalizeA2AResponseFormat(params.responseFormat);
			const agentHint = typeof params.agentHint === "string" && params.agentHint.trim()
				? params.agentHint.trim()
				: null;
			const discoveryPrompt = buildA2ADiscoveryPrompt({
				request,
				responseFormat,
				agentHint,
			});
			const currentAgentName = resolveCurrentAstroAgentName(process.env);

			const devProjectEnabled = Boolean(process.env[A2A_DEV_PROJECT_ENV]?.trim());
			if (!devProjectEnabled) {
				return {
					content: [
						{
							type: "text",
							text:
								"A2A backend discovery and routing are not implemented yet. Production A2A should raise not implemented until the backend routes are defined.",
						},
					],
					details: {
						ok: false,
						mode: "production",
						discoveryPrompt,
						error: "a2a_backend_not_implemented",
					},
					isError: true,
				};
			}

			try {
				const localCandidate = readA2ADevCandidate(process.env);
				const candidates = [localCandidate.candidate];
				const selected = selectBestA2ACandidate(candidates, discoveryPrompt);
				if (!selected) {
					return {
						content: [{ type: "text", text: "A2A discovery did not return any candidates." }],
						details: {
							ok: false,
							mode: "local_debug",
							discoveryPrompt,
							cardPath: localCandidate.cardPath,
						},
						isError: true,
					};
				}

				const confirmation = await maybeConfirmA2A(
					ctx,
					currentAgentName,
					selected.candidate,
					request,
					responseFormat,
					params.confirmedByUser === true,
				);
				if (!confirmation.ok) {
					const confirmationMessage = (confirmation as { ok: false; message: string }).message;
					return {
						content: [{ type: "text", text: confirmationMessage }],
						details: {
							ok: false,
							mode: "local_debug",
							discoveryPrompt,
							selectedCandidate: selected.candidate,
							score: selected.score,
							cardPath: localCandidate.cardPath,
						},
						isError: true,
					};
				}

				const communication = await sendA2AToLocalDevRuntime({
					candidate: selected.candidate,
					request,
					responseFormat,
					currentAgentName,
					discoveryPrompt,
				});

				if (communication.stream.error) {
					return {
						content: [
							{
								type: "text",
								text: `A2A request failed: ${communication.stream.error.message}`,
							},
						],
						details: {
							ok: false,
							mode: "local_debug",
							discoveryPrompt,
							candidates,
							selectedCandidate: selected.candidate,
							score: selected.score,
							cardPath: localCandidate.cardPath,
							targetUrl: communication.targetUrl,
							responseStatus: communication.responseStatus,
							stream: communication.stream,
						},
						isError: true,
					};
				}

				const responseText = communication.stream.text.trim() || "A2A request completed without text output.";
				return {
					content: [{ type: "text", text: responseText }],
					details: {
						ok: true,
						mode: "local_debug",
						discoveryPrompt,
						candidates,
						selectedCandidate: selected.candidate,
						score: selected.score,
						cardPath: localCandidate.cardPath,
						targetUrl: communication.targetUrl,
						responseStatus: communication.responseStatus,
						stream: communication.stream,
					},
				};
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				return {
					content: [{ type: "text", text: `A2A request failed: ${message}` }],
					details: {
						ok: false,
						mode: "local_debug",
						discoveryPrompt,
						error: message,
					},
					isError: true,
				};
			}
		},
	});
}
