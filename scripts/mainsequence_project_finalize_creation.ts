import { copyFileSync, existsSync, lstatSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { buildProjectScopedProcessEnv, ExitCodeError, extractProjectId, setupProjectLocally } from "./mainsequence_project_local_setup.js";

const DEFAULT_COMMIT_MESSAGE = "chore(mainsequence): add project blueprint";

type ParsedArgs = {
	setupArgs: string[];
	projectId: string;
	sourceBlueprintPath: string;
	commitMessage: string;
};

function fail(message: string): never {
	console.error(`[astro] ${message}`);
	process.exit(1);
}

function parseArgs(argv: string[]): ParsedArgs {
	const setupArgs: string[] = [];
	let sourceBlueprintPath = "project_blueprint.md";
	let commitMessage = DEFAULT_COMMIT_MESSAGE;

	for (let index = 0; index < argv.length; index += 1) {
		const arg = argv[index];
		if (!arg) continue;

		if (arg === "--blueprint") {
			const value = argv[index + 1]?.trim();
			if (!value) fail("Missing value for --blueprint.");
			sourceBlueprintPath = value;
			index += 1;
			continue;
		}

		if (arg === "--commit-message") {
			const value = argv[index + 1];
			if (!value?.trim()) fail("Missing value for --commit-message.");
			commitMessage = value.trim();
			index += 1;
			continue;
		}

		setupArgs.push(arg);
	}

	const projectId = extractProjectId(setupArgs);
	if (!projectId) {
		fail(
			"Usage: tsx /app/scripts/mainsequence_project_finalize_creation.ts <project-id> [--base-dir <dir>] [--scaffold-docker|--no-scaffold-docker] [--blueprint <path>] [--commit-message <text>]",
		);
	}

	return {
		setupArgs,
		projectId,
		sourceBlueprintPath: path.resolve(process.cwd(), sourceBlueprintPath),
		commitMessage,
	};
}

function ensureReadableFile(filePath: string, label: string) {
	if (!existsSync(filePath)) {
		fail(`Missing ${label}: ${filePath}`);
	}
	try {
		const stat = lstatSync(filePath);
		if (!stat.isFile()) {
			fail(`${label} is not a regular file: ${filePath}`);
		}
	} catch (error) {
		fail(error instanceof Error ? error.message : String(error));
	}
}

function runCommand(
	command: string,
	args: string[],
	options: { cwd: string; env: NodeJS.ProcessEnv; allowNonZero?: boolean } ,
) {
	const result = spawnSync(command, args, {
		cwd: options.cwd,
		env: options.env,
		shell: false,
		encoding: "utf8",
	});
	if (typeof result.stdout === "string" && result.stdout.length > 0) process.stdout.write(result.stdout);
	if (typeof result.stderr === "string" && result.stderr.length > 0) process.stderr.write(result.stderr);

	if (result.error) {
		const error = result.error as NodeJS.ErrnoException;
		if (error.code === "ENOENT") {
			fail(`Missing required command: ${command}`);
		}
		throw error;
	}

	if (!options.allowNonZero && (result.status ?? 1) !== 0) {
		throw new ExitCodeError(result.status ?? 1);
	}

	return result;
}

function resolveCurrentBranch(cwd: string, env: NodeJS.ProcessEnv): string {
	const result = spawnSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
		cwd,
		env,
		shell: false,
		encoding: "utf8",
	});
	if (result.error) {
		const error = result.error as NodeJS.ErrnoException;
		if (error.code === "ENOENT") fail("Missing required command: git");
		throw result.error;
	}
	if ((result.status ?? 1) !== 0) {
		if (typeof result.stdout === "string" && result.stdout.length > 0) process.stdout.write(result.stdout);
		if (typeof result.stderr === "string" && result.stderr.length > 0) process.stderr.write(result.stderr);
		throw new ExitCodeError(result.status ?? 1);
	}
	return typeof result.stdout === "string" ? result.stdout.trim() : "";
}

function shellQuote(value: string): string {
	return `'${value.replace(/'/g, `'\\''`)}'`;
}

function hasBlueprintChanges(cwd: string, env: NodeJS.ProcessEnv): boolean {
	const result = spawnSync("git", ["status", "--porcelain", "--", "project_blueprint.md"], {
		cwd,
		env,
		shell: false,
		encoding: "utf8",
	});
	if (result.error) {
		const error = result.error as NodeJS.ErrnoException;
		if (error.code === "ENOENT") fail("Missing required command: git");
		throw result.error;
	}
	if ((result.status ?? 1) !== 0) {
		if (typeof result.stdout === "string" && result.stdout.length > 0) process.stdout.write(result.stdout);
		if (typeof result.stderr === "string" && result.stderr.length > 0) process.stderr.write(result.stderr);
		throw new ExitCodeError(result.status ?? 1);
	}
	return Boolean(typeof result.stdout === "string" && result.stdout.trim());
}

async function main() {
	const parsed = parseArgs(process.argv.slice(2));
	ensureReadableFile(parsed.sourceBlueprintPath, "project_blueprint.md source file");

	const setupResult = await setupProjectLocally(parsed.setupArgs, { streamOutput: true });
	if (!setupResult.checkoutDir) {
		fail(
			`Astro could not determine the checked-out project directory for project ${parsed.projectId} after local setup.`,
		);
	}

	const checkoutDir = setupResult.checkoutDir;
	const targetBlueprintPath = path.join(checkoutDir, "project_blueprint.md");
	if (path.resolve(parsed.sourceBlueprintPath) !== path.resolve(targetBlueprintPath)) {
		copyFileSync(parsed.sourceBlueprintPath, targetBlueprintPath);
		process.stdout.write(`[astro] Copied project_blueprint.md into ${targetBlueprintPath}.\n`);
	}

	const gitEnv = buildProjectScopedProcessEnv(setupResult.checkoutHome.projectHome);
	if (!hasBlueprintChanges(checkoutDir, gitEnv)) {
		process.stdout.write(
			`[astro] project_blueprint.md is already up to date in ${checkoutDir}; no signed-terminal commit is required.\n`,
		);
		return;
	}
	const branch = resolveCurrentBranch(checkoutDir, gitEnv);
	process.stdout.write(
		[
			`[astro] project_blueprint.md is ready in ${checkoutDir}.`,
			`[astro] Open a signed terminal before committing and pushing:`,
			`mainsequence project open-signed-terminal ${parsed.projectId}`,
			`[astro] Then run inside that signed terminal:`,
			`cd ${shellQuote(checkoutDir)}`,
			`git add -- project_blueprint.md`,
			`git commit -m ${shellQuote(parsed.commitMessage)}`,
			`git push origin ${shellQuote(branch)}`,
		].join("\n") + "\n",
	);
}

try {
	await main();
} catch (error) {
	if (error instanceof ExitCodeError) {
		process.exit(error.exitCode);
	}
	fail(error instanceof Error ? error.message : String(error));
}
