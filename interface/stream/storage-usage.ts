import { existsSync, lstatSync, readdirSync, realpathSync, statfsSync, type Dirent } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");

type StorageBucketName = "pi" | "astro" | "sessions" | "mainsequence" | "system";
type StorageCapacitySource = "filesystem" | "simulated";

type StorageScanError = {
	path: string;
	operation: "readdir" | "lstat";
	code: string | null;
	message: string;
	bucket: StorageBucketName | null;
};

export type StorageUsageResponse = {
	version: 1;
	root: string;
	capacitySource: StorageCapacitySource;
	totalBytes: number;
	availableBytes: number;
	filesystemUsedBytes: number;
	filesystemUsagePercent: number | null;
	consumedBytes: number;
	consumedPercentOfTotal: number | null;
	detail: Record<StorageBucketName, { bytes: number }>;
	scanComplete: boolean;
	scanErrors: StorageScanError[];
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
	if (
		normalizedPath === "mainsequence" ||
		normalizedPath.startsWith("mainsequence/") ||
		normalizedPath === "mainsequence-dev" ||
		normalizedPath.startsWith("mainsequence-dev/") ||
		normalizedPath === ".config" ||
		normalizedPath.startsWith(".config/")
	) {
		return "mainsequence";
	}
	if (
		normalizedPath === ".pi" ||
		normalizedPath.startsWith(".pi/") ||
		normalizedPath === ".pi/agent" ||
		normalizedPath.startsWith(".pi/agent/")
	) {
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
	scanErrors: StorageScanError[],
): void {
	let entries: Dirent[];
	try {
		entries = readdirSync(currentDir, { withFileTypes: true });
	} catch (error) {
		const relativePath = path.relative(rootDir, currentDir);
		scanErrors.push(buildStorageScanError({
			error,
			operation: "readdir",
			path: currentDir,
			bucket: relativePath ? classifyStorageBucket(relativePath) : null,
		}));
		return;
	}

	for (const entry of entries) {
		const entryPath = path.join(currentDir, entry.name);
		let stats: ReturnType<typeof lstatSync>;
		try {
			stats = lstatSync(entryPath);
		} catch (error) {
			const relativePath = path.relative(rootDir, entryPath);
			scanErrors.push(buildStorageScanError({
				error,
				operation: "lstat",
				path: entryPath,
				bucket: relativePath ? classifyStorageBucket(relativePath) : null,
			}));
			continue;
		}
		if (entry.isDirectory()) {
			walkStorageTree(rootDir, entryPath, bucketSizes, scanErrors);
			continue;
		}

		const relativePath = path.relative(rootDir, entryPath);
		if (!relativePath) continue;
		const bucket = classifyStorageBucket(relativePath);
		bucketSizes[bucket] += stats.size;
	}
}

function buildStorageScanError(options: {
	error: unknown;
	operation: StorageScanError["operation"];
	path: string;
	bucket: StorageBucketName | null;
}): StorageScanError {
	const nodeError = options.error as NodeJS.ErrnoException;
	return {
		path: options.path,
		operation: options.operation,
		code: typeof nodeError?.code === "string" ? nodeError.code : null,
		message: options.error instanceof Error ? options.error.message : String(options.error),
		bucket: options.bucket,
	};
}

function normalizePercent(numerator: number, denominator: number): number | null {
	if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) {
		return null;
	}
	return Number(((numerator / denominator) * 100).toFixed(2));
}

function resolveSimulatedTotalBytes(env: NodeJS.ProcessEnv): number | null {
	const rawValue = env.ASTRO_STORAGE_SIM_TOTAL_BYTES?.trim();
	if (!rawValue) return null;

	const parsed = Number(rawValue);
	if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed <= 0) {
		throw new Error(
			`ASTRO_STORAGE_SIM_TOTAL_BYTES must be a positive integer number of bytes. Received: ${rawValue}`,
		);
	}

	return parsed;
}

export function readStorageUsage(env: NodeJS.ProcessEnv = process.env): StorageUsageResponse {
	const root = resolveStorageRoot(env);
	const statfs = statfsSync(root);
	const filesystemTotalBytes = statfs.blocks * statfs.bsize;
	const filesystemAvailableBytes = statfs.bavail * statfs.bsize;

	const bucketSizes: Record<StorageBucketName, number> = {
		pi: 0,
		astro: 0,
		sessions: 0,
		mainsequence: 0,
		system: 0,
	};
	const scanErrors: StorageScanError[] = [];
	walkStorageTree(root, root, bucketSizes, scanErrors);

	const consumedBytes = Object.values(bucketSizes).reduce((sum, value) => sum + value, 0);
	const simulatedTotalBytes = resolveSimulatedTotalBytes(env);
	const capacitySource: StorageCapacitySource = simulatedTotalBytes ? "simulated" : "filesystem";
	const totalBytes = simulatedTotalBytes ?? filesystemTotalBytes;
	const filesystemUsedBytes = simulatedTotalBytes
		? Math.min(consumedBytes, simulatedTotalBytes)
		: Math.max(filesystemTotalBytes - filesystemAvailableBytes, 0);
	const availableBytes = simulatedTotalBytes
		? Math.max(simulatedTotalBytes - filesystemUsedBytes, 0)
		: filesystemAvailableBytes;

	return {
		version: 1,
		root,
		capacitySource,
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
			mainsequence: { bytes: bucketSizes.mainsequence },
			system: { bytes: bucketSizes.system },
		},
		scanComplete: scanErrors.length === 0,
		scanErrors,
		capturedAt: new Date().toISOString(),
	};
}
