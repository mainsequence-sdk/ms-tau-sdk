import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

function fail(message) {
	console.error(`\n[astro] ${message}`);
	process.exit(1);
}

function run(command, args, options = {}) {
	const result = spawnSync(command, args, {
		cwd: repoRoot,
		stdio: "inherit",
		...options,
	});

	if (result.error) {
		if (result.error.code === "ENOENT") {
			fail(`Missing required command: ${command}`);
		}

		fail(result.error.message);
	}

	if (result.status !== 0) {
		process.exit(result.status ?? 1);
	}
}

function hasLocalNpmDeps() {
	return existsSync(path.join(repoRoot, "node_modules", ".bin", "tsx"));
}

function hasPiWebAccess() {
	return existsSync(
		path.join(repoRoot, ".pi", "npm", "node_modules", "pi-web-access", "package.json"),
	);
}

function ensureNodeVersion() {
	const major = Number.parseInt(process.versions.node.split(".")[0], 10);

	if (!Number.isFinite(major) || major < 20) {
		fail(`Node 20+ is required. Current version: ${process.version}`);
	}
}

function ensurePiCli() {
	const result = spawnSync("pi", ["--version"], {
		cwd: repoRoot,
		stdio: "ignore",
	});

	if (result.error?.code === "ENOENT") {
		fail("Pi CLI is not installed or not on your PATH.");
	}

	if (result.status !== 0) {
		fail("Pi CLI is installed, but `pi --version` did not succeed.");
	}
}

function main() {
	ensureNodeVersion();

	if (!hasLocalNpmDeps()) {
		console.log("[astro] Installing local npm dependencies...");
		run("npm", ["install"]);
	}

	ensurePiCli();

	if (!hasPiWebAccess()) {
		console.log("[astro] Installing pi-web-access for this repo...");
		run("pi", ["install", "npm:pi-web-access", "-l"]);
	}

	console.log("[astro] Refreshing generated repo context...");
	run("npm", ["run", "docs:index"]);

	console.log("[astro] Running TypeScript check...");
	run("npm", ["run", "check"]);

	console.log("[astro] Starting Pi...");
	run("pi", []);
}

main();
