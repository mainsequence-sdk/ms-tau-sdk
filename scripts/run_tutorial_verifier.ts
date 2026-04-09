import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const promptTemplatePath = path.join(repoRoot, "pi", "prompts", "verify-mainsequence-tutorial.md");

function fail(message: string): never {
	console.error(`\n[astro] ${message}`);
	process.exit(1);
}

function run(command: string, args: string[], options: { cwd?: string; stdio?: "inherit" | "pipe" } = {}) {
	const result = spawnSync(command, args, {
		cwd: options.cwd ?? repoRoot,
		stdio: options.stdio ?? "inherit",
	});

	if (result.error) {
		const error = result.error as { code?: string; message: string };
		if (error.code === "ENOENT") {
			fail(`Missing required command: ${command}`);
		}

		fail(error.message);
	}

	if ((result.status ?? 1) !== 0) {
		process.exit(result.status ?? 1);
	}

	return result;
}

function hasPiWebAccess() {
	return existsSync(
		path.join(repoRoot, ".pi", "npm", "node_modules", "pi-web-access", "package.json"),
	);
}

function ensurePiCli() {
	const result = spawnSync("pi", ["--version"], {
		cwd: repoRoot,
		stdio: "ignore",
	});

	const error = result.error as { code?: string } | undefined;
	if (error?.code === "ENOENT") {
		fail("Pi CLI is not installed or not on your PATH.");
	}

	if (result.status !== 0) {
		fail("Pi CLI is installed, but `pi --version` did not succeed.");
	}
}

function stripFrontmatter(template: string): string {
	return template.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "").trim();
}

function expandTemplate(templateBody: string, args: string[]): string {
	let expanded = templateBody;
	const allArgs = args.join(" ").trim();

	expanded = expanded.replace(/\$ARGUMENTS|\$@/g, allArgs);
	expanded = expanded.replace(/\$\{@:([0-9]+)\}/g, (_match, startText) => {
		const start = Number.parseInt(startText, 10);
		if (!Number.isFinite(start) || start < 1) return "";
		return args.slice(start - 1).join(" ");
	});
	expanded = expanded.replace(/\$\{@:([0-9]+):([0-9]+)\}/g, (_match, startText, lengthText) => {
		const start = Number.parseInt(startText, 10);
		const length = Number.parseInt(lengthText, 10);
		if (!Number.isFinite(start) || start < 1 || !Number.isFinite(length) || length < 0) return "";
		return args.slice(start - 1, start - 1 + length).join(" ");
	});
	expanded = expanded.replace(/\$([1-9][0-9]*)/g, (_match, indexText) => {
		const index = Number.parseInt(indexText, 10);
		return args[index - 1] ?? "";
	});

	return expanded.trim();
}

function main() {
	ensurePiCli();

	if (!hasPiWebAccess()) {
		console.log("[astro] Installing pi-web-access for this repo...");
		run("pi", ["install", "npm:pi-web-access", "-l"]);
	}

	if (!existsSync(promptTemplatePath)) {
		fail(`Missing tutorial verifier prompt template: ${promptTemplatePath}`);
	}

	const extraArgs = process.argv.slice(2);
	const template = readFileSync(promptTemplatePath, "utf8");
	const prompt = expandTemplate(stripFrontmatter(template), extraArgs);

	console.log("[astro] Running TypeScript check...");
	run("npm", ["run", "check"]);

	console.log("[astro] Running tutorial verifier...");
	run("pi", ["-p", "--no-session", prompt]);
}

main();
