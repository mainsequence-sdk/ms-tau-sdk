import * as fs from "node:fs";
import * as path from "node:path";

export function findRepoRoot(startCwd: string): string {
	let current = path.resolve(startCwd);

	while (true) {
		const looksLikeRepoRoot =
			(fs.existsSync(path.join(current, ".pi")) && fs.existsSync(path.join(current, "extensions"))) ||
			(fs.existsSync(path.join(current, "package.json")) && fs.existsSync(path.join(current, "docs")));

		if (looksLikeRepoRoot) return current;

		const parent = path.dirname(current);
		if (parent === current) return path.resolve(startCwd);
		current = parent;
	}
}

export function safeReadFile(filePath: string): string | undefined {
	try {
		return fs.readFileSync(filePath, "utf8");
	} catch {
		return undefined;
	}
}

export function truncateMiddle(text: string, maxChars: number): string {
	if (text.length <= maxChars) return text;
	if (maxChars < 32) return text.slice(0, maxChars);

	const head = Math.floor(maxChars * 0.65);
	const tail = Math.floor(maxChars * 0.25);
	return `${text.slice(0, head)}\n\n...[truncated]...\n\n${text.slice(text.length - tail)}`;
}

export function normalizeFilePath(repoRoot: string, rawPath: string): string {
	const resolved = path.resolve(repoRoot, rawPath);
	const relative = path.relative(repoRoot, resolved);
	return relative && !relative.startsWith("..") ? relative.replaceAll("\\", "/") : resolved.replaceAll("\\", "/");
}
