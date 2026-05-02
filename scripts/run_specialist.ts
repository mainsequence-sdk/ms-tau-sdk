import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { spawn } from "node:child_process";
import { bootstrapPiAgentDir } from "./bootstrap_pi_agent_dir.mjs";
import { discoverAgents, type AgentConfig } from "../pi/extensions/tools/specialist-delegate/agents.js";
import { resolveMainsequenceUserId } from "../pi/extensions/shared/agent-registration.js";
import { findRepoRoot } from "../pi/extensions/shared/repo.js";
import {
	buildMainsequenceStoredAuthEnv,
	bootstrapMainsequenceCliAuth,
	loadEnvFile,
	startMainsequenceCredentialExchangeLoop,
} from "./mainsequence_runtime_auth.js";

interface ParsedArgs {
	agentName: string;
	cwd?: string;
	projectId?: string;
	task?: string;
}

function fail(message: string): never {
	console.error(`[astro] ${message}`);
	process.exit(1);
}

function parseArgs(argv: string[]): ParsedArgs {
	let agentName = "";
	let cwd: string | undefined;
	let projectId: string | undefined;
	const taskParts: string[] = [];

	for (let index = 0; index < argv.length; index++) {
		const arg = argv[index];

		if (arg === "--agent") {
			agentName = argv[index + 1] || "";
			index += 1;
			continue;
		}

		if (arg === "--cwd") {
			cwd = argv[index + 1] || "";
			index += 1;
			continue;
		}

		if (arg === "--project-id") {
			projectId = argv[index + 1] || "";
			index += 1;
			continue;
		}

		taskParts.push(arg);
	}

	if (!agentName.trim()) {
		fail("Usage: npm run specialist -- --agent <name> [--cwd <path>] [--project-id <id>] [task...]");
	}

	return {
		agentName: agentName.trim(),
		cwd: cwd?.trim() || undefined,
		projectId: projectId?.trim() || undefined,
		task: taskParts.join(" ").trim() || undefined,
	};
}

function writePromptToTempFile(agent: AgentConfig): string {
	const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "astro-specialist-"));
	const safeName = agent.name.replace(/[^\w.-]+/g, "_");
	const promptPath = path.join(tempDir, `${safeName}.md`);
	fs.writeFileSync(promptPath, agent.systemPrompt, { encoding: "utf8", mode: 0o600 });
	return promptPath;
}

function cleanupPromptFile(promptPath: string) {
	try {
		fs.rmSync(path.dirname(promptPath), { recursive: true, force: true });
	} catch {
		// ignore cleanup failures
	}
}

async function main() {
	bootstrapPiAgentDir();
	const repoRoot = findRepoRoot(process.cwd());
	loadEnvFile(repoRoot);
	try {
		await bootstrapMainsequenceCliAuth({
			env: process.env,
			log: (message) => console.log(`[astro] ${message}`),
		});
	} catch (error) {
		fail(error instanceof Error ? error.message : "Unknown Main Sequence auth bootstrap failure.");
	}
	const runtimeUserId = resolveMainsequenceUserId({ env: process.env });
	const { agentName, cwd, projectId, task } = parseArgs(process.argv.slice(2));
	const discovery = discoverAgents(repoRoot, "project");
	const agent = discovery.agents.find((candidate) => candidate.name === agentName);

	if (!agent) {
		const known = discovery.agents.map((candidate) => candidate.name).sort().join(", ") || "none";
		fail(`Unknown specialist "${agentName}". Available project specialists: ${known}`);
	}

	if (agentName === "mainsequence-project-coder" || agentName === "mainsequence-project-executor") {
		if (!cwd) {
			fail(`${agentName} requires --cwd pointing to the checked-out target project.`);
		}
		if (!projectId) {
			fail(`${agentName} requires --project-id.`);
		}
		const resolvedCwd = path.resolve(cwd);
		try {
			if (!fs.statSync(resolvedCwd).isDirectory()) {
				fail(`${agentName} requires --cwd pointing to an existing checked-out target project directory.`);
			}
		} catch {
			fail(`${agentName} requires --cwd pointing to an existing checked-out target project directory.`);
		}
	}

	const promptPath = writePromptToTempFile(agent);
	const args: string[] = [];

	if (agent.model) args.push("--model", agent.model);
	if (agent.tools?.length) args.push("--tools", agent.tools.join(","));
	args.push("--append-system-prompt", promptPath);
	if (task) args.push("-p", "--no-session", task);

	const credentialExchangeLoop = startMainsequenceCredentialExchangeLoop({
		env: process.env,
		log: (message) => console.error(`[astro] ${message}`),
	});
	const proc = spawn("pi", args, {
		cwd: cwd ? path.resolve(cwd) : repoRoot,
		stdio: "inherit",
		shell: false,
		env: buildMainsequenceStoredAuthEnv({
			...process.env,
			...(runtimeUserId ? { ASTRO_MAINSEQUENCE_USER_ID: runtimeUserId } : {}),
			ASTRO_SUBAGENT_CHILD: "1",
			ASTRO_ACTIVE_SPECIALIST: agent.name,
			...(projectId ? { ASTRO_TARGET_PROJECT_ID: projectId } : {}),
		}),
	});

	proc.on("close", (code) => {
		cleanupPromptFile(promptPath);
		credentialExchangeLoop?.stop();
		process.exit(code ?? 0);
	});

	proc.on("error", (error: NodeJS.ErrnoException) => {
		cleanupPromptFile(promptPath);
		credentialExchangeLoop?.stop();
		if (error.code === "ENOENT") fail("Pi CLI is not installed or not on your PATH.");
		fail(error.message);
	});
}

main().catch((error) => {
	fail(error instanceof Error ? error.message : "Unknown specialist startup failure.");
});
