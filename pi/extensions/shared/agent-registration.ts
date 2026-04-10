import { spawn } from "node:child_process";

type AgentRole = "orchestrator" | "specialist";

type RegistrationResult = {
	ok: boolean;
	exitCode: number | null;
	stdout: string;
	stderr: string;
};

type RegistrationOptions = {
	agentName: string;
	agentRole: AgentRole;
	cwd: string;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
};

const UNKNOWN_OPTION = /unknown option|no such option|unrecognized option/i;

function sanitizeId(value: string): string {
	return value.trim().replace(/\s+/g, "_");
}

function runMainsequence(
	args: string[],
	options: { cwd: string; env?: NodeJS.ProcessEnv },
): Promise<RegistrationResult> {
	return new Promise((resolve) => {
		const proc = spawn("mainsequence", args, {
			cwd: options.cwd,
			env: options.env,
			stdio: ["ignore", "pipe", "pipe"],
			shell: false,
		});

		let stdout = "";
		let stderr = "";

		proc.stdout.on("data", (chunk) => {
			stdout += String(chunk);
		});
		proc.stderr.on("data", (chunk) => {
			stderr += String(chunk);
		});

		proc.on("error", (error) => {
			resolve({
				ok: false,
				exitCode: null,
				stdout,
				stderr: `${stderr}${error.message}`,
			});
		});

		proc.on("close", (code) => {
			resolve({
				ok: code === 0,
				exitCode: code,
				stdout,
				stderr,
			});
		});
	});
}

async function resolveMainsequenceUserId(options: {
	cwd: string;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): Promise<string | null> {
	const { cwd, env, log } = options;
	const jsonResult = await runMainsequence(["user", "--json"], { cwd, env });
	if (jsonResult.ok) {
		try {
			const parsed = JSON.parse(jsonResult.stdout);
			const id =
				parsed?.id ??
				parsed?.user?.id ??
				parsed?.user_id ??
				parsed?.userId ??
				parsed?.data?.id ??
				parsed?.data?.user?.id;
			if (id !== undefined && id !== null) {
				return String(id);
			}
		} catch {
			log?.("Failed to parse JSON from `mainsequence user --json`.");
		}
	} else if (jsonResult.stderr.trim()) {
		log?.(`Failed to read user id from \`mainsequence user --json\`: ${jsonResult.stderr.trim()}`);
	}

	log?.("Could not resolve Main Sequence user id from `mainsequence user --json`.");
	return null;
}

export async function registerMainsequenceAgent(options: RegistrationOptions): Promise<RegistrationResult> {
	const { agentName, agentRole, cwd, env, log } = options;
	const safeAgentName = sanitizeId(agentName);
	const userId = await resolveMainsequenceUserId({ cwd, env: env ?? process.env, log });
	const uniqueId = userId ? `${safeAgentName}_${userId}` : null;

	log?.(
		`Registering agent "${safeAgentName}" (${agentRole})${uniqueId ? ` with unique id "${uniqueId}"` : ""}.`,
	);

	const attempts: string[][] = [];
	if (uniqueId) {
		attempts.push(
			["agent", "get-or-create", "--name", safeAgentName, "--unique-id", uniqueId],
			["agent", "get-or-create", "--name", safeAgentName, "--unique_id", uniqueId],
			["agent", "get-or-create", safeAgentName, "--unique-id", uniqueId],
		);
	}
	attempts.push(["agent", "get-or-create", "--name", safeAgentName], ["agent", "get-or-create", safeAgentName]);

	let lastResult: RegistrationResult | null = null;
	for (const args of attempts) {
		const result = await runMainsequence(args, { cwd, env: env ?? process.env });
		lastResult = result;
		if (result.ok) return result;
		if (!UNKNOWN_OPTION.test(result.stderr)) break;
		log?.("Retrying agent registration with alternate CLI arguments.");
	}

	if (lastResult && !lastResult.ok) {
		const reason =
			lastResult.exitCode === null
				? "failed to spawn mainsequence CLI"
				: `exit code ${lastResult.exitCode}`;
		log?.(`Agent registration failed (${reason}).`);
		if (lastResult.stderr.trim()) {
			log?.(`stderr: ${lastResult.stderr.trim()}`);
		}
		return lastResult;
	}

	return {
		ok: false,
		exitCode: null,
		stdout: "",
		stderr: "Agent registration did not run.",
	};
}
