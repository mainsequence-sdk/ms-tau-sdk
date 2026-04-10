import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { spawn } from "node:child_process";
import { discoverAgents, type AgentConfig } from "../pi/extensions/tools/specialist-delegate/agents.js";
import { findRepoRoot } from "../pi/extensions/shared/repo.js";

const MAINSEQUENCE_REFRESH_INTERVAL_ENV = "MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS";
const MAINSEQUENCE_REQUIRED_ENV = [
	"MAINSEQUENCE_ACCESS_TOKEN",
	"MAINSEQUENCE_REFRESH_TOKEN",
	"MAINSEQUENCE_BACKEND",
	"MAINSEQUENCE_PROJECTS_BASE",
];

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

function loadEnvFile(repoRoot: string) {
	const envPath = path.join(repoRoot, ".env");
	if (!fs.existsSync(envPath)) return;
	const contents = fs.readFileSync(envPath, "utf8");
	for (const rawLine of contents.split(/\r?\n/)) {
		const line = rawLine.trim();
		if (!line || line.startsWith("#")) continue;
		const equalsIndex = line.indexOf("=");
		if (equalsIndex === -1) continue;
		const key = line.slice(0, equalsIndex).trim();
		if (!key) continue;
		let value = line.slice(equalsIndex + 1).trim();
		if (
			(value.startsWith("\"") && value.endsWith("\"")) ||
			(value.startsWith("'") && value.endsWith("'"))
		) {
			value = value.slice(1, -1);
		}
		if (process.env[key] === undefined) {
			process.env[key] = value;
		}
	}
}

function getRefreshIntervalMs(): number | null {
	const raw = process.env[MAINSEQUENCE_REFRESH_INTERVAL_ENV];
	if (!raw) return null;
	const seconds = Number.parseInt(raw, 10);
	if (!Number.isFinite(seconds) || seconds <= 0) {
		fail(`${MAINSEQUENCE_REFRESH_INTERVAL_ENV} must be a positive integer (seconds).`);
	}
	return seconds * 1000;
}

function ensureMainsequenceEnv() {
	const missing = MAINSEQUENCE_REQUIRED_ENV.filter((key) => !process.env[key]);
	if (missing.length) {
		fail(`Missing required env for token refresh: ${missing.join(", ")}`);
	}
}

function startMainsequenceRefreshLoop() {
	const intervalMs = getRefreshIntervalMs();
	if (!intervalMs) return null;
	ensureMainsequenceEnv();

	let refreshInFlight = false;
	const runLogin = (reason: string) => {
		if (refreshInFlight) return;
		refreshInFlight = true;
		const args = [
			"login",
			"--access-token",
			process.env.MAINSEQUENCE_ACCESS_TOKEN,
			"--refresh-token",
			process.env.MAINSEQUENCE_REFRESH_TOKEN,
			"--backend",
			process.env.MAINSEQUENCE_BACKEND,
			"--projects-base",
			process.env.MAINSEQUENCE_PROJECTS_BASE,
		];
		const child = spawn("mainsequence", args, {
			stdio: "inherit",
			shell: false,
			env: process.env,
		});

		child.on("exit", (code) => {
			refreshInFlight = false;
			if (typeof code === "number" && code !== 0) {
				console.error(`[astro] mainsequence login failed (${reason}) with code ${code}.`);
			}
		});

		child.on("error", (error: NodeJS.ErrnoException) => {
			refreshInFlight = false;
			if (error.code === "ENOENT") {
				console.error("[astro] Missing required command: mainsequence");
				return;
			}
			console.error(`[astro] mainsequence login error (${reason}): ${error.message}`);
		});
	};

	console.log(
		`[astro] Starting Main Sequence token refresh loop every ${Math.floor(intervalMs / 1000)}s...`,
	);
	runLogin("startup");
	const timer = setInterval(() => runLogin("interval"), intervalMs);
	return {
		stop: () => clearInterval(timer),
	};
}

function main() {
	const repoRoot = findRepoRoot(process.cwd());
	loadEnvFile(repoRoot);
	const { agentName, cwd, projectId, task } = parseArgs(process.argv.slice(2));
	const discovery = discoverAgents(repoRoot, "project");
	const agent = discovery.agents.find((candidate) => candidate.name === agentName);

	if (!agent) {
		const known = discovery.agents.map((candidate) => candidate.name).sort().join(", ") || "none";
		fail(`Unknown specialist "${agentName}". Available project specialists: ${known}`);
	}

	if (agentName === "mainsequence-project-coder") {
		if (!cwd) {
			fail("mainsequence-project-coder requires --cwd pointing to the checked-out target project.");
		}
		if (!projectId) {
			fail("mainsequence-project-coder requires --project-id.");
		}
		const resolvedCwd = path.resolve(cwd);
		try {
			if (!fs.statSync(resolvedCwd).isDirectory()) {
				fail("mainsequence-project-coder requires --cwd pointing to an existing checked-out target project directory.");
			}
		} catch {
			fail("mainsequence-project-coder requires --cwd pointing to an existing checked-out target project directory.");
		}
	}

	const promptPath = writePromptToTempFile(agent);
	const args: string[] = [];

	if (agent.model) args.push("--model", agent.model);
	if (agent.tools?.length) args.push("--tools", agent.tools.join(","));
	args.push("--append-system-prompt", promptPath);
	if (task) args.push("-p", "--no-session", task);

	const refreshLoop = startMainsequenceRefreshLoop();
	const proc = spawn("pi", args, {
		cwd: cwd ? path.resolve(cwd) : repoRoot,
		stdio: "inherit",
		shell: false,
		env: {
			...process.env,
			ASTRO_SUBAGENT_CHILD: "1",
			ASTRO_ACTIVE_SPECIALIST: agent.name,
			...(projectId ? { ASTRO_TARGET_PROJECT_ID: projectId } : {}),
		},
	});

	proc.on("close", (code) => {
		cleanupPromptFile(promptPath);
		refreshLoop?.stop();
		process.exit(code ?? 0);
	});

	proc.on("error", (error: NodeJS.ErrnoException) => {
		cleanupPromptFile(promptPath);
		refreshLoop?.stop();
		if (error.code === "ENOENT") fail("Pi CLI is not installed or not on your PATH.");
		fail(error.message);
	});
}

main();
