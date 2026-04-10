import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { registerMainsequenceAgent } from "../../shared/agent-registration.js";

export default function (pi: ExtensionAPI) {
	pi.on("session_start", async (_event, ctx) => {
		const enabled = /^(1|true|yes|on)$/i.test(process.env.BUILD_AGENTS_IN_BACKEND ?? "");
		if (!enabled) return;

		const agentRole = process.env.ASTRO_SUBAGENT_CHILD === "1" ? "specialist" : "orchestrator";
		const agentName = process.env.ASTRO_ACTIVE_SPECIALIST?.trim() || "astro-orchestrator";

		await registerMainsequenceAgent({
			agentName,
			agentRole,
			cwd: ctx.cwd,
			log: (message) => {
				console.log(`[astro-agent-register] ${message}`);
			},
		});
	});
}
