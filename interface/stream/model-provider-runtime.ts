import { homedir } from "node:os";
import path from "node:path";

export function resolvePiAgentDir(env: NodeJS.ProcessEnv = process.env): string {
	const configured = env.PI_CODING_AGENT_DIR?.trim();
	if (configured === "~") return homedir();
	if (configured?.startsWith("~/")) return path.join(homedir(), configured.slice(2));
	if (configured) return path.resolve(configured);
	return path.join(homedir(), ".pi", "agent");
}

export function withScopedPiAgentDir<T>(env: NodeJS.ProcessEnv, fn: () => T): T {
	const originalPiAgentDir = process.env.PI_CODING_AGENT_DIR;
	const scopedPiAgentDir = env.PI_CODING_AGENT_DIR;
	if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
		process.env.PI_CODING_AGENT_DIR = scopedPiAgentDir;
	}
	try {
		return fn();
	} finally {
		if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
			if (originalPiAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
			else process.env.PI_CODING_AGENT_DIR = originalPiAgentDir;
		}
	}
}

export async function withScopedPiAgentDirAsync<T>(
	env: NodeJS.ProcessEnv,
	fn: () => Promise<T>,
): Promise<T> {
	const originalPiAgentDir = process.env.PI_CODING_AGENT_DIR;
	const scopedPiAgentDir = env.PI_CODING_AGENT_DIR;
	if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
		process.env.PI_CODING_AGENT_DIR = scopedPiAgentDir;
	}
	try {
		return await fn();
	} finally {
		if (scopedPiAgentDir && scopedPiAgentDir !== originalPiAgentDir) {
			if (originalPiAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
			else process.env.PI_CODING_AGENT_DIR = originalPiAgentDir;
		}
	}
}
