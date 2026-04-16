import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { registerMainsequenceAgent, shouldRegisterAgents } from "../../shared/agent-registration.js";

function failHardRegistration(message: string): never {
	console.error(`[astro-agent-register] ${message}`);
	setImmediate(() => {
		process.exit(1);
	});
	throw new Error(message);
}

export default function (pi: ExtensionAPI) {
	pi.on("session_start", async (_event, ctx) => {
		if (!shouldRegisterAgents()) return;

		const agentRole = process.env.ASTRO_SUBAGENT_CHILD === "1" ? "specialist" : "orchestrator";
		const agentName = process.env.ASTRO_ACTIVE_SPECIALIST?.trim() || "astro-orchestrator";

		const registration = await registerMainsequenceAgent({
			agentName,
			agentRole,
			cwd: ctx.cwd,
			userId: process.env.ASTRO_MAINSEQUENCE_USER_ID,
			log: (message) => {
				console.log(`[astro-agent-register] ${message}`);
			},
		});

		if (!registration.ok || !registration.agentId) {
			failHardRegistration(
				`${agentName} deterministic registration failed: ${
					registration.stderr || "missing backend agent id."
				}`,
			);
		}
	});
}
