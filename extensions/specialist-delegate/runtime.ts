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
		step,
	};

	const emitUpdate = () => {
		if (!onUpdate) return;
		onUpdate({
			content: [
				{
					type: "text",
					text: getFinalOutput(currentResult.messages) || `[${agent.name}] running...`,
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
					}

					emitUpdate();
				}

				if (parsed.type === "tool_result_end" && message) {
					currentResult.messages.push(message);
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
