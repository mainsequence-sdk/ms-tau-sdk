import { bootstrapPiAgentDir } from "./bootstrap_pi_agent_dir.mjs";
import { logStructuredEvent } from "../pi/extensions/shared/structured-logging.js";

try {
	const piAgentState = bootstrapPiAgentDir();
	logStructuredEvent({
		component: "astro-stream",
		event: "orchestrator_runtime_ready",
		message: "Astro orchestrator runtime cwd and project-local Pi settings are ready.",
		data: {
			piAgentDir: piAgentState.targetDir,
			orchestratorRuntimeCwd: piAgentState.orchestratorRuntime?.runtimeCwd,
			orchestratorProjectPiDir: piAgentState.orchestratorRuntime?.projectPiDir,
			orchestratorProjectPiLink: piAgentState.orchestratorRuntime?.projectPiLink,
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
}

await import("../interface/stream/server.js");
