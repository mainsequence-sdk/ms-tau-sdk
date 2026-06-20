import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

const __dirname = dirname(fileURLToPath(import.meta.url));
const childPolicyPath = join(__dirname, "child-policy.md");
type Env = Record<string, string | undefined>;

function isChildRuntime(env: Env): boolean {
	return env.MAINSEQUENCE_PI_CHILD_RUNTIME === "1";
}

function activeAgentName(env: Env): string {
	return env.MAINSEQUENCE_PI_ACTIVE_AGENT || "unknown";
}

export default function (pi: ExtensionAPI) {
	if (!isChildRuntime(process.env)) return;

	pi.on("before_agent_start", async (event) => {
		const policy = readFileSync(childPolicyPath, "utf8").trim();
		if (!policy) return;

		return {
			systemPrompt: `${event.systemPrompt}

# Main Sequence child runtime policy
Current agent: ${activeAgentName(process.env)}

${policy}`,
		};
	});
}
