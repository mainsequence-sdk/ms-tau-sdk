import {
	existsSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	rmSync,
	symlinkSync,
	writeFileSync,
} from "node:fs";
import path from "node:path";
import { SettingsManager } from "../../node_modules/@mariozechner/pi-coding-agent/dist/core/settings-manager.js";
import { resolvePiAgentDir } from "./model-provider-runtime.js";
import type { PiCredential } from "./model-provider-credentials-client.js";
import type { SessionModelBinding } from "./session-model.js";

export type SessionConfigOverrides = {
	compaction?: {
		enabled?: boolean;
		reserveTokens?: number;
	};
};

export type SessionInsightsConfig = {
	compaction: {
		enabled: boolean;
		reserveTokens: number;
		thresholdTokens: number | null;
		thresholdPercent: number | null;
	};
	model: {
		provider: string | null;
		model: string | null;
		reasoningEffort: string | null;
		contextWindow: number;
		maxOutputTokens: number;
	};
};

export type SessionConfigFieldEditability = {
	editable: boolean;
	type: "boolean" | "integer" | "number" | "enum" | "string";
	min?: number;
	max?: number;
	step?: number;
	unit?: "tokens" | "percent";
	values?: string[];
};

export type SessionInsightsEditable = {
	config: {
		compaction: {
			enabled: SessionConfigFieldEditability;
			reserveTokens: SessionConfigFieldEditability;
			thresholdTokens: SessionConfigFieldEditability;
			thresholdPercent: SessionConfigFieldEditability;
		};
		model: {
			provider: SessionConfigFieldEditability;
			model: SessionConfigFieldEditability;
			reasoningEffort: SessionConfigFieldEditability;
			contextWindow: SessionConfigFieldEditability;
			maxOutputTokens: SessionConfigFieldEditability;
		};
	};
};

type SessionCompactionConstraint = {
	min: number;
	max: number;
	step: number;
};

type SessionConfigPatchSuccess = {
	ok: true;
	updatedFields: string[];
	overrides: SessionConfigOverrides | null;
};

type SessionConfigPatchError = {
	ok: false;
	statusCode: 400;
	error: "invalid_session_config";
	message: string;
};

type SessionConfigPatchResult = SessionConfigPatchSuccess | SessionConfigPatchError;
const SENSITIVE_PI_AGENT_ENTRIES = new Set([
	"auth.json",
	"oauth.json",
	"sessions",
	"astro-model-provider-auth.json",
	"astro-model-provider-signin.json",
]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeFiniteNumber(value: unknown): number | null {
	return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeInteger(value: unknown): number | null {
	const normalized = normalizeFiniteNumber(value);
	if (normalized == null || !Number.isInteger(normalized)) return null;
	return normalized;
}

function dedupeStrings(values: Array<string | null | undefined>): string[] {
	const deduped: string[] = [];
	for (const value of values) {
		if (!value || deduped.includes(value)) continue;
		deduped.push(value);
	}
	return deduped;
}

function deepMergeSettings(base: Record<string, unknown>, overrides: Record<string, unknown>): Record<string, unknown> {
	const merged: Record<string, unknown> = { ...base };
	for (const [key, value] of Object.entries(overrides)) {
		const existing = merged[key];
		if (isPlainObject(existing) && isPlainObject(value)) {
			merged[key] = deepMergeSettings(existing, value);
			continue;
		}
		merged[key] = value;
	}
	return merged;
}

function readJsonObject(filePath: string): Record<string, unknown> {
	if (!existsSync(filePath)) return {};
	try {
		const parsed = JSON.parse(readFileSync(filePath, "utf8"));
		return isPlainObject(parsed) ? parsed : {};
	} catch {
		return {};
	}
}

function hasOverrides(overrides: SessionConfigOverrides | null): boolean {
	if (!overrides?.compaction) return false;
	return overrides.compaction.enabled !== undefined || overrides.compaction.reserveTokens !== undefined;
}

function hasProviderCredentials(credentials: Record<string, PiCredential> | null | undefined): boolean {
	return Boolean(credentials && Object.keys(credentials).length > 0);
}

function sanitizeScopeKey(value: string): string {
	return value.trim().replace(/[^A-Za-z0-9_.:-]/g, "_");
}

function resolveProviderCredentialRoot(env: NodeJS.ProcessEnv): string {
	const configured = env.ASTRO_PROVIDER_CREDENTIAL_DIR?.trim();
	if (configured) return path.resolve(configured);

	const sessionStateDir = env.ASTRO_SESSION_STATE_DIR?.trim();
	if (sessionStateDir) return path.join(path.resolve(sessionStateDir), "pi-agent-auth");

	const streamSessionDir = env.ASTRO_STREAM_SESSION_DIR?.trim();
	if (streamSessionDir) return path.join(path.dirname(path.resolve(streamSessionDir)), "pi-agent-auth");

	return path.join(resolvePiAgentDir(env), ".astro-provider-auth");
}

export function normalizeSessionConfigOverrides(value: unknown): SessionConfigOverrides | null {
	if (!isPlainObject(value)) return null;

	const normalized: SessionConfigOverrides = {};
	if (isPlainObject(value.compaction)) {
		const compaction: NonNullable<SessionConfigOverrides["compaction"]> = {};
		if (value.compaction.enabled === true || value.compaction.enabled === false) {
			compaction.enabled = value.compaction.enabled;
		}
		const normalizedReserveTokens = normalizeInteger(value.compaction.reserveTokens);
		if (normalizedReserveTokens != null) {
			compaction.reserveTokens = normalizedReserveTokens;
		}
		if (Object.keys(compaction).length > 0) {
			normalized.compaction = compaction;
		}
	}

	return hasOverrides(normalized) ? normalized : null;
}

export function resolveSessionRuntimeLimits(sessionModelBinding: SessionModelBinding | null): {
	contextWindow: number;
	maxOutputTokens: number;
} {
	const metadata = sessionModelBinding?.metadata;
	const contextWindow = normalizeFiniteNumber(metadata?.contextWindow) ?? 128000;
	const maxOutputTokens =
		normalizeFiniteNumber(metadata?.maxOutputTokens) ??
		normalizeFiniteNumber(metadata?.maxTokens) ??
		16384;
	return {
		contextWindow,
		maxOutputTokens,
	};
}

export function getCompactionReserveTokensConstraint(contextWindow: number): SessionCompactionConstraint {
	return {
		min: 1024,
		max: Math.max(contextWindow - 1024, 1024),
		step: 1024,
	};
}

export function resolveEffectiveCompactionConfig(input: {
	cwd: string | null;
	sessionConfigOverrides: SessionConfigOverrides | null;
	contextWindow: number;
	agentDir?: string;
}): SessionInsightsConfig["compaction"] {
	const resolvedAgentDir = input.agentDir ?? process.env.PI_CODING_AGENT_DIR ?? undefined;
	const settingsManager = SettingsManager.create(
		input.cwd ?? process.cwd(),
		resolvedAgentDir,
	);
	const baseSettings = settingsManager.getCompactionSettings();
	const enabled = input.sessionConfigOverrides?.compaction?.enabled ?? baseSettings.enabled;
	const reserveTokens = input.sessionConfigOverrides?.compaction?.reserveTokens ?? baseSettings.reserveTokens;
	const thresholdTokens = enabled ? Math.max(input.contextWindow - reserveTokens, 0) : null;
	const thresholdPercent =
		thresholdTokens == null || input.contextWindow <= 0
			? null
			: Number(((thresholdTokens / input.contextWindow) * 100).toFixed(1));

	return {
		enabled,
		reserveTokens,
		thresholdTokens,
		thresholdPercent,
	};
}

export function buildSessionInsightsEditable(input: {
	sessionModelBinding: SessionModelBinding | null;
	contextWindow: number;
	maxOutputTokens: number;
}): SessionInsightsEditable {
	const reserveTokensConstraint = getCompactionReserveTokensConstraint(input.contextWindow);
	const reasoningCapability = input.sessionModelBinding?.capabilities.reasoning_effort ?? null;
	const reasoningValues =
		reasoningCapability == null
			? []
			: dedupeStrings([
					...reasoningCapability.values,
					input.sessionModelBinding?.runConfig.reasoning_effort ?? null,
			  ]);

	return {
		config: {
			compaction: {
				enabled: {
					editable: true,
					type: "boolean",
				},
				reserveTokens: {
					editable: true,
					type: "integer",
					min: reserveTokensConstraint.min,
					max: reserveTokensConstraint.max,
					step: reserveTokensConstraint.step,
					unit: "tokens",
				},
				thresholdTokens: {
					editable: false,
					type: "integer",
					unit: "tokens",
				},
				thresholdPercent: {
					editable: false,
					type: "number",
					unit: "percent",
				},
			},
			model: {
				provider: {
					editable: false,
					type: "string",
				},
				model: {
					editable: false,
					type: "string",
				},
				reasoningEffort:
					reasoningValues.length > 0
						? {
								editable: false,
								type: "enum",
								values: reasoningValues,
						  }
						: {
								editable: false,
								type: "string",
						  },
				contextWindow: {
					editable: false,
					type: "integer",
					unit: "tokens",
				},
				maxOutputTokens: {
					editable: false,
					type: "integer",
					unit: "tokens",
				},
			},
		},
	};
}

export function validateSessionConfigPatch(input: {
	body: unknown;
	currentOverrides: SessionConfigOverrides | null;
	contextWindow: number;
}): SessionConfigPatchResult {
	if (!isPlainObject(input.body)) {
		return {
			ok: false,
			statusCode: 400,
			error: "invalid_session_config",
			message: "Session config patch body must be a JSON object.",
		};
	}

	const config = input.body.config;
	if (!isPlainObject(config)) {
		return {
			ok: false,
			statusCode: 400,
			error: "invalid_session_config",
			message: "Session config patch requires a config object.",
		};
	}

	const unsupportedConfigKeys = Object.keys(config).filter((key) => key !== "compaction");
	if (unsupportedConfigKeys.length > 0) {
		return {
			ok: false,
			statusCode: 400,
			error: "invalid_session_config",
			message: `Unsupported session config section(s): ${unsupportedConfigKeys.join(", ")}.`,
		};
	}

	const compactionPatch = config.compaction;
	if (!isPlainObject(compactionPatch)) {
		return {
			ok: false,
			statusCode: 400,
			error: "invalid_session_config",
			message: "Session config patch currently supports config.compaction only.",
		};
	}

	const unsupportedCompactionKeys = Object.keys(compactionPatch).filter(
		(key) => key !== "enabled" && key !== "reserveTokens",
	);
	if (unsupportedCompactionKeys.length > 0) {
		return {
			ok: false,
			statusCode: 400,
			error: "invalid_session_config",
			message: `Unsupported compaction config field(s): ${unsupportedCompactionKeys.join(", ")}.`,
		};
	}

	const updatedFields: string[] = [];
	const nextCompactionOverrides: NonNullable<SessionConfigOverrides["compaction"]> = {
		...(input.currentOverrides?.compaction ?? {}),
	};

	if ("enabled" in compactionPatch) {
		if (compactionPatch.enabled !== true && compactionPatch.enabled !== false) {
			return {
				ok: false,
				statusCode: 400,
				error: "invalid_session_config",
				message: "config.compaction.enabled must be a boolean.",
			};
		}
		nextCompactionOverrides.enabled = compactionPatch.enabled;
		updatedFields.push("config.compaction.enabled");
	}

	if ("reserveTokens" in compactionPatch) {
		const reserveTokens = normalizeInteger(compactionPatch.reserveTokens);
		if (reserveTokens == null) {
			return {
				ok: false,
				statusCode: 400,
				error: "invalid_session_config",
				message: "config.compaction.reserveTokens must be an integer.",
			};
		}
		const constraint = getCompactionReserveTokensConstraint(input.contextWindow);
		if (reserveTokens < constraint.min || reserveTokens > constraint.max) {
			return {
				ok: false,
				statusCode: 400,
				error: "invalid_session_config",
				message: `config.compaction.reserveTokens must be between ${constraint.min} and ${constraint.max}.`,
			};
		}
		if (reserveTokens % constraint.step !== 0) {
			return {
				ok: false,
				statusCode: 400,
				error: "invalid_session_config",
				message: `config.compaction.reserveTokens must be a multiple of ${constraint.step}.`,
			};
		}
		nextCompactionOverrides.reserveTokens = reserveTokens;
		updatedFields.push("config.compaction.reserveTokens");
	}

	if (updatedFields.length === 0) {
		return {
			ok: false,
			statusCode: 400,
			error: "invalid_session_config",
			message: "No editable session config fields were provided.",
		};
	}

	const normalizedOverrides = normalizeSessionConfigOverrides({
		compaction: nextCompactionOverrides,
	});

	return {
		ok: true,
		updatedFields,
		overrides: normalizedOverrides,
	};
}

export function ensureSessionScopedPiAgentDir(options: {
	sessionKey: string;
	sessionConfigOverrides: SessionConfigOverrides | null;
	providerCredentials?: Record<string, PiCredential> | null;
	forceProviderAuthDir?: boolean;
	sessionSkillPaths?: string[];
	env?: NodeJS.ProcessEnv;
}): string | null {
	const hasConfigOverrides = hasOverrides(options.sessionConfigOverrides);
	const hasCredentials = hasProviderCredentials(options.providerCredentials);
	const sessionSkillPaths = dedupeStrings(options.sessionSkillPaths ?? []);
	const hasSessionSkillPaths = sessionSkillPaths.length > 0;
	const forceProviderAuthDir = options.forceProviderAuthDir === true;
	if (!hasConfigOverrides && !hasCredentials && !hasSessionSkillPaths && !forceProviderAuthDir) return null;

	const env = options.env ?? process.env;
	const baseAgentDir = resolvePiAgentDir(env);
	const overlayRoot = hasCredentials || forceProviderAuthDir
		? resolveProviderCredentialRoot(env)
		: env.ASTRO_SESSION_OVERRIDES_DIR?.trim() || path.join(baseAgentDir, ".astro-session-overrides");
	const overlayDir = path.join(overlayRoot, sanitizeScopeKey(options.sessionKey));
	const overlaySettingsPath = path.join(overlayDir, "settings.json");
	const baseSettingsPath = path.join(baseAgentDir, "settings.json");

	mkdirSync(overlayDir, { recursive: true, mode: 0o700 });

	const baseSettings = readJsonObject(baseSettingsPath);
	const overrideSettings: Record<string, unknown> = {};
	if (options.sessionConfigOverrides?.compaction) {
		overrideSettings.compaction = {
			...options.sessionConfigOverrides.compaction,
		};
	}
	if (hasSessionSkillPaths) {
		const baseSkillPaths = Array.isArray(baseSettings.skills)
			? baseSettings.skills.filter((value): value is string => typeof value === "string" && value.trim().length > 0)
			: [];
		overrideSettings.skills = dedupeStrings([...baseSkillPaths, ...sessionSkillPaths]);
	}
	const mergedSettings = deepMergeSettings(baseSettings, overrideSettings);
	writeFileSync(overlaySettingsPath, `${JSON.stringify(mergedSettings, null, 2)}\n`, { mode: 0o600 });
	if (hasCredentials) {
		writeFileSync(
			path.join(overlayDir, "auth.json"),
			`${JSON.stringify(options.providerCredentials ?? {}, null, 2)}\n`,
			{ mode: 0o600 },
		);
	}

	if (existsSync(baseAgentDir)) {
		for (const entry of readdirSync(baseAgentDir)) {
			if (
				entry === "settings.json" ||
				entry === ".astro-session-overrides" ||
				entry === ".astro-provider-auth" ||
				SENSITIVE_PI_AGENT_ENTRIES.has(entry)
			) {
				continue;
			}

			const sourcePath = path.join(baseAgentDir, entry);
			const targetPath = path.join(overlayDir, entry);
			if (!existsSync(sourcePath)) continue;

			rmSync(targetPath, { recursive: true, force: true });
			symlinkSync(sourcePath, targetPath);
		}
	}

	return overlayDir;
}
