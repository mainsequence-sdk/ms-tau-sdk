import { bootstrapPiAgentDir } from "./bootstrap_pi_agent_dir.mjs";

try {
	bootstrapPiAgentDir();
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
