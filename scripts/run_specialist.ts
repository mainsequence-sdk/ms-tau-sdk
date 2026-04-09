import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { spawn } from "node:child_process";
import { discoverAgents, type AgentConfig } from "../pi/extensions/tools/specialist-delegate/agents.js";
import { findRepoRoot } from "../pi/extensions/shared/repo.js";

interface ParsedArgs {
	agentName: string;
	cwd?: string;
	task?: string;
}

function fail(message: string): never {
	console.error(`[astro] ${message}`);
	process.exit(1);
}

function parseArgs(argv: string[]): ParsedArgs {
	let agentName = "";
	let cwd: string | undefined;
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

		taskParts.push(arg);
	}

	if (!agentName.trim()) {
		fail("Usage: npm run specialist -- --agent <name> [--cwd <path>] [task...]");
	}

	return {
		agentName: agentName.trim(),
		cwd: cwd?.trim() || undefined,
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

function main() {
	const repoRoot = findRepoRoot(process.cwd());
	const { agentName, cwd, task } = parseArgs(process.argv.slice(2));
	const discovery = discoverAgents(repoRoot, "project");
	const agent = discovery.agents.find((candidate) => candidate.name === agentName);

	if (!agent) {
		const known = discovery.agents.map((candidate) => candidate.name).sort().join(", ") || "none";
		fail(`Unknown specialist "${agentName}". Available project specialists: ${known}`);
	}

	const promptPath = writePromptToTempFile(agent);
	const args: string[] = [];

	if (agent.model) args.push("--model", agent.model);
	if (agent.tools?.length) args.push("--tools", agent.tools.join(","));
	args.push("--append-system-prompt", promptPath);
	if (task) args.push("-p", "--no-session", task);

	const proc = spawn("pi", args, {
		cwd: cwd ? path.resolve(cwd) : repoRoot,
		stdio: "inherit",
		shell: false,
		env: {
			...process.env,
			ASTRO_SUBAGENT_CHILD: "1",
			ASTRO_ACTIVE_SPECIALIST: agent.name,
		},
	});

	proc.on("close", (code) => {
		cleanupPromptFile(promptPath);
		process.exit(code ?? 0);
	});

	proc.on("error", (error: NodeJS.ErrnoException) => {
		cleanupPromptFile(promptPath);
		if (error.code === "ENOENT") fail("Pi CLI is not installed or not on your PATH.");
		fail(error.message);
	});
}

main();
