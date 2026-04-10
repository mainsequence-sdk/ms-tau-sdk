import * as fs from "node:fs";
import * as path from "node:path";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { type AgentConfig, type AgentScope, discoverAgents } from "./agents.js";
import { getFinalOutput, isResultFailure, runSingleAgent, type DelegateToolDetails, type SingleResult } from "./runtime.js";
import { emitTelemetryEvent } from "../../shared/telemetry.js";

const AgentScopeSchema = Type.Union(
	[Type.Literal("user"), Type.Literal("project"), Type.Literal("both")],
	{
		default: "project",
		description: "Where to discover specialists from. Default is project.",
	},
);

const SingleTaskSchema = Type.Object({
	agent: Type.String({ description: "Specialist name from .pi/agents or ~/.pi/agent/agents" }),
	task: Type.String({ description: "Delegated task text" }),
	cwd: Type.Optional(Type.String({ description: "Optional working directory for the child pi process" })),
	projectId: Type.Optional(
		Type.String({ description: "Selected Main Sequence project id. Required for checked-out project coding." }),
	),
});

const ChainTaskSchema = Type.Object({
	agent: Type.String({ description: "Specialist name from .pi/agents or ~/.pi/agent/agents" }),
	task: Type.String({
		description: "Delegated task text. Use {previous} to insert the previous step output.",
	}),
	cwd: Type.Optional(Type.String({ description: "Optional working directory for the child pi process" })),
	projectId: Type.Optional(
		Type.String({ description: "Selected Main Sequence project id for this step when needed." }),
	),
});

const DelegateParams = Type.Object({
	agent: Type.Optional(Type.String({ description: "Specialist name for single-step mode" })),
	task: Type.Optional(Type.String({ description: "Delegated task for single-step mode" })),
	chain: Type.Optional(
		Type.Array(ChainTaskSchema, {
			description: "Sequential specialist chain. Each step can reference {previous}.",
		}),
	),
	agentScope: Type.Optional(AgentScopeSchema),
	confirmProjectAgents: Type.Optional(
		Type.Boolean({
			description: "Prompt before running project-local specialists when UI is available.",
			default: false,
		}),
	),
	cwd: Type.Optional(Type.String({ description: "Working directory for single-step mode" })),
	projectId: Type.Optional(
		Type.String({ description: "Selected Main Sequence project id for single-step mode." }),
	),
});

function availableAgentsText(agents: AgentConfig[]): string {
	if (!agents.length) return "none";
	return agents.map((agent) => `${agent.name} (${agent.source})`).join(", ");
}

function isDirectory(candidate: string): boolean {
	try {
		return fs.statSync(candidate).isDirectory();
	} catch {
		return false;
	}
}

function validateCoderTarget(options: {
	agentName: string;
	cwd?: string;
	projectId?: string;
}): string | null {
	if (options.agentName !== "mainsequence-project-coder") return null;

	if (!options.projectId?.trim()) {
		return "mainsequence-project-coder requires `projectId`. Do not delegate coding work without the selected Main Sequence project id.";
	}

	if (!options.cwd?.trim()) {
		return "mainsequence-project-coder requires `cwd`. Do not delegate coding work without the checked-out target project path.";
	}

	const resolvedCwd = path.resolve(options.cwd);
	if (!isDirectory(resolvedCwd)) {
		return `mainsequence-project-coder requires a valid checked-out project directory. Invalid cwd: ${resolvedCwd}`;
	}

	return null;
}

function makeDetails(
	mode: "single" | "chain",
	agentScope: AgentScope,
	projectAgentsDir: string | null,
	results: SingleResult[],
): DelegateToolDetails {
	return { mode, agentScope, projectAgentsDir, results };
}

async function maybeConfirmProjectSpecialists(
	ctx: any,
	agents: AgentConfig[],
	requestedNames: string[],
	discovery: { projectAgentsDir: string | null },
	confirmProjectAgents: boolean,
): Promise<boolean> {
	if (!confirmProjectAgents || !ctx.hasUI) return true;

	const projectAgents = requestedNames
		.map((name) => agents.find((agent) => agent.name === name))
		.filter((agent): agent is AgentConfig => Boolean(agent && agent.source === "project"));

	if (projectAgents.length === 0) return true;

	const names = projectAgents.map((agent) => agent.name).join(", ");
	return await ctx.ui.confirm(
		"Run project-local specialists?",
		`Specialists: ${names}\nSource: ${discovery.projectAgentsDir || "(unknown)"}\n\nOnly continue if this repository is trusted.`,
	);
}

export default function (pi: ExtensionAPI) {
	if (process.env.ASTRO_SUBAGENT_CHILD === "1") return;

	pi.registerTool({
		name: "delegate_specialist",
		label: "Delegate Specialist",
		description:
			"Delegate coding work to Astro's repo-local specialists with isolated child pi processes. Use mainsequence-project-coder as the coding subagent in a checked-out project folder. Supports single-step and sequential chain execution.",
		promptSnippet:
			"delegate_specialist: delegate coding work to a project-local specialist in .pi/agents",
		promptGuidelines: [
			"Use mainsequence-project-coder only with both cwd and projectId set to the checked-out Main Sequence project.",
			"Prefer project-local specialists for this repo unless there is a clear reason to use user-level specialists.",
		],
		parameters: DelegateParams,
		async execute(_toolCallId, params, signal, onUpdate, ctx) {
			const agentScope: AgentScope = params.agentScope ?? "project";
			const confirmProjectAgents = params.confirmProjectAgents ?? false;
			const discovery = discoverAgents(ctx.cwd, agentScope);
			const agents = discovery.agents;

			const hasChain = Array.isArray(params.chain) && params.chain.length > 0;
			const hasSingle = Boolean(params.agent && params.task);

			if (Number(hasChain) + Number(hasSingle) !== 1) {
				return {
					content: [
						{
							type: "text",
							text: `Invalid delegate_specialist parameters. Provide either {agent, task} or {chain}. Available agents: ${availableAgentsText(agents)}.`,
						},
					],
					details: makeDetails("single", agentScope, discovery.projectAgentsDir, []),
					isError: true,
				};
			}

			const requestedNames = hasChain
				? params.chain!.map((step) => step.agent)
				: [params.agent as string];

			const validationError = hasChain
				? params.chain!
						.map((step) =>
							validateCoderTarget({
								agentName: step.agent,
								cwd: step.cwd,
								projectId: step.projectId,
							}),
						)
						.find(Boolean) || null
				: validateCoderTarget({
						agentName: params.agent as string,
						cwd: params.cwd,
						projectId: params.projectId,
				  });

			if (validationError) {
				return {
					content: [{ type: "text", text: validationError }],
					details: makeDetails(hasChain ? "chain" : "single", agentScope, discovery.projectAgentsDir, []),
					isError: true,
				};
			}

			const approved = await maybeConfirmProjectSpecialists(
				ctx,
				agents,
				requestedNames,
				discovery,
				confirmProjectAgents,
			);

			if (!approved) {
				return {
					content: [{ type: "text", text: "Canceled: project-local specialists were not approved." }],
					details: makeDetails(hasChain ? "chain" : "single", agentScope, discovery.projectAgentsDir, []),
					isError: true,
				};
			}

			const delegateContext = {
				mode: hasChain ? "chain" : "single",
				agents: requestedNames,
				chainLength: hasChain ? params.chain!.length : 1,
				cwd: params.cwd ?? null,
				projectId: params.projectId ?? null,
			};
			emitTelemetryEvent("delegate_start", delegateContext);

			if (hasChain) {
				const results: SingleResult[] = [];
				let previousOutput = "";

				for (let index = 0; index < params.chain!.length; index++) {
					const step = params.chain![index];
					const task = step.task.replace(/\{previous\}/g, previousOutput);

					const result = await runSingleAgent({
						defaultCwd: ctx.cwd,
						agents,
						projectAgentsDir: discovery.projectAgentsDir,
						agentName: step.agent,
						task,
						mode: "chain",
						agentScope,
						cwd: step.cwd,
						projectId: step.projectId,
						step: index + 1,
						signal,
						onUpdate: onUpdate
							? (partial) => {
									const current = partial.details?.results?.[0];
									if (!current) return;
									onUpdate({
										content: partial.content,
										details: makeDetails("chain", agentScope, discovery.projectAgentsDir, [
											...results,
											current,
										]),
									});
							  }
							: undefined,
					});

					results.push(result);

					if (isResultFailure(result)) {
						emitTelemetryEvent("delegate_end", {
							...delegateContext,
							status: "error",
							step: index + 1,
							agent: step.agent,
						});
						return {
							content: [
								{
									type: "text",
									text:
										result.errorMessage ||
										result.stderr ||
										getFinalOutput(result.messages) ||
										`Chain failed in step ${index + 1}.`,
								},
							],
							details: makeDetails("chain", agentScope, discovery.projectAgentsDir, results),
							isError: true,
						};
					}

					previousOutput = getFinalOutput(result.messages);
				}

				emitTelemetryEvent("delegate_end", { ...delegateContext, status: "success" });
				return {
					content: [
						{
							type: "text",
							text: getFinalOutput(results[results.length - 1].messages) || "(no output)",
						},
					],
					details: makeDetails("chain", agentScope, discovery.projectAgentsDir, results),
				};
			}

			const result = await runSingleAgent({
				defaultCwd: ctx.cwd,
				agents,
				projectAgentsDir: discovery.projectAgentsDir,
				agentName: params.agent as string,
				task: params.task as string,
				mode: "single",
				agentScope,
				cwd: params.cwd,
				projectId: params.projectId,
				signal,
				onUpdate,
			});

			if (isResultFailure(result)) {
				emitTelemetryEvent("delegate_end", {
					...delegateContext,
					status: "error",
					agent: params.agent as string,
				});
				return {
					content: [
						{
							type: "text",
							text:
								result.errorMessage ||
								result.stderr ||
								getFinalOutput(result.messages) ||
								"Specialist failed.",
						},
					],
					details: makeDetails("single", agentScope, discovery.projectAgentsDir, [result]),
					isError: true,
				};
			}

			emitTelemetryEvent("delegate_end", {
				...delegateContext,
				status: "success",
				agent: params.agent as string,
			});
			return {
				content: [{ type: "text", text: getFinalOutput(result.messages) || "(no output)" }],
				details: makeDetails("single", agentScope, discovery.projectAgentsDir, [result]),
			};
		},
	});
}
