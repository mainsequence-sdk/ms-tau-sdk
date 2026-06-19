export type WarmRunnerTimeoutConfig = {
	idleTtlMs: number;
	rpcCommandTimeoutMs: number;
	startupTimeoutMs: number;
	turnTimeoutMs: number;
};

function resolveTimeoutMs(value: string | undefined, fallbackMs: number): number {
	const configured = Number(value ?? String(fallbackMs));
	return Number.isFinite(configured) && configured >= 1000 ? Math.trunc(configured) : fallbackMs;
}

export function resolveWarmRunnerTimeoutConfig(
	env: NodeJS.ProcessEnv = process.env,
): WarmRunnerTimeoutConfig {
	return {
		idleTtlMs: resolveTimeoutMs(env.ASTRO_A2A_WARM_RUNNER_IDLE_TTL_MS, 3600000),
		rpcCommandTimeoutMs: resolveTimeoutMs(env.ASTRO_A2A_WARM_RUNNER_RPC_TIMEOUT_MS, 10000),
		startupTimeoutMs: resolveTimeoutMs(env.ASTRO_A2A_WARM_RUNNER_STARTUP_TIMEOUT_MS, 120000),
		turnTimeoutMs: resolveTimeoutMs(env.ASTRO_A2A_WARM_RUNNER_TURN_TIMEOUT_MS, 120000),
	};
}
