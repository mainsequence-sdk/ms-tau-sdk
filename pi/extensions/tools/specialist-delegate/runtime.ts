import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { AgentToolUpdateCallback } from "@mariozechner/pi-agent-core";
import type { Message } from "@mariozechner/pi-ai";
import type { AgentConfig, AgentScope } from "./agents.js";

export type DelegateMode = "single" | "chain";

export interface UsageStats {
	input: number;
	output: number;
	cacheRead: number;
	cacheWrite: number;
	cost: number;
	contextTokens: number;
	turns: number;
}

export interface SingleResult {
	agent: string;
	agentSource: "user" | "project" | "unknown";
	task: string;
	exitCode: number;
	messages: Message[];
	stderr: string;
	usage: UsageStats;
	model?: string;
	stopReason?: string;
	errorMessage?: string;
	liveStatus?: string;
	liveTrace?: string[];
	step?: number;
}

export interface DelegateToolDetails {
	mode: DelegateMode;
	agentScope: AgentScope;
	projectAgentsDir: string | null;
	results: SingleResult[];
}

function emptyUsage(): UsageStats {
	return {
		input: 0,
		output: 0,
		cacheRead: 0,
		cacheWrite: 0,
		cost: 0,
		contextTokens: 0,
		turns: 0,
	};
}

function writePromptToTempFile(agentName: string, prompt: string): string | null {
	if (!prompt.trim()) return null;

	const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "astro-agent-"));
	const safeName = agentName.replace(/[^\w.-]+/g, "_");
	const promptPath = path.join(tempDir, `${safeName}.md`);
	fs.writeFileSync(promptPath, prompt, { encoding: "utf8", mode: 0o600 });
	return promptPath;
}

function cleanupPromptFile(promptPath: string | null) {
	if (!promptPath) return;
	try {
		fs.rmSync(path.dirname(promptPath), { recursive: true, force: true });
	} catch {
		// ignore cleanup failures
	}
}

export function getFinalOutput(messages: Message[]): string {
	for (let i = messages.length - 1; i >= 0; i--) {
		const message = messages[i];
		if (message.role !== "assistant") continue;

		for (const part of message.content) {
			if (part.type === "text") return part.text;
		}
	}

	return "";
}

function clipInline(value: string, maxLength = 160): string {
	const compact = value.replace(/\s+/g, " ").trim();
	if (!compact) return "";
	if (compact.length <= maxLength) return compact;
	return `${compact.slice(0, maxLength - 3).trimEnd()}...`;
}

function summarizeTask(task: string, maxLength = 120): string {
	const trimmed = task.trim();
	if (!trimmed) return "";

	const firstLine = trimmed.split(/\r?\n/).map((line) => line.trim()).find(Boolean) || "";
	if (!firstLine) return "";
	if (firstLine.length <= maxLength) return firstLine;
	return `${firstLine.slice(0, maxLength - 3).trimEnd()}...`;
}

function clipBlock(value: string, maxLines = 3, maxLength = 240): string {
	const compact = value.trim();
	if (!compact) return "";

	const lines = compact
		.split(/\r?\n/)
		.map((line) => line.trim())
		.filter(Boolean)
		.slice(0, maxLines);

	const joined = lines.join("\n");
	if (joined.length <= maxLength) return joined;
	return `${joined.slice(0, maxLength - 3).trimEnd()}...`;
}

function extractTextParts(content: any[] | undefined): string {
	if (!Array.isArray(content)) return "";
	return content
		.filter((part) => part?.type === "text" && typeof part.text === "string")
		.map((part) => part.text)
		.join("");
}

function extractTextPayload(payload: any): string {
	if (!payload) return "";
	if (typeof payload === "string") return payload;
	if (typeof payload.text === "string") return payload.text;
	if (Array.isArray(payload.content)) return extractTextParts(payload.content);
	return "";
}

function summarizeArguments(args: any, partialJson?: string): string {
	if (typeof args === "string") return clipInline(args);
	if (!args || typeof args !== "object") return clipInline(String(args || ""));

	const preferredKeys = ["command", "path", "pattern", "q", "query", "url", "location", "ticker"];
	for (const key of preferredKeys) {
		if (typeof args[key] === "string" && args[key].trim()) return clipInline(args[key]);
	}

	const meaningfulEntries = Object.entries(args).filter(([, value]) => {
		if (typeof value === "string") return value.trim().length > 0;
		if (value == null) return false;
		if (Array.isArray(value)) return value.length > 0;
		if (typeof value === "object") return Object.keys(value).length > 0;
		return true;
	});

	if (meaningfulEntries.length === 0) return "";

	try {
		const serialized = JSON.stringify(Object.fromEntries(meaningfulEntries));
		if (serialized && serialized !== "{}") return clipInline(serialized);
	} catch {
		// ignore serialization failures
	}

	return partialJson ? clipInline(partialJson) : "";
}

function findToolCall(message: Message | undefined, contentIndex?: number): any {
	const content = Array.isArray(message?.content) ? message.content : [];
	if (typeof contentIndex === "number" && contentIndex >= 0 && contentIndex < content.length) {
		const candidate = content[contentIndex];
		if (candidate?.type === "toolCall") return candidate;
	}

	for (let index = content.length - 1; index >= 0; index--) {
		const candidate = content[index];
		if (candidate?.type === "toolCall") return candidate;
	}

	return null;
}

function formatToolCallStatus(agentName: string, toolCall: any): string {
	const toolName = toolCall?.name || "tool";
	const args = summarizeArguments(toolCall?.arguments);
	return args ? `[${agentName}] calling ${toolName}: ${args}` : `[${agentName}] calling ${toolName}...`;
}

function getAssistantLiveStatus(agentName: string, event: any, message: Message | undefined): string | null {
	const eventType = event?.type;
	if (typeof eventType !== "string") return null;

	if (eventType.startsWith("text")) {
		const text = clipBlock(extractTextParts(message?.content as any[] | undefined));
		return text || `[${agentName}] writing...`;
	}

	if (eventType.startsWith("toolcall")) {
		const toolCall = event?.toolCall || findToolCall(message, event?.contentIndex);
		return toolCall ? formatToolCallStatus(agentName, toolCall) : `[${agentName}] preparing tool call...`;
	}

	if (eventType.startsWith("thinking")) {
		return `[${agentName}] thinking...`;
	}

	return null;
}

function getToolExecutionStatus(agentName: string, parsed: any, previousStatus?: string): string | null {
	const toolName = parsed?.toolName || "tool";
	const args = summarizeArguments(parsed?.args);
	const previousPrefix =
		typeof previousStatus === "string" && previousStatus.startsWith(`[${agentName}] ${toolName}`)
			? previousStatus.split("\n")[0]
			: "";
	const prefix = args
		? `[${agentName}] ${toolName}: ${args}`
		: previousPrefix || `[${agentName}] ${toolName}`;

	if (parsed?.type === "tool_execution_start") {
		return `${prefix}\n(running)`;
	}

	if (parsed?.type === "tool_execution_update") {
		const output = clipBlock(extractTextPayload(parsed?.partialResult));
		return output ? `${prefix}\n${output}` : `${prefix}\n(running)`;
	}

	if (parsed?.type === "tool_execution_end") {
		const output = clipBlock(extractTextPayload(parsed?.result));
		if (output) return `${prefix}\n${output}`;
		return parsed?.isError ? `${prefix}\n(failed)` : `${prefix}\n(done)`;
	}

	return null;
}

function getToolResultStatus(agentName: string, message: Message | undefined): string | null {
	if (!message || message.role !== "toolResult") return null;

	const toolName = (message as any).toolName || "tool";
	const output = clipBlock(extractTextParts(message.content as any[] | undefined));
	if (!output) return `[${agentName}] ${toolName} finished`;
	return `[${agentName}] ${toolName} result\n${output}`;
}

function pushLiveStatus(result: SingleResult, status: string | null | undefined) {
	if (!status) return;

	const normalized = status.trim();
	if (!normalized) return;

	const trace = result.liveTrace ?? [];
	if (trace[trace.length - 1] === normalized) {
		result.liveStatus = normalized;
		result.liveTrace = trace;
		return;
	}

	const nextTrace = [...trace, normalized].slice(-5);
	result.liveStatus = normalized;
	result.liveTrace = nextTrace;
}

function getDisplayOutput(result: SingleResult): string {
	if (Array.isArray(result.liveTrace) && result.liveTrace.length > 0) {
		return result.liveTrace.join("\n\n");
	}

	if (result.liveStatus) return result.liveStatus;

	const finalOutput = getFinalOutput(result.messages);
	if (finalOutput) return finalOutput;

	return `[${result.agent}] running...`;
}

export function isResultFailure(result: SingleResult): boolean {
	return result.exitCode !== 0 || result.stopReason === "error" || result.stopReason === "aborted";
}

export async function runSingleAgent(options: {
	defaultCwd: string;
	agents: AgentConfig[];
	projectAgentsDir: string | null;
	agentName: string;
	task: string;
	mode: DelegateMode;
	agentScope: AgentScope;
	cwd?: string;
	projectId?: string;
	step?: number;
	signal?: AbortSignal;
	onUpdate?: AgentToolUpdateCallback;
}): Promise<SingleResult> {
	const {
		defaultCwd,
		agents,
		projectAgentsDir,
		agentName,
		task,
		mode,
		agentScope,
		cwd,
		projectId,
		step,
		signal,
		onUpdate,
	} = options;

	const agent = agents.find((candidate) => candidate.name === agentName);

	if (!agent) {
		return {
			agent: agentName,
			agentSource: "unknown",
			task,
			exitCode: 1,
			messages: [],
			stderr: `Unknown agent "${agentName}"`,
			usage: emptyUsage(),
			step,
		};
	}

	const currentResult: SingleResult = {
		agent: agent.name,
		agentSource: agent.source,
		task,
		exitCode: 0,
		messages: [],
		stderr: "",
		usage: emptyUsage(),
		model: agent.model,
		liveStatus: summarizeTask(task)
			? `[${agent.name}] starting task\n${summarizeTask(task)}`
			: `[${agent.name}] starting...`,
		liveTrace: [],
		step,
	};
	pushLiveStatus(currentResult, currentResult.liveStatus);

	let lastEmittedText = "";

	const emitUpdate = () => {
		if (!onUpdate) return;
		const text = getDisplayOutput(currentResult);
		if (text === lastEmittedText) return;
		lastEmittedText = text;
		onUpdate({
			content: [
				{
					type: "text",
					text,
				},
			],
			details: {
				mode,
				agentScope,
				projectAgentsDir,
				results: [currentResult],
			},
		});
	};

	const args = ["--mode", "json", "-p", "--no-session"];
	if (agent.model) args.push("--model", agent.model);
	if (agent.tools?.length) args.push("--tools", agent.tools.join(","));

	const promptPath = writePromptToTempFile(agent.name, agent.systemPrompt);
	if (promptPath) args.push("--append-system-prompt", promptPath);
	args.push(task);

	let aborted = false;
	emitUpdate();

	try {
		const exitCode = await new Promise<number>((resolve) => {
			const proc = spawn("pi", args, {
				cwd: cwd || defaultCwd,
				shell: false,
				stdio: ["ignore", "pipe", "pipe"],
				env: {
					...process.env,
					ASTRO_SUBAGENT_CHILD: "1",
					ASTRO_ACTIVE_SPECIALIST: agent.name,
					...(projectId ? { ASTRO_TARGET_PROJECT_ID: projectId } : {}),
				},
			});

			let buffer = "";

			const processLine = (line: string) => {
				if (!line.trim()) return;

				let parsed: any;
				try {
					parsed = JSON.parse(line);
				} catch {
					return;
				}

				const message = parsed.message as Message | undefined;

				if (parsed.type === "message_update" && message?.role === "assistant") {
					currentResult.model = (message as any).model || currentResult.model;
					currentResult.stopReason = (message as any).stopReason || currentResult.stopReason;
					pushLiveStatus(
						currentResult,
						getAssistantLiveStatus(agent.name, parsed.assistantMessageEvent, message) ||
							currentResult.liveStatus,
					);
					emitUpdate();
					return;
				}

				if (parsed.type === "message_end" && message) {
					currentResult.messages.push(message);

					if (message.role === "assistant") {
						currentResult.usage.turns += 1;
						const usage = (message as any).usage;
						if (usage) {
							currentResult.usage.input += usage.input || 0;
							currentResult.usage.output += usage.output || 0;
							currentResult.usage.cacheRead += usage.cacheRead || 0;
							currentResult.usage.cacheWrite += usage.cacheWrite || 0;
							currentResult.usage.cost += usage.cost?.total || usage.cost || 0;
							currentResult.usage.contextTokens =
								usage.totalTokens || usage.contextTokens || currentResult.usage.contextTokens;
						}

						currentResult.model = (message as any).model || currentResult.model;
						currentResult.stopReason = (message as any).stopReason || currentResult.stopReason;
						currentResult.errorMessage = (message as any).errorMessage || currentResult.errorMessage;
						if (extractTextParts(message.content as any[] | undefined).trim()) {
							currentResult.liveStatus = undefined;
							currentResult.liveTrace = undefined;
						}
					}

					emitUpdate();
					return;
				}

				if (parsed.type === "tool_result_end" && message) {
					currentResult.messages.push(message);
					if (!currentResult.liveStatus) {
						pushLiveStatus(
							currentResult,
							getToolResultStatus(agent.name, message) || currentResult.liveStatus,
						);
					}
					emitUpdate();
					return;
				}

				if (
					parsed.type === "tool_execution_start" ||
					parsed.type === "tool_execution_update" ||
					parsed.type === "tool_execution_end"
				) {
					pushLiveStatus(
						currentResult,
						getToolExecutionStatus(agent.name, parsed, currentResult.liveStatus) ||
							currentResult.liveStatus,
					);
					emitUpdate();
				}
			};

			proc.stdout.on("data", (chunk) => {
				buffer += chunk.toString();
				const lines = buffer.split("\n");
				buffer = lines.pop() || "";
				for (const line of lines) processLine(line);
			});

			proc.stderr.on("data", (chunk) => {
				currentResult.stderr += chunk.toString();
			});

			proc.on("close", (code) => {
				if (buffer.trim()) processLine(buffer);
				resolve(code ?? 0);
			});

			proc.on("error", () => {
				resolve(1);
			});

			if (signal) {
				const abort = () => {
					aborted = true;
					try {
						proc.kill("SIGTERM");
					} catch {
						// ignore
					}
				};

				if (signal.aborted) abort();
				else signal.addEventListener("abort", abort, { once: true });
			}
		});

		currentResult.exitCode = exitCode;
		if (aborted) currentResult.stopReason = "aborted";
		return currentResult;
	} finally {
		cleanupPromptFile(promptPath);
	}
}
