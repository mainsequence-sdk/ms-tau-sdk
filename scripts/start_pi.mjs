import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const MAINSEQUENCE_REFRESH_INTERVAL_ENV = "MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS";
const MAINSEQUENCE_REQUIRED_ENV = [
	"MAINSEQUENCE_ACCESS_TOKEN",
	"MAINSEQUENCE_REFRESH_TOKEN",
	"MAINSEQUENCE_BACKEND",
	"MAINSEQUENCE_PROJECTS_BASE",
];

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

function loadEnvFile() {
	const envPath = path.join(repoRoot, ".env");
	if (!existsSync(envPath)) return;
	const contents = readFileSync(envPath, "utf8");
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

function getRefreshIntervalMs() {
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
	const runLogin = (reason) => {
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

		child.on("error", (error) => {
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

function hasLocalNpmDeps() {
	return existsSync(path.join(repoRoot, "node_modules", ".bin", "tsx"));
}

function hasRepoPiWebAccess() {
	return existsSync(path.join(repoRoot, "node_modules", "pi-web-access", "package.json"));
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
	loadEnvFile();
	ensureNodeVersion();

	if (!hasLocalNpmDeps()) {
		console.log("[astro] Installing local npm dependencies...");
		run("npm", ["install"]);
	}

	if (!hasRepoPiWebAccess()) {
		console.log("[astro] Installing missing repo npm dependencies...");
		run("npm", ["install"]);
	}

	ensurePiCli();

	console.log("[astro] Running TypeScript check...");
	run("npm", ["run", "check"]);

	const refreshLoop = startMainsequenceRefreshLoop();

	console.log("[astro] Starting Pi...");
	const child = spawn("pi", [], {
		cwd: repoRoot,
		stdio: "inherit",
		shell: false,
		env: process.env,
	});

	child.on("exit", (code) => {
		refreshLoop?.stop();
		process.exit(code ?? 0);
	});

	child.on("error", (error) => {
		refreshLoop?.stop();
		if (error.code === "ENOENT") {
			fail("Pi CLI is not installed or not on your PATH.");
		}
		fail(error.message);
	});
}

main();
