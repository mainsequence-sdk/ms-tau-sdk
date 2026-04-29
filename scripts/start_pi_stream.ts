import { bootstrapPiAgentDir } from "./bootstrap_pi_agent_dir.mjs";
import { logStructuredEvent } from "../pi/extensions/shared/structured-logging.js";

try {
	const piAgentState = bootstrapPiAgentDir();
	logStructuredEvent({
		component: "astro-stream",
		event: "workspace_analysis_skill_ready",
		message: "Astro materialized the workspace-analysis skill before server startup.",
		data: {
			workspaceAnalysisSkill: piAgentState.workspaceAnalysisSkill ?? null,
		},
	});
	logStructuredEvent({
		component: "astro-stream",
		event: "orchestrator_runtime_ready",
		message: "Astro orchestrator runtime cwd and project-local Pi settings are ready.",
		data: {
			piAgentDir: piAgentState.targetDir,
			orchestratorRuntimeCwd: piAgentState.orchestratorRuntime?.runtimeCwd,
			orchestratorProjectPiDir: piAgentState.orchestratorRuntime?.projectPiDir,
			orchestratorProjectPiLink: piAgentState.orchestratorRuntime?.projectPiLink,
			workspaceAnalysisSkill: piAgentState.workspaceAnalysisSkill ?? null,
			prunedProviderAuthEntries: piAgentState.prunedProviderAuthEntries ?? [],
			prunedScopedProviderCredentialDir: piAgentState.prunedScopedProviderCredentialDir ?? null,
		},
	});
} catch (error) {
	const serialized =
		error instanceof Error
			? {
					name: error.name,
					message: error.message,
					stack: error.stack,
			  }
			: {
					name: null,
					message: String(error),
					stack: null,
			  };
	process.env.ASTRO_STREAM_BOOTSTRAP_ERROR = JSON.stringify(serialized);
	console.error(`[astro-stream] bootstrap failed before server startup: ${serialized.message}`);
	throw error instanceof Error ? error : new Error(serialized.message);
}

await import("../interface/stream/server.js");
