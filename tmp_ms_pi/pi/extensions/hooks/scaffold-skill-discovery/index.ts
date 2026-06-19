import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, statSync } from "node:fs";
import { join } from "node:path";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

type Env = Record<string, string | undefined>;

const DISABLE_ENV = "MAINSEQUENCE_PI_DISABLE_SKILL_DISCOVERY";
const COMMAND_BIN_ENV = "MAINSEQUENCE_PI_SKILL_COPY_BIN";

function isDisabled(env: Env): boolean {
	const value = env[DISABLE_ENV]?.trim().toLowerCase();
	return value === "1" || value === "true" || value === "yes";
}

function skillCopyCommand(env: Env): string {
	return env[COMMAND_BIN_ENV]?.trim() || "mainsequence";
}

function summarize(text: string): string {
	const trimmed = text.trim();
	if (trimmed.length <= 4000) return trimmed;
	return `${trimmed.slice(0, 4000)}...`;
}

function quoteArg(value: string): string {
	if (/^[a-zA-Z0-9_./:=@+-]+$/.test(value)) return value;
	return JSON.stringify(value);
}

async function runMainsequence(args: string[], cwd: string): Promise<string> {
	const command = skillCopyCommand(process.env);
	const result = await new Promise<{
		code: number | null;
		stdout: string;
		stderr: string;
		error?: string;
	}>((resolve) => {
		const child = spawn(command, args, {
			cwd,
			env: process.env,
			stdio: ["ignore", "pipe", "pipe"],
			shell: false,
		});

		let stdout = "";
		let stderr = "";
		let settled = false;

		child.stdout?.on("data", (chunk) => {
			stdout += String(chunk);
		});

		child.stderr?.on("data", (chunk) => {
			stderr += String(chunk);
		});

		child.on("error", (error) => {
			if (settled) return;
			settled = true;
			resolve({
				code: null,
				stdout: summarize(stdout),
				stderr: summarize(stderr),
				error: error.message,
			});
		});

		child.on("close", (code) => {
			if (settled) return;
			settled = true;
			resolve({
				code,
				stdout: summarize(stdout),
				stderr: summarize(stderr),
			});
		});
	});

	if (result.code === 0 && !result.error) return result.stdout;

	const commandText = [command, ...args].map(quoteArg).join(" ");
	const details = [
		`Main Sequence SDK skill discovery failed while running: ${commandText}`,
		result.error ? `error=${result.error}` : null,
		`exit_code=${result.code ?? "unknown"}`,
		result.stderr ? `stderr=${result.stderr}` : null,
		result.stdout ? `stdout=${result.stdout}` : null,
	].filter(Boolean);

	throw new Error(details.join(" | "));
}

function lastNonEmptyLine(text: string): string {
	return text
		.split(/\r?\n/)
		.map((line) => line.trim())
		.filter(Boolean)
		.at(-1) ?? "";
}

async function resolveSdkSkillRoot(cwd: string): Promise<string> {
	const sourceRoot = lastNonEmptyLine(await runMainsequence(["skills", "path"], cwd));
	if (!sourceRoot) {
		throw new Error("Main Sequence SDK skill discovery failed: `mainsequence skills path` returned no path.");
	}
	if (!existsSync(sourceRoot) || !statSync(sourceRoot).isDirectory()) {
		throw new Error(`Main Sequence SDK skill discovery failed: ${sourceRoot} is not a directory.`);
	}
	return sourceRoot;
}

async function copySdkSkills(cwd: string): Promise<string> {
	const skillRoot = join(cwd, ".agents", "skills");
	const sourceRoot = await resolveSdkSkillRoot(cwd);

	mkdirSync(skillRoot, { recursive: true });
	cpSync(sourceRoot, join(skillRoot, "mainsequence"), {
		recursive: true,
		force: true,
	});

	return skillRoot;
}

function existingSkillRoot(cwd: string): string | null {
	const skillRoot = join(cwd, ".agents", "skills");
	if (!existsSync(skillRoot)) return null;
	if (!statSync(skillRoot).isDirectory()) {
		throw new Error(`Main Sequence skill discovery cannot use ${skillRoot}: path exists but is not a directory.`);
	}
	return skillRoot;
}

export default function (pi: ExtensionAPI) {
	pi.on("resources_discover", async (event) => {
		if (isDisabled(process.env)) return;

		const existing = existingSkillRoot(event.cwd);
		if (existing) return;

		const created = await copySdkSkills(event.cwd);

		return {
			skillPaths: [created],
		};
	});
}
