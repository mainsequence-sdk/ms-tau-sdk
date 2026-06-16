import { createHash } from "node:crypto";
import {
	existsSync,
	mkdirSync,
	readFileSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import path from "node:path";
import {
	resolveBackendAuthHeaders,
	resolveBackendUrl,
	type BackendAuthHeaders,
} from "../../pi/extensions/shared/agent-registration.js";

type CapabilitySourceType = "inline" | "registry" | "repository" | "api" | "external";
type CapabilityKind = "skill" | "prompt" | "extension";

export type AgentCapability = {
	uid: string;
	name: string;
	kind: CapabilityKind;
	source_type: CapabilitySourceType;
	source_ref: string;
	capability_path: string;
	is_editable: boolean;
	description: string;
	metadata: Record<string, unknown>;
	content_file: string | null;
	content_sha256: string;
	content_mime_type: string;
	content_size: number;
	has_content: boolean;
	created_by_user_uid: string | null;
	updated_at: string;
};

export type AgentCapabilityBinding = {
	uid: string;
	agent_uid: string;
	capability_uid: string;
	capability: AgentCapability;
	role: string;
	sort_order: number;
	is_enabled: boolean;
	is_locked: boolean;
	configuration: Record<string, unknown>;
	source_type: CapabilitySourceType;
	source_ref: string;
	updated_at: string;
};

export type AgentSessionCapabilityBinding = {
	uid: string;
	agent_session_uid: string;
	capability_uid: string;
	capability: AgentCapability;
	role: string;
	sort_order: number;
	is_enabled: boolean;
	is_locked: boolean;
	configuration: Record<string, unknown>;
	source_type: CapabilitySourceType;
	source_ref: string;
	updated_at: string;
};

export type AgentCapabilityContent = {
	content: string;
	content_sha256: string;
	content_mime_type: string;
	content_size: number;
};

export type CapabilityClientResult<T> =
	| {
			ok: true;
			status: number;
			body: T;
	  }
	| {
			ok: false;
			status: number | null;
			error: string;
			body: unknown;
			responseText: string | null;
			url: string | null;
	  };

export type SessionCapabilityMaterializedSkill = {
	bindingUid: string;
	capabilityUid: string;
	capabilityPath: string;
	targetPath: string;
	contentSha256: string | null;
	bindingSourceType: CapabilitySourceType;
	capabilitySourceType: CapabilitySourceType;
};

export type SessionCapabilityMaterialization = {
	version: 1;
	agentSessionUid: string;
	materializedAt: string;
	sessionAssetRoot: string;
	skillsRoot: string;
	settingsSkillPaths: string[];
	bindingCount: number;
	enabledSkillBindingCount: number;
	materializedSkillCount: number;
	skipped: {
		disabled: number;
		unsupportedKind: number;
		repositoryProjectedDefault: number;
		missingContent: number;
		invalidPath: number;
	};
	materialized: SessionCapabilityMaterializedSkill[];
};

export type SessionCapabilityMaterializationResult =
	| {
			ok: true;
			value: SessionCapabilityMaterialization;
			cacheHit?: boolean;
			cacheReason?: string;
			cacheInvalidationReason?: string;
	  }
	| {
			ok: false;
			statusCode: number | null;
			error: string;
			message: string;
			body?: unknown;
			responseText?: string | null;
			url?: string | null;
	  };

const SUPPORTED_CAPABILITY_SOURCE_TYPES = new Set<CapabilitySourceType>([
	"inline",
	"registry",
	"repository",
	"api",
	"external",
]);

const SUPPORTED_CAPABILITY_KINDS = new Set<CapabilityKind>(["skill", "prompt", "extension"]);
const DEFAULT_ZERO_CAPABILITY_CACHE_TTL_MS = 60 * 60 * 1000;

type CachedSessionCapabilityMaterialization = {
	value: SessionCapabilityMaterialization;
	expiresAtMs: number | null;
	bindingSignature: string | null;
};

const sessionCapabilityMaterializationCache = new Map<string, CachedSessionCapabilityMaterialization>();

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeString(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value.trim() : null;
}

function normalizeBoolean(value: unknown, fallback = false): boolean {
	return value === true ? true : value === false ? false : fallback;
}

function normalizeInteger(value: unknown, fallback = 0): number {
	if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
	if (typeof value === "string" && /^-?\d+$/.test(value.trim())) return Number.parseInt(value.trim(), 10);
	return fallback;
}

function normalizeCapabilitySourceType(value: unknown): CapabilitySourceType | null {
	const normalized = normalizeString(value);
	if (!normalized || !SUPPORTED_CAPABILITY_SOURCE_TYPES.has(normalized as CapabilitySourceType)) return null;
	return normalized as CapabilitySourceType;
}

function normalizeCapabilityKind(value: unknown): CapabilityKind | null {
	const normalized = normalizeString(value);
	if (!normalized || !SUPPORTED_CAPABILITY_KINDS.has(normalized as CapabilityKind)) return null;
	return normalized as CapabilityKind;
}

function normalizeRecord(value: unknown): Record<string, unknown> {
	return isPlainObject(value) ? value : {};
}

function parseCapability(value: unknown): AgentCapability | null {
	if (!isPlainObject(value)) return null;
	const uid = normalizeString(value.uid);
	const kind = normalizeCapabilityKind(value.kind);
	const sourceType = normalizeCapabilitySourceType(value.source_type);
	if (!uid || !kind || !sourceType) return null;
	return {
		uid,
		name: normalizeString(value.name) ?? uid,
		kind,
		source_type: sourceType,
		source_ref: normalizeString(value.source_ref) ?? "",
		capability_path: normalizeString(value.capability_path) ?? "",
		is_editable: normalizeBoolean(value.is_editable),
		description: normalizeString(value.description) ?? "",
		metadata: normalizeRecord(value.metadata),
		content_file: normalizeString(value.content_file),
		content_sha256: normalizeString(value.content_sha256) ?? "",
		content_mime_type: normalizeString(value.content_mime_type) ?? "",
		content_size: normalizeInteger(value.content_size),
		has_content: normalizeBoolean(value.has_content),
		created_by_user_uid: normalizeString(value.created_by_user_uid),
		updated_at: normalizeString(value.updated_at) ?? "",
	};
}

function resolveCacheEnabled(env: NodeJS.ProcessEnv): boolean {
	return env.ASTRO_SESSION_CAPABILITY_CACHE !== "0";
}

function resolveZeroCapabilityCacheTtlMs(env: NodeJS.ProcessEnv): number {
	const configured = Number(env.ASTRO_SESSION_CAPABILITY_CACHE_TTL_MS ?? DEFAULT_ZERO_CAPABILITY_CACHE_TTL_MS);
	return Number.isFinite(configured) && configured > 0
		? Math.trunc(configured)
		: DEFAULT_ZERO_CAPABILITY_CACHE_TTL_MS;
}

function cacheKey(input: { agentSessionUid: string; sessionAssetsRoot: string; env?: NodeJS.ProcessEnv }): string {
	const env = input.env ?? process.env;
	const backendUrl = env.MAINSEQUENCE_BACKEND ?? "";
	return `${backendUrl}:${path.resolve(input.sessionAssetsRoot)}:${input.agentSessionUid}`;
}

function isZeroCapabilityMaterialization(value: SessionCapabilityMaterialization): boolean {
	return value.bindingCount === 0 && value.enabledSkillBindingCount === 0 && value.materializedSkillCount === 0;
}

function buildBindingsSignature(bindings: AgentSessionCapabilityBinding[]): string {
	const normalized = bindings
		.map((binding) => ({
			uid: binding.uid,
			agent_session_uid: binding.agent_session_uid,
			capability_uid: binding.capability_uid,
			role: binding.role,
			sort_order: binding.sort_order,
			is_enabled: binding.is_enabled,
			is_locked: binding.is_locked,
			source_type: binding.source_type,
			source_ref: binding.source_ref,
			updated_at: binding.updated_at,
			capability: {
				uid: binding.capability.uid,
				kind: binding.capability.kind,
				source_type: binding.capability.source_type,
				source_ref: binding.capability.source_ref,
				capability_path: binding.capability.capability_path,
				content_file: binding.capability.content_file,
				content_sha256: binding.capability.content_sha256,
				content_mime_type: binding.capability.content_mime_type,
				content_size: binding.capability.content_size,
				has_content: binding.capability.has_content,
				updated_at: binding.capability.updated_at,
			},
		}))
		.sort((left, right) => left.uid.localeCompare(right.uid));
	return `sha256:${createHash("sha256").update(JSON.stringify(normalized)).digest("hex")}`;
}

function cachedMaterializedFilesValid(value: SessionCapabilityMaterialization): boolean {
	for (const entry of value.materialized) {
		const existing = readExistingText(entry.targetPath);
		if (existing == null) return false;
		if (entry.contentSha256 && hashContent(existing) !== entry.contentSha256) return false;
	}
	return true;
}

function cloneMaterialization(
	value: SessionCapabilityMaterialization,
	materializedAt = value.materializedAt,
): SessionCapabilityMaterialization {
	return {
		...value,
		materializedAt,
		skipped: { ...value.skipped },
		settingsSkillPaths: [...value.settingsSkillPaths],
		materialized: value.materialized.map((entry) => ({ ...entry })),
	};
}

export function clearSessionCapabilityMaterializationCache() {
	sessionCapabilityMaterializationCache.clear();
}

function parseAgentCapabilityBinding(value: unknown): AgentCapabilityBinding | null {
	if (!isPlainObject(value)) return null;
	const uid = normalizeString(value.uid);
	const agentUid = normalizeString(value.agent_uid);
	const capabilityUid = normalizeString(value.capability_uid);
	const capability = parseCapability(value.capability);
	const sourceType = normalizeCapabilitySourceType(value.source_type);
	if (!uid || !agentUid || !capabilityUid || !capability || !sourceType) return null;
	return {
		uid,
		agent_uid: agentUid,
		capability_uid: capabilityUid,
		capability,
		role: normalizeString(value.role) ?? "",
		sort_order: normalizeInteger(value.sort_order),
		is_enabled: normalizeBoolean(value.is_enabled),
		is_locked: normalizeBoolean(value.is_locked),
		configuration: normalizeRecord(value.configuration),
		source_type: sourceType,
		source_ref: normalizeString(value.source_ref) ?? "",
		updated_at: normalizeString(value.updated_at) ?? "",
	};
}

function parseAgentSessionCapabilityBinding(value: unknown): AgentSessionCapabilityBinding | null {
	if (!isPlainObject(value)) return null;
	const uid = normalizeString(value.uid);
	const agentSessionUid = normalizeString(value.agent_session_uid);
	const capabilityUid = normalizeString(value.capability_uid);
	const capability = parseCapability(value.capability);
	const sourceType = normalizeCapabilitySourceType(value.source_type);
	if (!uid || !agentSessionUid || !capabilityUid || !capability || !sourceType) return null;
	return {
		uid,
		agent_session_uid: agentSessionUid,
		capability_uid: capabilityUid,
		capability,
		role: normalizeString(value.role) ?? "",
		sort_order: normalizeInteger(value.sort_order),
		is_enabled: normalizeBoolean(value.is_enabled),
		is_locked: normalizeBoolean(value.is_locked),
		configuration: normalizeRecord(value.configuration),
		source_type: sourceType,
		source_ref: normalizeString(value.source_ref) ?? "",
		updated_at: normalizeString(value.updated_at) ?? "",
	};
}

function parseListPayload<T>(body: unknown, parser: (value: unknown) => T | null): T[] {
	const items = Array.isArray(body)
		? body
		: isPlainObject(body) && Array.isArray(body.results)
			? body.results
			: [];
	const parsed: T[] = [];
	for (const item of items) {
		const value = parser(item);
		if (value) parsed.push(value);
	}
	return parsed;
}

function parseCapabilityContent(body: unknown): AgentCapabilityContent | null {
	if (!isPlainObject(body)) return null;
	const content = typeof body.content === "string" ? body.content : null;
	if (content == null) return null;
	return {
		content,
		content_sha256: normalizeString(body.content_sha256) ?? "",
		content_mime_type: normalizeString(body.content_mime_type) ?? "",
		content_size: normalizeInteger(body.content_size, Buffer.byteLength(content, "utf8")),
	};
}

async function readResponseBody(response: Response): Promise<{ text: string; json: unknown }> {
	const text = await response.text();
	if (!text.trim()) return { text, json: null };
	try {
		return { text, json: JSON.parse(text) };
	} catch {
		return { text, json: null };
	}
}

function extractBackendError(body: unknown, text: string, fallback: string): string {
	if (isPlainObject(body)) {
		for (const key of ["error_detail", "errorDetail", "detail", "message", "error"]) {
			const value = body[key];
			if (typeof value === "string" && value.trim()) return value.trim();
			if (value != null) {
				try {
					return JSON.stringify(value);
				} catch {
					return String(value);
				}
			}
		}
	}
	return text || fallback;
}

function endpoint(backendUrl: string, suffix: string): string {
	return `${backendUrl}/orm/api/agents/v1/${suffix.replace(/^\/+/, "")}`;
}

export class AgentCapabilitiesClient {
	private readonly backendUrl: string;
	private readonly env: NodeJS.ProcessEnv;
	private readonly log?: (message: string) => void;

	constructor(options: { env?: NodeJS.ProcessEnv; log?: (message: string) => void } = {}) {
		this.env = options.env ?? process.env;
		this.backendUrl = resolveBackendUrl(this.env);
		this.log = options.log;
	}

	async listAgentCapabilities(agentUid: string): Promise<CapabilityClientResult<AgentCapabilityBinding[]>> {
		const url = endpoint(
			this.backendUrl,
			`agents/${encodeURIComponent(agentUid)}/capabilities/`,
		);
		const result = await this.getJson<unknown>(url);
		if (result.ok === false) return result;
		return {
			ok: true,
			status: result.status,
			body: parseListPayload(result.body, parseAgentCapabilityBinding),
		};
	}

	async listSessionCapabilities(
		sessionUid: string,
	): Promise<CapabilityClientResult<AgentSessionCapabilityBinding[]>> {
		const url = endpoint(
			this.backendUrl,
			`sessions/${encodeURIComponent(sessionUid)}/capabilities/`,
		);
		const result = await this.getJson<unknown>(url);
		if (result.ok === false) return result;
		return {
			ok: true,
			status: result.status,
			body: parseListPayload(result.body, parseAgentSessionCapabilityBinding),
		};
	}

	async getCapabilityContent(
		capabilityUid: string,
	): Promise<CapabilityClientResult<AgentCapabilityContent>> {
		const url = endpoint(
			this.backendUrl,
			`capabilities/${encodeURIComponent(capabilityUid)}/content/`,
		);
		const result = await this.getJson<unknown>(url);
		if (result.ok === false) return result;
		const content = parseCapabilityContent(result.body);
		if (!content) {
			return {
				ok: false,
				status: result.status,
				error: "Capability content response did not include markdown content.",
				body: result.body,
				responseText: null,
				url,
			};
		}
		return {
			ok: true,
			status: result.status,
			body: content,
		};
	}

	private async getJson<T>(url: string): Promise<CapabilityClientResult<T>> {
		const authHeadersResult = await resolveBackendAuthHeaders(this.env, this.log);
		if (!authHeadersResult.headers) {
			return {
				ok: false,
				status: null,
				error: authHeadersResult.error ?? "Missing backend auth headers for capability request.",
				body: null,
				responseText: null,
				url,
			};
		}

		let response = await this.fetchGetJson(url, authHeadersResult.headers);
		if (response.status === 401 || response.status === 403) {
			const retryAuthHeaders = await resolveBackendAuthHeaders(this.env, this.log);
			if (retryAuthHeaders.headers) {
				response = await this.fetchGetJson(url, retryAuthHeaders.headers);
			}
		}

		const body = await readResponseBody(response);
		if (!response.ok) {
			return {
				ok: false,
				status: response.status,
				error: extractBackendError(
					body.json,
					body.text,
					`Capability request failed with status ${response.status}.`,
				),
				body: body.json,
				responseText: body.text,
				url,
			};
		}

		return {
			ok: true,
			status: response.status,
			body: body.json as T,
		};
	}

	private fetchGetJson(url: string, authHeaders: BackendAuthHeaders): Promise<Response> {
		return fetch(url, {
			method: "GET",
			headers: {
				...authHeaders,
				"Content-Type": "application/json",
			},
		});
	}
}

function sanitizeScopeKey(value: string): string {
	return value.trim().replace(/[^A-Za-z0-9_.:-]/g, "_");
}

function isInsidePath(parent: string, child: string): boolean {
	const relative = path.relative(parent, child);
	return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function normalizeSkillCapabilityPath(value: string): string | null {
	if (!value.trim() || value.includes("\\")) return null;
	if (path.posix.isAbsolute(value)) return null;
	const segments = value.split("/");
	if (segments[0] === "skills") segments.shift();
	if (segments.length === 0) return null;
	if (segments.some((segment) => !segment || segment === "." || segment === ".." || segment.startsWith("."))) {
		return null;
	}
	if (segments.at(-1) !== "SKILL.md") return null;
	return segments.join("/");
}

function isRepositoryProjectedDefault(binding: AgentSessionCapabilityBinding): boolean {
	return binding.source_type === "repository" || binding.capability.source_type === "repository";
}

function hashContent(content: string): string {
	return `sha256:${createHash("sha256").update(content).digest("hex")}`;
}

function readExistingText(filePath: string): string | null {
	if (!existsSync(filePath)) return null;
	try {
		return readFileSync(filePath, "utf8");
	} catch {
		return null;
	}
}

export async function materializeSessionCapabilities(input: {
	agentSessionUid: string;
	sessionAssetsRoot: string;
	env?: NodeJS.ProcessEnv;
	log?: (message: string) => void;
}): Promise<SessionCapabilityMaterializationResult> {
	const env = input.env ?? process.env;
	const key = cacheKey(input);
	const cacheEnabled = resolveCacheEnabled(env);
	let cacheInvalidationReason: string | null = null;
	if (cacheEnabled) {
		const cached = sessionCapabilityMaterializationCache.get(key);
		if (cached) {
			if (cached.expiresAtMs != null && cached.expiresAtMs > Date.now()) {
				return {
					ok: true,
					value: cloneMaterialization(cached.value, new Date().toISOString()),
					cacheHit: true,
					cacheReason: "zero_session_capabilities",
				};
			}
			if (cached.expiresAtMs != null) {
				cacheInvalidationReason = "zero_capability_cache_expired";
				sessionCapabilityMaterializationCache.delete(key);
			}
		}
	} else {
		cacheInvalidationReason = "cache_disabled";
	}

	const client = new AgentCapabilitiesClient({ env: input.env, log: input.log });
	const bindingsResult = await client.listSessionCapabilities(input.agentSessionUid);
	if (bindingsResult.ok === false) {
		return {
			ok: false,
			statusCode: bindingsResult.status,
			error: "session_capabilities_fetch_failed",
			message: bindingsResult.error,
			body: bindingsResult.body,
			responseText: bindingsResult.responseText,
			url: bindingsResult.url,
		};
	}
	const bindingSignature = buildBindingsSignature(bindingsResult.body);
	const cached = cacheEnabled ? sessionCapabilityMaterializationCache.get(key) : null;
	if (cached?.bindingSignature === bindingSignature) {
		if (cachedMaterializedFilesValid(cached.value)) {
			return {
				ok: true,
				value: cloneMaterialization(cached.value, new Date().toISOString()),
				cacheHit: true,
				cacheReason: "capability_signature_unchanged",
			};
		}
		cacheInvalidationReason = "materialized_files_missing_or_changed";
		sessionCapabilityMaterializationCache.delete(key);
	} else if (cached?.bindingSignature) {
		cacheInvalidationReason = "capability_signature_changed";
		sessionCapabilityMaterializationCache.delete(key);
	} else if (!cacheInvalidationReason) {
		cacheInvalidationReason = cached ? "cache_entry_not_reusable" : "cache_miss";
	}

	const sessionAssetRoot = path.resolve(
		input.sessionAssetsRoot,
		sanitizeScopeKey(input.agentSessionUid),
	);
	const skillsRoot = path.resolve(sessionAssetRoot, ".agents", "skills");
	if (!isInsidePath(path.resolve(input.sessionAssetsRoot), sessionAssetRoot) || !isInsidePath(sessionAssetRoot, skillsRoot)) {
		return {
			ok: false,
			statusCode: 500,
			error: "session_capability_materialization_root_invalid",
			message: "Resolved session capability materialization root escaped the configured session assets root.",
		};
	}

	rmSync(skillsRoot, { recursive: true, force: true });
	mkdirSync(skillsRoot, { recursive: true, mode: 0o700 });

	const skipped = {
		disabled: 0,
		unsupportedKind: 0,
		repositoryProjectedDefault: 0,
		missingContent: 0,
		invalidPath: 0,
	};
	const materialized: SessionCapabilityMaterializedSkill[] = [];
	let enabledSkillBindingCount = 0;

	for (const binding of bindingsResult.body) {
		if (!binding.is_enabled) {
			skipped.disabled += 1;
			continue;
		}
		if (binding.capability.kind !== "skill") {
			skipped.unsupportedKind += 1;
			continue;
		}
		enabledSkillBindingCount += 1;
		if (isRepositoryProjectedDefault(binding)) {
			skipped.repositoryProjectedDefault += 1;
			continue;
		}
		if (!binding.capability.has_content) {
			skipped.missingContent += 1;
			continue;
		}
		const relativeSkillPath = normalizeSkillCapabilityPath(binding.capability.capability_path);
		if (!relativeSkillPath) {
			skipped.invalidPath += 1;
			continue;
		}
		const targetPath = path.resolve(skillsRoot, relativeSkillPath);
		if (!isInsidePath(skillsRoot, targetPath)) {
			skipped.invalidPath += 1;
			continue;
		}

		const contentResult = await client.getCapabilityContent(binding.capability_uid);
		if (contentResult.ok === false) {
			return {
				ok: false,
				statusCode: contentResult.status,
				error: "session_capability_content_fetch_failed",
				message: contentResult.error,
				body: contentResult.body,
				responseText: contentResult.responseText,
				url: contentResult.url,
			};
		}

		const existing = readExistingText(targetPath);
		if (existing != null && hashContent(existing) !== hashContent(contentResult.body.content)) {
			return {
				ok: false,
				statusCode: 409,
				error: "session_capability_path_collision",
				message: `Multiple session capabilities resolved to the same skill path: ${binding.capability.capability_path}`,
			};
		}

		mkdirSync(path.dirname(targetPath), { recursive: true, mode: 0o700 });
		writeFileSync(targetPath, contentResult.body.content, { mode: 0o600 });
		materialized.push({
			bindingUid: binding.uid,
			capabilityUid: binding.capability_uid,
			capabilityPath: binding.capability.capability_path,
			targetPath,
			contentSha256: contentResult.body.content_sha256 || hashContent(contentResult.body.content),
			bindingSourceType: binding.source_type,
			capabilitySourceType: binding.capability.source_type,
		});
	}

	if (materialized.length === 0) {
		rmSync(skillsRoot, { recursive: true, force: true });
	}

	const value: SessionCapabilityMaterialization = {
		version: 1,
		agentSessionUid: input.agentSessionUid,
		materializedAt: new Date().toISOString(),
		sessionAssetRoot,
		skillsRoot,
		settingsSkillPaths: materialized.length > 0 ? [skillsRoot] : [],
		bindingCount: bindingsResult.body.length,
		enabledSkillBindingCount,
		materializedSkillCount: materialized.length,
		skipped,
		materialized,
	};

	if (cacheEnabled && isZeroCapabilityMaterialization(value)) {
		sessionCapabilityMaterializationCache.set(key, {
			value: cloneMaterialization(value),
			expiresAtMs: Date.now() + resolveZeroCapabilityCacheTtlMs(env),
			bindingSignature,
		});
	} else if (cacheEnabled) {
		sessionCapabilityMaterializationCache.set(key, {
			value: cloneMaterialization(value),
			expiresAtMs: null,
			bindingSignature,
		});
	} else {
		sessionCapabilityMaterializationCache.delete(key);
	}

	return {
		ok: true,
		value,
		cacheHit: false,
		cacheReason: cacheInvalidationReason ?? "cache_miss",
		cacheInvalidationReason: cacheInvalidationReason ?? undefined,
	};
}
