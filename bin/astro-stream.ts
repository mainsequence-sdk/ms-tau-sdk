import { bootstrapPiAgentDir } from "../runtime/bootstrap/pi-agent-dir.mjs";
import { logStructuredEvent } from "../pi/extensions/shared/structured-logging.js";

try {
	const piAgentState = bootstrapPiAgentDir();
	logStructuredEvent({
		component: "astro-stream",
		event: "runtime_project_ready",
		message: "Astro runtime cwd and project-local Pi package settings are ready.",
		data: {
			piAgentDir: piAgentState.targetDir,
			runtimeCwd: piAgentState.runtimeProject?.runtimeCwd,
			runtimeProjectPiDir: piAgentState.runtimeProject?.projectPiDir,
			runtimeProjectPiLink: piAgentState.runtimeProject?.projectPiLink,
			orchestratorRuntimeCwd: piAgentState.orchestratorRuntime?.runtimeCwd,
			orchestratorProjectPiDir: piAgentState.orchestratorRuntime?.projectPiDir,
			orchestratorProjectPiLink: piAgentState.orchestratorRuntime?.projectPiLink,
			configuredPiPackages: piAgentState.configuredPiPackages ?? [],
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
	logStructuredEvent({
		severity: "ERROR",
		component: "astro-stream",
		event: "stream.bootstrap.failed",
		message: "Bootstrap failed before server startup.",
		data: {
			error: serialized.message,
			errorType: serialized.name,
			stack: serialized.stack,
		},
	});
	throw error instanceof Error ? error : new Error(serialized.message);
}

await import("../interface/stream/server.js");
