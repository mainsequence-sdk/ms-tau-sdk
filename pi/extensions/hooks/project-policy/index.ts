import * as path from "node:path";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { findRepoRoot, safeReadFile } from "../../shared/repo.js";

export default function (pi: ExtensionAPI) {
	if (process.env.ASTRO_SUBAGENT_CHILD !== "1") return;

	pi.on("before_agent_start", async (event, ctx) => {
		const repoRoot = findRepoRoot(ctx.cwd);
		const policyPath = path.join(
			repoRoot,
			"pi",
			"extensions",
			"project-policy",
			"child-policy.md",
		);
		const policy = safeReadFile(policyPath);
		if (!policy?.trim()) return;

		return {
			systemPrompt: `${event.systemPrompt}

# Astro child-specialist policy
Current specialist: ${process.env.ASTRO_ACTIVE_SPECIALIST || "unknown"}

${policy.trim()}`,
		};
	});
}
