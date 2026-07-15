import { mainsequenceBackendAdapter } from "./mainsequence/adapter.js";
import type { BackendAdapter } from "./types.js";

export const DEFAULT_BACKEND_ADAPTER_NAME = "mainsequence";

export function resolveBackendAdapter(env: NodeJS.ProcessEnv = process.env): BackendAdapter {
	const configured = env.ASTRO_BACKEND?.trim().toLowerCase() || DEFAULT_BACKEND_ADAPTER_NAME;
	if (configured === "mainsequence") return mainsequenceBackendAdapter;
	throw new Error(
		`Unsupported ASTRO_BACKEND "${configured}". Supported backends: ${DEFAULT_BACKEND_ADAPTER_NAME}.`,
	);
}
