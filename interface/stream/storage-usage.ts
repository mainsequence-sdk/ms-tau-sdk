import { existsSync, lstatSync, readdirSync, realpathSync, statfsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");

type StorageBucketName = "pi" | "astro" | "sessions" | "system";

export type StorageUsageResponse = {
	version: 1;
	root: string;
	totalBytes: number;
	availableBytes: number;
	filesystemUsedBytes: number;
	filesystemUsagePercent: number | null;
	consumedBytes: number;
	consumedPercentOfTotal: number | null;
	detail: Record<StorageBucketName, { bytes: number }>;
	capturedAt: string;
};

function resolveStorageRoot(env: NodeJS.ProcessEnv): string {
	const configuredRoot = env.ASTRO_CONTAINER_DATA_DIR?.trim();
	const candidateRoot = configuredRoot ? path.resolve(configuredRoot) : path.join(repoRoot, ".astro");
	if (!existsSync(candidateRoot)) {
		throw new Error(`Storage root does not exist: ${candidateRoot}`);
	}
	return realpathSync(candidateRoot);
}

function normalizeRelativePath(relativePath: string): string {
	return relativePath.split(path.sep).join("/");
}

function classifyStorageBucket(relativePath: string): StorageBucketName {
	const normalizedPath = normalizeRelativePath(relativePath);
	if (normalizedPath === ".pi/agent/sessions" || normalizedPath.startsWith(".pi/agent/sessions/")) {
		return "sessions";
	}
	if (
		normalizedPath === ".astro/stream-sessions" ||
		normalizedPath.startsWith(".astro/stream-sessions/")
	) {
		return "sessions";
	}
	if (normalizedPath === ".pi/agent" || normalizedPath.startsWith(".pi/agent/")) {
		return "pi";
	}
	if (normalizedPath === ".astro" || normalizedPath.startsWith(".astro/")) {
		return "astro";
	}
	return "system";
}

function walkStorageTree(
	rootDir: string,
	currentDir: string,
	bucketSizes: Record<StorageBucketName, number>,
): void {
	for (const entry of readdirSync(currentDir, { withFileTypes: true })) {
		const entryPath = path.join(currentDir, entry.name);
		const stats = lstatSync(entryPath);
		if (entry.isDirectory()) {
			walkStorageTree(rootDir, entryPath, bucketSizes);
			continue;
		}

		const relativePath = path.relative(rootDir, entryPath);
		if (!relativePath) continue;
		const bucket = classifyStorageBucket(relativePath);
		bucketSizes[bucket] += stats.size;
	}
}

function normalizePercent(numerator: number, denominator: number): number | null {
	if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) {
		return null;
	}
	return Number(((numerator / denominator) * 100).toFixed(2));
}

export function readStorageUsage(env: NodeJS.ProcessEnv = process.env): StorageUsageResponse {
	const root = resolveStorageRoot(env);
	const statfs = statfsSync(root);
	const totalBytes = statfs.blocks * statfs.bsize;
	const availableBytes = statfs.bavail * statfs.bsize;
	const filesystemUsedBytes = Math.max(totalBytes - availableBytes, 0);

	const bucketSizes: Record<StorageBucketName, number> = {
		pi: 0,
		astro: 0,
		sessions: 0,
		system: 0,
	};
	walkStorageTree(root, root, bucketSizes);

	const consumedBytes = Object.values(bucketSizes).reduce((sum, value) => sum + value, 0);

	return {
		version: 1,
		root,
		totalBytes,
		availableBytes,
		filesystemUsedBytes,
		filesystemUsagePercent: normalizePercent(filesystemUsedBytes, totalBytes),
		consumedBytes,
		consumedPercentOfTotal: normalizePercent(consumedBytes, totalBytes),
		detail: {
			pi: { bytes: bucketSizes.pi },
			astro: { bytes: bucketSizes.astro },
			sessions: { bytes: bucketSizes.sessions },
			system: { bytes: bucketSizes.system },
		},
		capturedAt: new Date().toISOString(),
	};
}
