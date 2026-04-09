import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { type AgentConfig, type AgentScope, discoverAgents } from "./agents.js";
import { getFinalOutput, isResultFailure, runSingleAgent, type DelegateToolDetails, type SingleResult } from "./runtime.js";

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
});

const ChainTaskSchema = Type.Object({
	agent: Type.String({ description: "Specialist name from .pi/agents or ~/.pi/agent/agents" }),
	task: Type.String({
		description: "Delegated task text. Use {previous} to insert the previous step output.",
	}),
	cwd: Type.Optional(Type.String({ description: "Optional working directory for the child pi process" })),
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
});

function availableAgentsText(agents: AgentConfig[]): string {
	if (!agents.length) return "none";
	return agents.map((agent) => `${agent.name} (${agent.source})`).join(", ");
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
			"Delegate coding or review work to Astro's repo-local specialists with isolated child pi processes. Use mainsequence-project-coder as the coding subagent in a checked-out project folder. Use doc-bug-auditor for structured status review. Supports single-step and sequential chain execution.",
		promptSnippet:
			"delegate_specialist: delegate coding or review work to a project-local specialist in .pi/agents",
		promptGuidelines: [
			"Use mainsequence-project-coder with cwd set to the checked-out Main Sequence project when implementation should happen there.",
			"Use doc-bug-auditor when project status, blockers, or failures need review.",
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
				signal,
				onUpdate,
			});

			if (isResultFailure(result)) {
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

			return {
				content: [{ type: "text", text: getFinalOutput(result.messages) || "(no output)" }],
				details: makeDetails("single", agentScope, discovery.projectAgentsDir, [result]),
			};
		},
	});
}
