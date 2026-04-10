import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const SAFE_HOST_ENTRIES = ["auth.json", "settings.json", "sessions"];

function ensureDir(dirPath) {
	fs.mkdirSync(dirPath, { recursive: true, mode: 0o700 });
}

function removePath(targetPath) {
	try {
		fs.rmSync(targetPath, { recursive: true, force: true });
	} catch {
		// ignore cleanup failures
	}
}

function ensureSymlink(sourcePath, targetPath) {
	const sourceStat = fs.statSync(sourcePath);
	const linkType = sourceStat.isDirectory() ? "dir" : "file";

	try {
		const existingStat = fs.lstatSync(targetPath);
		if (existingStat.isSymbolicLink() && fs.readlinkSync(targetPath) === sourcePath) {
			return false;
		}
		removePath(targetPath);
	} catch {
		// target does not exist yet
	}

	fs.symlinkSync(sourcePath, targetPath, linkType);
	return true;
}

export function bootstrapPiAgentDir() {
	const homeDir = process.env.HOME || os.homedir();
	const targetDir = process.env.PI_CODING_AGENT_DIR || path.join(homeDir, ".pi", "agent");
	const hostImportDir = process.env.ASTRO_PI_HOST_AGENT_IMPORT_DIR?.trim();

	ensureDir(targetDir);
	ensureDir(path.join(targetDir, "bin"));

	if (!hostImportDir || !fs.existsSync(hostImportDir)) {
		return {
			targetDir,
			hostImportDir: null,
			importedEntries: [],
		};
	}

	const importedEntries = [];

	for (const entry of SAFE_HOST_ENTRIES) {
		const sourcePath = path.join(hostImportDir, entry);
		if (!fs.existsSync(sourcePath)) continue;

		const targetPath = path.join(targetDir, entry);
		ensureDir(path.dirname(targetPath));

		if (ensureSymlink(sourcePath, targetPath)) {
			importedEntries.push(entry);
		}
	}

	return {
		targetDir,
		hostImportDir,
		importedEntries,
	};
}
