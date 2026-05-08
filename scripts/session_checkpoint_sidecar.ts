import { createHash } from "node:crypto";
import {
	existsSync,
	mkdirSync,
	readFileSync,
	readdirSync,
	rmSync,
	statSync,
	unlinkSync,
	watch,
	writeFileSync,
	type FSWatcher,
} from "node:fs";
import path from "node:path";
import {
	SessionCheckpointClient,
	type AgentSessionTerminalState,
	type CheckpointBundle,
	type CheckpointFlushResponse,
} from "../interface/stream/session-checkpoint-client.js";
import {
	buildSessionInsightsResponse,
	readSessionInsights,
} from "../interface/stream/session-insights.js";
import {
	normalizeStructuredLogRecord,
	shouldEmitStructuredLog,
	resolveStructuredLogSessionId,
} from "../pi/extensions/shared/structured-logging.js";

type CheckpointReason =
	| "stream_finish"
	| "stream_error"
	| "periodic"
	| "compaction"
	| "shutdown";

type CheckpointManifest = {
	session_id: string;
	checkpoint_version: number;
	restored_at: string;
	bundle_hash: string;
	lease_holder_id: string;
	lease_token: string;
	lease_expires_at: string;
	last_flushed_at?: string;
	last_flush_reason?: CheckpointReason;
};

type CheckpointMarker = {
	sessionKey: string;
	reason: CheckpointReason;
	leaseHolderId: string | null;
	leaseToken: string | null;
	checkpointVersion: number | null;
	bundleHash: string | null;
	agentSessionTerminalState: AgentSessionTerminalState | null;
};

type CheckpointLifecycleState = {
	session_id: string;
	state: "running" | "finalizing_checkpoint" | "idle";
	updated_at: string;
	lease_holder_id: string | null;
	lease_token: string | null;
	checkpoint_version: number | null;
	bundle_hash: string | null;
	reason: string | null;
	marker_file?: string | null;
};

type DirtySession = {
	firstDirtyAt: number;
	reason: CheckpointReason;
	timer: NodeJS.Timeout | null;
	marker: CheckpointMarker | null;
};

type JsonlReadResult =
	| {
			ok: true;
			content: string;
			latestCompactionId: string | null;
	  }
	| {
			ok: false;
			error: string;
			retryable: boolean;
	  };

type BundleBuildResult =
	| {
			ok: true;
			agentSessionId: number;
			manifest: CheckpointManifest;
			bundle: CheckpointBundle;
			bundleHash: string;
			latestCompactionId: string | null;
	  }
	| {
			ok: false;
			error: string;
			retryable: boolean;
	  };

const sessionDir =
	process.env.ASTRO_STREAM_SESSION_DIR?.trim() ||
	path.join(process.env.ASTRO_SESSION_STATE_DIR?.trim() || "/session-state", "sessions");
const sessionStateDir = path.dirname(sessionDir);
const manifestDir = path.join(sessionStateDir, "manifests");
const checkpointDir = path.join(sessionStateDir, "checkpoints");
const lifecycleDir = path.join(sessionStateDir, "checkpoint-lifecycle");
const piAgentDir =
	process.env.PI_CODING_AGENT_DIR?.trim() ||
	path.join(process.env.HOME?.trim() || process.cwd(), ".pi", "agent");
const sessionOverridesDir =
	process.env.ASTRO_SESSION_OVERRIDES_DIR?.trim() ||
	path.join(piAgentDir, ".astro-session-overrides");

const debounceMs = readPositiveInteger("ASTRO_CHECKPOINT_SIDECAR_DEBOUNCE_MS", 1000, 100);
const scanIntervalMs = readPositiveInteger("ASTRO_CHECKPOINT_SIDECAR_SCAN_INTERVAL_MS", 10000, 1000);
const periodicFlushMs = readPositiveInteger("ASTRO_CHECKPOINT_SIDECAR_PERIODIC_FLUSH_MS", 30000, 5000);
const metricsIntervalMs = readPositiveInteger("ASTRO_CHECKPOINT_SIDECAR_METRICS_INTERVAL_MS", 60000, 10000);
const shutdownTimeoutMs = readPositiveInteger("ASTRO_CHECKPOINT_SIDECAR_SHUTDOWN_TIMEOUT_MS", 25000, 1000);

const reasonPriority: Record<CheckpointReason, number> = {
	periodic: 1,
	stream_finish: 2,
	stream_error: 3,
	compaction: 4,
	shutdown: 5,
};

const checkpointClient = new SessionCheckpointClient({
	env: process.env,
	log: (message) => console.log(`[astro-checkpoint-sidecar] ${message}`),
});

const pendingFlushes = new Map<string, DirtySession>();
const inFlightFlushes = new Set<string>();
const latestCompactionIds = new Map<string, string>();
const knownRestoreMarkers = new Map<string, string>();
const watchedOverrideDirs = new Set<string>();
const uploadedInsightsKeys = new Map<string, string>();
const watchers: FSWatcher[] = [];
const skippedUnmanagedSessions = new Set<string>();
const skippedExpiredLeases = new Set<string>();

let closed = false;
let observedRestoreManifestCount = 0;
let flushAttemptCount = 0;
let flushSuccessCount = 0;
let flushNoopCount = 0;
let flushRejectedCount = 0;
let leaseFailureCount = 0;
let localValidationRejectCount = 0;
let compactionFlushCount = 0;
let shutdownFlushCount = 0;
let lastCheckpointVersion: number | null = null;
let lastBundleHash: string | null = null;
let lastDirtyAgeMs: number | null = null;
let insightsUploadAttemptCount = 0;
let insightsUploadSuccessCount = 0;
let insightsUploadFailureCount = 0;
let insightsUploadSkippedCount = 0;

function readPositiveInteger(name: string, fallback: number, minimum: number): number {
	const parsed = Number.parseInt(process.env[name] ?? "", 10);
	return Number.isFinite(parsed) && parsed >= minimum ? parsed : fallback;
}

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function normalizeNumericId(value: unknown): number | null {
	if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
	if (typeof value === "string" && value.trim()) {
		const parsed = Number.parseInt(value, 10);
		return Number.isFinite(parsed) ? parsed : null;
	}
	return null;
}

function normalizeOptionalString(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value.trim() : null;
}

function parseAgentSessionTerminalState(value: unknown): AgentSessionTerminalState | null {
	if (!isPlainObject(value)) return null;
	const status =
		value.status === "completed"
			? "completed"
			: value.status === "error"
				? "error"
				: value.status === "canceled" || value.status === "cancelled"
					? "canceled"
					: null;
	if (!status) return null;
	return {
		status,
		error_code: normalizeOptionalString(value.error_code),
		error_detail: normalizeOptionalString(value.error_detail),
	};
}

function defaultAgentSessionTerminalState(reason: CheckpointReason): AgentSessionTerminalState | null {
	if (reason === "stream_finish") {
		return {
			status: "completed",
			error_code: null,
			error_detail: null,
		};
	}
	if (reason === "stream_error") {
		return {
			status: "error",
			error_code: null,
			error_detail: "Runtime ended with an error.",
		};
	}
	return null;
}

function readJsonObject(filePath: string): Record<string, unknown> | null {
	try {
		const parsed = JSON.parse(readFileSync(filePath, "utf8"));
		return isPlainObject(parsed) ? parsed : null;
	} catch {
		return null;
	}
}

function writeJsonObject(filePath: string, value: Record<string, unknown>) {
	mkdirSync(path.dirname(filePath), { recursive: true });
	writeFileSync(filePath, JSON.stringify(value, null, 2));
}

function unlinkIfExists(filePath: string): boolean {
	if (!existsSync(filePath)) return false;
	try {
		unlinkSync(filePath);
		return true;
	} catch {
		return false;
	}
}

function canonicalJson(value: unknown): string {
	if (value === null || value === undefined) return "null";
	if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
	if (typeof value === "object") {
		const entries = Object.entries(value as Record<string, unknown>)
			.filter(([, entryValue]) => entryValue !== undefined)
			.sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));
		return `{${entries.map(([key, entryValue]) => `${JSON.stringify(key)}:${canonicalJson(entryValue)}`).join(",")}}`;
	}
	return JSON.stringify(value);
}

function computeBundleHash(bundle: CheckpointBundle): string {
	const normalized = {
		astro_metadata_json: bundle.astro_metadata_json,
		pi_session_jsonl: bundle.pi_session_jsonl,
		session_overrides_json: bundle.session_overrides_json ?? null,
		thread_binding_json: bundle.thread_binding_json,
	};
	return `sha256:${createHash("sha256").update(canonicalJson(normalized)).digest("hex")}`;
}

function sortedKeys(value: unknown): string[] {
	return isPlainObject(value) ? Object.keys(value).sort((left, right) => (left < right ? -1 : left > right ? 1 : 0)) : [];
}

function countJsonlLines(content: string): number {
	return content
		.split(/\n/)
		.map((line) => line.trim())
		.filter(Boolean).length;
}

function summarizeCheckpointBundle(bundle: CheckpointBundle, latestCompactionId: string | null) {
	const sessionOverrides = bundle.session_overrides_json ?? null;
	return {
		hash_algorithm: "sha256(canonical_json(bundle))",
		canonical_json_sort: "recursive unicode/code-point key order, compact separators, no locale sorting",
		hash_fields: [
			"astro_metadata_json",
			"pi_session_jsonl",
			"session_overrides_json",
			"thread_binding_json",
		],
		pi_session_jsonl_bytes: Buffer.byteLength(bundle.pi_session_jsonl, "utf8"),
		pi_session_jsonl_lines: countJsonlLines(bundle.pi_session_jsonl),
		pi_session_jsonl_ends_with_newline: bundle.pi_session_jsonl.endsWith("\n"),
		latest_compaction_id: latestCompactionId,
		astro_metadata_json_keys: sortedKeys(bundle.astro_metadata_json),
		thread_binding_json_keys: sortedKeys(bundle.thread_binding_json),
		session_overrides_json_state: sessionOverrides === null ? "null" : "object",
		session_overrides_json_keys: sortedKeys(sessionOverrides),
	};
}

function deleteLocalConversationHistory(sessionKey: string): string[] {
	const deleted: string[] = [];
	for (const filePath of [getConversationEventLogPath(sessionKey), getConversationHistoryPath(sessionKey)]) {
		if (unlinkIfExists(filePath)) deleted.push(path.basename(filePath));
	}
	return deleted;
}

function materializeNormalizedCheckpointBundle(sessionKey: string, bundle: CheckpointBundle): {
	threadId: string;
	deletedHistoryFiles: string[];
	latestCompactionId: string | null;
} {
	mkdirSync(sessionDir, { recursive: true });
	writeFileSync(getSessionPath(sessionKey), bundle.pi_session_jsonl);
	writeJsonObject(getSessionMetadataPath(sessionKey), bundle.astro_metadata_json);

	const metadataThreadId =
		typeof bundle.astro_metadata_json.threadId === "string" && bundle.astro_metadata_json.threadId.trim()
			? bundle.astro_metadata_json.threadId.trim()
			: typeof bundle.astro_metadata_json.thread_id === "string" && bundle.astro_metadata_json.thread_id.trim()
				? bundle.astro_metadata_json.thread_id.trim()
				: null;
	const threadId =
		typeof bundle.thread_binding_json.threadId === "string" && bundle.thread_binding_json.threadId.trim()
			? bundle.thread_binding_json.threadId.trim()
			: typeof bundle.thread_binding_json.thread_id === "string" && bundle.thread_binding_json.thread_id.trim()
				? bundle.thread_binding_json.thread_id.trim()
				: metadataThreadId ?? sessionKey;
	writeJsonObject(getThreadBindingPath(threadId), bundle.thread_binding_json);

	const overridesPath = getSessionOverridesPath(sessionKey);
	if (isPlainObject(bundle.session_overrides_json)) {
		writeJsonObject(overridesPath, bundle.session_overrides_json);
	} else {
		rmSync(path.dirname(overridesPath), { recursive: true, force: true });
	}

	const jsonl = readCompleteJsonl(getSessionPath(sessionKey));
	const latestCompactionId = jsonl.ok ? jsonl.latestCompactionId : null;
	if (latestCompactionId) latestCompactionIds.set(sessionKey, latestCompactionId);

	return {
		threadId,
		deletedHistoryFiles: deleteLocalConversationHistory(sessionKey),
		latestCompactionId,
	};
}

function isCheckpointBundle(value: unknown): value is CheckpointBundle {
	if (!isPlainObject(value)) return false;
	if (typeof value.pi_session_jsonl !== "string") return false;
	if (!isPlainObject(value.astro_metadata_json)) return false;
	if (!isPlainObject(value.thread_binding_json)) return false;
	return value.session_overrides_json === undefined ||
		value.session_overrides_json === null ||
		isPlainObject(value.session_overrides_json);
}

function truncateString(value: string, maxLength = 500): string {
	return value.length > maxLength ? `${value.slice(0, maxLength)}...<truncated>` : value;
}

function summarizeForLog(value: unknown, depth = 0): unknown {
	if (value == null) return value;
	if (typeof value === "string") return truncateString(value, 1000);
	if (typeof value === "number" || typeof value === "boolean") return value;
	if (Array.isArray(value)) {
		return value.slice(0, 25).map((entry) => summarizeForLog(entry, depth + 1));
	}
	if (!isPlainObject(value)) return String(value);
	if (depth >= 6) return "<max_depth>";
	const summarized: Record<string, unknown> = {};
	for (const [key, entryValue] of Object.entries(value)) {
		if (key === "pi_session_jsonl") {
			summarized[key] =
				typeof entryValue === "string"
					? `<omitted:${Buffer.byteLength(entryValue, "utf8")}_bytes>`
					: "<omitted>";
			continue;
		}
		summarized[key] = summarizeForLog(entryValue, depth + 1);
	}
	return summarized;
}

function flattenBackendValidationErrors(value: unknown, prefix = ""): Array<{ field: string; error: string }> {
	if (value == null) return [];
	if (typeof value === "string") return [{ field: prefix || "non_field_error", error: truncateString(value, 1000) }];
	if (Array.isArray(value)) {
		return value.flatMap((entry, index) =>
			typeof entry === "string"
				? [{ field: prefix || "non_field_error", error: truncateString(entry, 1000) }]
				: flattenBackendValidationErrors(entry, `${prefix}[${index}]`),
		);
	}
	if (!isPlainObject(value)) return [{ field: prefix || "non_field_error", error: truncateString(String(value), 1000) }];

	const flattened: Array<{ field: string; error: string }> = [];
	for (const [key, entryValue] of Object.entries(value)) {
		const field = prefix ? `${prefix}.${key}` : key;
		flattened.push(...flattenBackendValidationErrors(entryValue, field));
	}
	return flattened;
}

function summarizeBackendErrorBody(body: unknown): unknown {
	if (body == null) return null;
	if (typeof body === "string") return truncateString(body);
	if (!isPlainObject(body)) return body;
	const summarized: Record<string, unknown> = {};
	for (const key of [
		"error_code",
		"error_detail",
		"detail",
		"error",
		"agent_session_id",
		"checkpoint_version",
		"bundle_hash",
	]) {
		const value = body[key];
		if (typeof value === "string") {
			summarized[key] = truncateString(value);
		} else if (value !== undefined) {
			summarized[key] = value;
		}
	}
	return Object.keys(summarized).length > 0 ? summarized : summarizeForLog(body);
}

function getManifestPath(sessionKey: string): string {
	return path.join(manifestDir, `${sessionKey}.manifest.json`);
}

function getLifecyclePath(sessionKey: string): string {
	return path.join(lifecycleDir, `${sessionKey}.json`);
}

function hasCheckpointManifest(sessionKey: string): boolean {
	return existsSync(getManifestPath(sessionKey));
}

function getSessionPath(sessionKey: string): string {
	return path.join(sessionDir, `${sessionKey}.jsonl`);
}

function getSessionMetadataPath(sessionKey: string): string {
	return path.join(sessionDir, `${sessionKey}.meta.json`);
}

function getThreadBindingPath(threadId: string): string {
	return path.join(sessionDir, `${sanitizeSessionKey(threadId)}.thread.json`);
}

function getSessionOverridesPath(sessionKey: string): string {
	return path.join(sessionOverridesDir, sessionKey, "settings.json");
}

function getConversationEventLogPath(sessionKey: string): string {
	return path.join(sessionDir, `${sessionKey}.conversation.jsonl`);
}

function getConversationHistoryPath(sessionKey: string): string {
	return path.join(sessionDir, `${sessionKey}.history.json`);
}

function sanitizeSessionKey(value: string): string {
	return value.trim().replace(/[^A-Za-z0-9_.-]/g, "_");
}

function readCheckpointManifest(sessionKey: string): CheckpointManifest | null {
	const parsed = readJsonObject(getManifestPath(sessionKey));
	if (!parsed) return null;

	const sessionId =
		typeof parsed.session_id === "string" && parsed.session_id.trim() ? parsed.session_id.trim() : null;
	const checkpointVersion = normalizeNumericId(parsed.checkpoint_version);
	const bundleHash =
		typeof parsed.bundle_hash === "string" && parsed.bundle_hash.trim() ? parsed.bundle_hash.trim() : "";
	const holderId =
		typeof parsed.lease_holder_id === "string" && parsed.lease_holder_id.trim()
			? parsed.lease_holder_id.trim()
			: null;
	const leaseToken =
		typeof parsed.lease_token === "string" && parsed.lease_token.trim() ? parsed.lease_token.trim() : null;
	const leaseExpiresAt =
		typeof parsed.lease_expires_at === "string" && parsed.lease_expires_at.trim()
			? parsed.lease_expires_at.trim()
			: null;
	const restoredAt =
		typeof parsed.restored_at === "string" && parsed.restored_at.trim()
			? parsed.restored_at.trim()
			: null;
	const lastFlushedAt =
		typeof parsed.last_flushed_at === "string" && parsed.last_flushed_at.trim()
			? parsed.last_flushed_at.trim()
			: undefined;
	const lastFlushReason =
		typeof parsed.last_flush_reason === "string" && isCheckpointReason(parsed.last_flush_reason)
			? parsed.last_flush_reason
			: undefined;

	if (!sessionId || checkpointVersion == null || !holderId || !leaseToken || !leaseExpiresAt) return null;
	return {
		session_id: sessionId,
		checkpoint_version: checkpointVersion,
		restored_at: restoredAt ?? new Date().toISOString(),
		bundle_hash: bundleHash,
		lease_holder_id: holderId,
		lease_token: leaseToken,
		lease_expires_at: leaseExpiresAt,
		last_flushed_at: lastFlushedAt,
		last_flush_reason: lastFlushReason,
	};
}

function readCheckpointLifecycleState(sessionKey: string): CheckpointLifecycleState | null {
	const parsed = readJsonObject(getLifecyclePath(sessionKey));
	if (!parsed) return null;
	const state =
		parsed.state === "running" ||
		parsed.state === "finalizing_checkpoint" ||
		parsed.state === "idle"
			? parsed.state
			: null;
	const updatedAt =
		typeof parsed.updated_at === "string" && parsed.updated_at.trim() ? parsed.updated_at.trim() : null;
	if (!state || !updatedAt) return null;
	return {
		session_id:
			typeof parsed.session_id === "string" && parsed.session_id.trim()
				? parsed.session_id.trim()
				: sessionKey,
		state,
		updated_at: updatedAt,
		lease_holder_id:
			typeof parsed.lease_holder_id === "string" && parsed.lease_holder_id.trim()
				? parsed.lease_holder_id.trim()
				: null,
		lease_token:
			typeof parsed.lease_token === "string" && parsed.lease_token.trim()
				? parsed.lease_token.trim()
				: null,
		checkpoint_version: normalizeNumericId(parsed.checkpoint_version),
		bundle_hash:
			typeof parsed.bundle_hash === "string" && parsed.bundle_hash.trim()
				? parsed.bundle_hash.trim()
				: null,
		reason: typeof parsed.reason === "string" && parsed.reason.trim() ? parsed.reason.trim() : null,
		marker_file:
			typeof parsed.marker_file === "string" && parsed.marker_file.trim()
				? parsed.marker_file.trim()
				: null,
	};
}

function clearCheckpointLifecycleIfLease(sessionKey: string, leaseToken: string) {
	const state = readCheckpointLifecycleState(sessionKey);
	if (state?.lease_token && state.lease_token !== leaseToken) return;
	rmSync(getLifecyclePath(sessionKey), { force: true });
}

function clearCheckpointLifecycleIfMarker(sessionKey: string, marker: CheckpointMarker | null) {
	if (!marker) return;
	const state = readCheckpointLifecycleState(sessionKey);
	if (!state || state.state !== "finalizing_checkpoint") return;
	if (
		marker.checkpointVersion != null &&
		state.checkpoint_version != null &&
		marker.checkpointVersion !== state.checkpoint_version
	) {
		return;
	}
	if (marker.bundleHash && state.bundle_hash && marker.bundleHash !== state.bundle_hash) return;
	rmSync(getLifecyclePath(sessionKey), { force: true });
}

function writeCheckpointManifest(
	sessionKey: string,
	manifest: CheckpointManifest,
	updates: {
		checkpointVersion: number;
		bundleHash: string;
		reason: CheckpointReason;
	},
) {
	mkdirSync(manifestDir, { recursive: true });
	const latestManifest = readCheckpointManifest(sessionKey);
	const leaseSource = latestManifest ?? manifest;
	const nextManifest: CheckpointManifest = {
		...manifest,
		session_id: sessionKey,
		checkpoint_version: updates.checkpointVersion,
		bundle_hash: updates.bundleHash,
		restored_at: latestManifest?.restored_at ?? manifest.restored_at,
		lease_holder_id: leaseSource.lease_holder_id,
		lease_token: leaseSource.lease_token,
		lease_expires_at: leaseSource.lease_expires_at,
		last_flushed_at: new Date().toISOString(),
		last_flush_reason: updates.reason,
	};
	writeFileSync(getManifestPath(sessionKey), JSON.stringify(nextManifest, null, 2));
}

function expireCheckpointManifestLease(sessionKey: string, leaseToken: string) {
	const manifest = readCheckpointManifest(sessionKey);
	if (!manifest || manifest.lease_token !== leaseToken) return;
	writeFileSync(
		getManifestPath(sessionKey),
		JSON.stringify(
			{
				...manifest,
				lease_expires_at: new Date(0).toISOString(),
			},
			null,
			2,
		),
	);
}

function shouldReleaseLeaseAfterFlush(reason: CheckpointReason): boolean {
	return (
		reason === "stream_finish" ||
		reason === "stream_error" ||
		reason === "shutdown"
	);
}

async function releaseCheckpointLeaseAfterFlush(input: {
	sessionKey: string;
	agentSessionId: number;
	manifest: CheckpointManifest;
	reason: CheckpointReason;
	marker: CheckpointMarker | null;
}) {
	if (!shouldReleaseLeaseAfterFlush(input.reason)) return;
	const releaseReason =
		input.reason === "shutdown" && input.marker?.agentSessionTerminalState?.status === "canceled"
			? "runtime_canceled"
			: input.reason;
	for (let attempt = 1; attempt <= 3; attempt += 1) {
		const result = await checkpointClient.releaseLease({
			agentSessionId: input.agentSessionId,
			holderId: input.manifest.lease_holder_id,
			leaseToken: input.manifest.lease_token,
			reason: releaseReason,
		});
		if (result.ok === true) {
			expireCheckpointManifestLease(input.sessionKey, input.manifest.lease_token);
			clearCheckpointLifecycleIfLease(input.sessionKey, input.manifest.lease_token);
			logEvent("checkpoint_lease_released", {
				session_id: input.sessionKey,
				reason: input.reason,
				release_reason: releaseReason,
				agent_session_id: input.agentSessionId,
				released: result.body.released,
				attempt,
			});
			return;
		}
		logEvent("checkpoint_lease_release_failed", {
			session_id: input.sessionKey,
			reason: input.reason,
			release_reason: releaseReason,
			agent_session_id: input.agentSessionId,
			attempt,
			status: result.status,
			error: result.error,
			backend_request_url: result.url,
			backend_response_body: result.body,
			backend_response_text: result.responseText,
		});
		if (attempt < 3) await sleep(500 * attempt);
	}
}

function observeRestoreManifest(sessionKey: string) {
	const manifest = readCheckpointManifest(sessionKey);
	if (!manifest) return;
	const marker = manifest.restored_at;
	if (knownRestoreMarkers.get(sessionKey) === marker) return;
	knownRestoreMarkers.set(sessionKey, marker);
	observedRestoreManifestCount += 1;
	logEvent("checkpoint_restore_observed", {
		session_id: sessionKey,
		checkpoint_version: manifest.checkpoint_version,
		bundle_hash: manifest.bundle_hash,
	});
}

function isCheckpointReason(value: string): value is CheckpointReason {
	return (
		value === "stream_finish" ||
		value === "stream_error" ||
		value === "periodic" ||
		value === "compaction" ||
		value === "shutdown"
	);
}

function normalizeMarkerReason(value: unknown): CheckpointReason {
	if (value === "finish" || value === "stream_finish") return "stream_finish";
	if (value === "error" || value === "stream_error") return "stream_error";
	if (value === "cancel" || value === "canceled" || value === "cancelled" || value === "stream_cancelled") {
		return "shutdown";
	}
	if (value === "compaction") return "compaction";
	if (value === "shutdown") return "shutdown";
	return "periodic";
}

function chooseReason(current: CheckpointReason, next: CheckpointReason): CheckpointReason {
	return reasonPriority[next] >= reasonPriority[current] ? next : current;
}

function readCompleteJsonl(filePath: string): JsonlReadResult {
	if (!existsSync(filePath)) {
		return { ok: false, error: "missing_pi_session_jsonl", retryable: false };
	}

	const content = readFileSync(filePath, "utf8");
	if (content.length === 0) {
		return { ok: true, content, latestCompactionId: null };
	}
	if (!content.endsWith("\n")) {
		return { ok: false, error: "incomplete_pi_session_jsonl_trailing_line", retryable: true };
	}

	let latestCompactionId: string | null = null;
	const lines = content.split(/\n/);
	for (const line of lines) {
		if (!line.trim()) continue;
		try {
			const parsed = JSON.parse(line);
			if (isPlainObject(parsed) && parsed.type === "compaction") {
				latestCompactionId =
					typeof parsed.id === "string" && parsed.id.trim()
						? parsed.id.trim()
						: typeof parsed.timestamp === "string"
							? parsed.timestamp
							: line;
			}
		} catch {
			return { ok: false, error: "invalid_pi_session_jsonl", retryable: true };
		}
	}

	return { ok: true, content, latestCompactionId };
}

function resolveAgentSessionId(sessionKey: string, manifest: CheckpointManifest, metadata: Record<string, unknown>): number | null {
	return normalizeNumericId(sessionKey) ?? normalizeNumericId(manifest.session_id) ?? normalizeNumericId(metadata.agentSessionId);
}

function findThreadBinding(sessionKey: string, metadata: Record<string, unknown>): Record<string, unknown> | null {
	const metadataThreadId =
		typeof metadata.threadId === "string" && metadata.threadId.trim() ? metadata.threadId.trim() : null;
	if (metadataThreadId) {
		const directBinding = readJsonObject(getThreadBindingPath(metadataThreadId));
		if (directBinding) return directBinding;
	}

	if (!existsSync(sessionDir)) return null;
	for (const entry of readdirSync(sessionDir, { withFileTypes: true })) {
		if (!entry.isFile() || !entry.name.endsWith(".thread.json")) continue;
		const binding = readJsonObject(path.join(sessionDir, entry.name));
		if (!binding) continue;
		const runtimeSessionId =
			typeof binding.runtimeSessionId === "string" && binding.runtimeSessionId.trim()
				? binding.runtimeSessionId.trim()
				: null;
		if (runtimeSessionId === sessionKey) return binding;
	}
	return null;
}

function buildCheckpointBundle(sessionKey: string): BundleBuildResult {
	const manifest = readCheckpointManifest(sessionKey);
	if (!manifest) return { ok: false, error: "missing_checkpoint_manifest", retryable: false };

	const jsonl = readCompleteJsonl(getSessionPath(sessionKey));
	if (jsonl.ok === false) {
		return { ok: false, error: jsonl.error, retryable: jsonl.retryable };
	}

	const metadata = readJsonObject(getSessionMetadataPath(sessionKey));
	if (!metadata) return { ok: false, error: "missing_or_invalid_astro_metadata_json", retryable: false };

	const threadBinding = findThreadBinding(sessionKey, metadata);
	if (!threadBinding) return { ok: false, error: "missing_or_invalid_thread_binding_json", retryable: false };

	const agentSessionId = resolveAgentSessionId(sessionKey, manifest, metadata);
	if (agentSessionId == null) return { ok: false, error: "invalid_agent_session_id", retryable: false };

	const overridesPath = getSessionOverridesPath(sessionKey);
	const sessionOverrides = existsSync(overridesPath) ? readJsonObject(overridesPath) : null;
	if (existsSync(overridesPath) && !sessionOverrides) {
		return { ok: false, error: "invalid_session_overrides_json", retryable: true };
	}

	const bundle: CheckpointBundle = {
		pi_session_jsonl: jsonl.content,
		astro_metadata_json: metadata,
		thread_binding_json: threadBinding,
		session_overrides_json: sessionOverrides,
	};
	return {
		ok: true,
		agentSessionId,
		manifest,
		bundle,
		bundleHash: computeBundleHash(bundle),
		latestCompactionId: jsonl.latestCompactionId,
	};
}

function insightsUploadKey(checkpointVersion: number, bundleHash: string): string {
	return `${checkpointVersion}:${bundleHash}`;
}

async function uploadSessionInsights(input: {
	sessionKey: string;
	agentSessionId: number;
	checkpointVersion: number;
	bundleHash: string;
	reason: CheckpointReason;
}) {
	const uploadKey = insightsUploadKey(input.checkpointVersion, input.bundleHash);
	if (uploadedInsightsKeys.get(input.sessionKey) === uploadKey) return;

	const metadata = readJsonObject(getSessionMetadataPath(input.sessionKey));
	if (!metadata) {
		insightsUploadSkippedCount += 1;
		logEvent("session_insights_upload_skipped", {
			session_id: input.sessionKey,
			agent_session_id: input.agentSessionId,
			checkpoint_version: input.checkpointVersion,
			bundle_hash: input.bundleHash,
			reason: input.reason,
			error: "missing_or_invalid_astro_metadata_json",
		});
		return;
	}

	const insights = readSessionInsights({
		sessionDir,
		sessionKey: input.sessionKey,
		metadata: metadata as Parameters<typeof readSessionInsights>[0]["metadata"],
	});
	if (!insights) {
		insightsUploadSkippedCount += 1;
		logEvent("session_insights_upload_skipped", {
			session_id: input.sessionKey,
			agent_session_id: input.agentSessionId,
			checkpoint_version: input.checkpointVersion,
			bundle_hash: input.bundleHash,
			reason: input.reason,
			error: "session_insights_unavailable",
		});
		return;
	}

	const computedAt = new Date().toISOString();
	const payload = buildSessionInsightsResponse(insights) as unknown as Record<string, unknown>;
	insightsUploadAttemptCount += 1;
	const result = await checkpointClient.updateInsights({
		agentSessionId: input.agentSessionId,
		checkpointVersion: input.checkpointVersion,
		bundleHash: input.bundleHash,
		computedAt,
		reason: input.reason,
		insights: payload,
	});

	if (result.ok === false) {
		insightsUploadFailureCount += 1;
		logEvent("session_insights_upload_failed", {
			session_id: input.sessionKey,
			agent_session_id: input.agentSessionId,
			checkpoint_version: input.checkpointVersion,
			bundle_hash: input.bundleHash,
			reason: input.reason,
			status: result.status,
			error: result.error,
			backend_request_url: result.url,
			backend_response_body: result.body,
			backend_response_text: result.responseText,
			backend_field_errors: flattenBackendValidationErrors(result.body),
			backend_error_summary: summarizeBackendErrorBody(result.body),
		});
		return;
	}

	uploadedInsightsKeys.set(input.sessionKey, uploadKey);
	insightsUploadSuccessCount += 1;
	logEvent("session_insights_uploaded", {
		session_id: input.sessionKey,
		agent_session_id: input.agentSessionId,
		checkpoint_version: result.body.checkpoint_version,
		bundle_hash: result.body.bundle_hash,
		reason: result.body.reason ?? input.reason,
		computed_at: result.body.computed_at,
		flushed_at: result.body.flushed_at ?? null,
	});
}

function isLeaseError(error: string): boolean {
	return error.includes("checkpoint_lease") || error.includes("lease");
}

function leaseIsExpired(manifest: CheckpointManifest): boolean {
	const expiresAt = Date.parse(manifest.lease_expires_at);
	return Number.isFinite(expiresAt) && expiresAt <= Date.now();
}

function reasonRequiresMarkerLease(reason: CheckpointReason, marker: CheckpointMarker | null): boolean {
	if (reason === "stream_finish" || reason === "stream_error") return true;
	return reason === "shutdown" && marker != null;
}

function markerMatchesManifest(marker: CheckpointMarker, manifest: CheckpointManifest): boolean {
	return (
		marker.leaseToken === manifest.lease_token &&
		marker.leaseHolderId === manifest.lease_holder_id
	);
}

function updateCompactionState(sessionKey: string, latestCompactionId: string | null) {
	if (!latestCompactionId) return;
	const previous = latestCompactionIds.get(sessionKey);
	latestCompactionIds.set(sessionKey, latestCompactionId);
	if (previous && previous !== latestCompactionId) {
		scheduleFlush(sessionKey, "compaction", { immediate: true });
	}
}

async function flushSession(sessionKey: string, reason: CheckpointReason) {
	if (inFlightFlushes.has(sessionKey)) {
		scheduleFlush(sessionKey, reason);
		return;
	}

	const dirty = pendingFlushes.get(sessionKey);
	if (dirty?.timer) clearTimeout(dirty.timer);
	pendingFlushes.delete(sessionKey);
	inFlightFlushes.add(sessionKey);

	const startedAt = Date.now();
	const dirtyAgeMs = dirty ? startedAt - dirty.firstDirtyAt : 0;
	const marker = dirty?.marker ?? null;
	lastDirtyAgeMs = dirtyAgeMs;

	try {
		const built = buildCheckpointBundle(sessionKey);
		if (built.ok === false) {
			localValidationRejectCount += 1;
			logEvent("checkpoint_flush_local_validation_failed", {
				session_id: sessionKey,
				reason,
				error: built.error,
				retryable: built.retryable,
				dirty_age_ms: dirtyAgeMs,
			});
			if (built.retryable && !closed) scheduleFlush(sessionKey, reason);
			return;
		}

		if (reasonRequiresMarkerLease(reason, marker)) {
			if (!marker?.leaseToken || !marker.leaseHolderId) {
				localValidationRejectCount += 1;
				logEvent("checkpoint_flush_skipped_marker_missing_lease", {
					session_id: sessionKey,
					reason,
					checkpoint_version: built.manifest.checkpoint_version,
					marker_checkpoint_version: marker?.checkpointVersion ?? null,
					marker_bundle_hash: marker?.bundleHash ?? null,
					dirty_age_ms: dirtyAgeMs,
				});
				clearCheckpointLifecycleIfMarker(sessionKey, marker);
				return;
			}
			if (!markerMatchesManifest(marker, built.manifest)) {
				logEvent("checkpoint_flush_skipped_stale_marker", {
					session_id: sessionKey,
					reason,
					marker_lease_holder_id: marker.leaseHolderId,
					marker_lease_token: marker.leaseToken,
					manifest_lease_holder_id: built.manifest.lease_holder_id,
					manifest_lease_token: built.manifest.lease_token,
					checkpoint_version: built.manifest.checkpoint_version,
					dirty_age_ms: dirtyAgeMs,
				});
				clearCheckpointLifecycleIfLease(sessionKey, marker.leaseToken);
				return;
			}
		}

		updateCompactionState(sessionKey, built.latestCompactionId);
		if (reason === "periodic" && built.manifest.bundle_hash === built.bundleHash) {
			lastCheckpointVersion = built.manifest.checkpoint_version;
			lastBundleHash = built.bundleHash;
			await uploadSessionInsights({
				sessionKey,
				agentSessionId: built.agentSessionId,
				checkpointVersion: built.manifest.checkpoint_version,
				bundleHash: built.bundleHash,
				reason,
			});
			return;
		}

		if (reason !== "shutdown" && leaseIsExpired(built.manifest)) {
			const expiredLeaseKey = `${sessionKey}:${built.manifest.lease_token}:${built.manifest.lease_expires_at}`;
			if (!skippedExpiredLeases.has(expiredLeaseKey)) {
				skippedExpiredLeases.add(expiredLeaseKey);
				leaseFailureCount += 1;
				logEvent("checkpoint_flush_skipped_expired_lease", {
					session_id: sessionKey,
					reason,
					checkpoint_version: built.manifest.checkpoint_version,
					lease_expires_at: built.manifest.lease_expires_at,
					dirty_age_ms: dirtyAgeMs,
				});
			}
			return;
		}

		flushAttemptCount += 1;
		if (reason === "compaction") compactionFlushCount += 1;
		if (reason === "shutdown") shutdownFlushCount += 1;

		logEvent("checkpoint_flush_started", {
			session_id: sessionKey,
			reason,
			agent_session_id: built.agentSessionId,
			checkpoint_version: built.manifest.checkpoint_version,
			bundle_hash: built.bundleHash,
			dirty_age_ms: dirtyAgeMs,
			local_bundle_summary: summarizeCheckpointBundle(built.bundle, built.latestCompactionId),
		});
		const result = await checkpointClient.flush({
			agentSessionId: built.agentSessionId,
			holderId: built.manifest.lease_holder_id,
			leaseToken: built.manifest.lease_token,
			expectedCheckpointVersion: built.manifest.checkpoint_version,
			reason,
			bundleHash: built.bundleHash,
			bundle: built.bundle,
			agentSessionTerminalState:
				reason === "stream_finish" || reason === "stream_error" || (reason === "shutdown" && marker != null)
					? marker?.agentSessionTerminalState ?? defaultAgentSessionTerminalState(reason)
					: null,
		});

		const latencyMs = Date.now() - startedAt;
		if (result.ok === false) {
			flushRejectedCount += 1;
			if (isLeaseError(result.error)) leaseFailureCount += 1;
			logEvent("checkpoint_flush_rejected", {
				session_id: sessionKey,
				reason,
				agent_session_id: built.agentSessionId,
				status: result.status,
				error: result.error,
				backend_request_url: result.url,
				backend_response_body: result.body,
				backend_response_text: result.responseText,
				backend_field_errors: flattenBackendValidationErrors(result.body),
				backend_error_summary: summarizeBackendErrorBody(result.body),
				expected_checkpoint_version: built.manifest.checkpoint_version,
				submitted_bundle_hash: built.bundleHash,
				manifest_bundle_hash: built.manifest.bundle_hash,
				local_bundle_summary: summarizeCheckpointBundle(built.bundle, built.latestCompactionId),
				latency_ms: latencyMs,
				dirty_age_ms: dirtyAgeMs,
			});
			return;
		}

		await recordSuccessfulFlush(sessionKey, built.manifest, result.body, reason, latencyMs, dirtyAgeMs);
		await releaseCheckpointLeaseAfterFlush({
			sessionKey,
			agentSessionId: built.agentSessionId,
			manifest: built.manifest,
			reason,
			marker,
		});
	} catch (error) {
		flushRejectedCount += 1;
		logEvent("checkpoint_flush_error", {
			session_id: sessionKey,
			reason,
			error: error instanceof Error ? error.message : String(error),
			dirty_age_ms: dirtyAgeMs,
		});
	} finally {
		inFlightFlushes.delete(sessionKey);
	}
}

async function recordSuccessfulFlush(
	sessionKey: string,
	manifest: CheckpointManifest,
	response: CheckpointFlushResponse,
	reason: CheckpointReason,
	latencyMs: number,
	dirtyAgeMs: number,
) {
	let normalizedMaterialization:
		| {
				threadId: string;
				deletedHistoryFiles: string[];
				latestCompactionId: string | null;
		  }
		| null = null;
	if (response.normalized) {
		if (!isCheckpointBundle(response.bundle)) {
			flushRejectedCount += 1;
			logEvent("checkpoint_flush_error", {
				session_id: sessionKey,
				reason,
				error: "normalized_checkpoint_bundle_missing",
				checkpoint_version: response.checkpoint_version,
				bundle_hash: response.bundle_hash,
				normalized: response.normalized,
				normalized_reason: response.normalized_reason ?? null,
				latency_ms: latencyMs,
				dirty_age_ms: dirtyAgeMs,
			});
			return;
		}
		normalizedMaterialization = materializeNormalizedCheckpointBundle(sessionKey, response.bundle);
	}

	flushSuccessCount += 1;
	if (response.noop) flushNoopCount += 1;
	lastCheckpointVersion = response.checkpoint_version;
	lastBundleHash = response.bundle_hash;
	writeCheckpointManifest(sessionKey, manifest, {
		checkpointVersion: response.checkpoint_version,
		bundleHash: response.bundle_hash,
		reason,
	});
	logEvent(reason === "shutdown" ? "shutdown_flush_completed" : "checkpoint_flush_completed", {
		session_id: sessionKey,
		reason,
		checkpoint_version: response.checkpoint_version,
		bundle_hash: response.bundle_hash,
		noop: response.noop,
		normalized: response.normalized === true,
		normalized_reason: response.normalized_reason ?? null,
		retention: response.retention ?? null,
		normalized_thread_id: normalizedMaterialization?.threadId ?? null,
		normalized_latest_compaction_id: normalizedMaterialization?.latestCompactionId ?? null,
		deleted_history_files: normalizedMaterialization?.deletedHistoryFiles ?? [],
		updated_at: response.updated_at,
		latency_ms: latencyMs,
		dirty_age_ms: dirtyAgeMs,
	});
	await uploadSessionInsights({
		sessionKey,
		agentSessionId: response.agent_session_id,
		checkpointVersion: response.checkpoint_version,
		bundleHash: response.bundle_hash,
		reason,
	});
}

function scheduleFlush(
	sessionKey: string,
	reason: CheckpointReason,
	options: { immediate?: boolean; marker?: CheckpointMarker | null } = {},
) {
	if (closed || !sessionKey) return;
	if (!hasCheckpointManifest(sessionKey)) {
		if (!skippedUnmanagedSessions.has(sessionKey)) {
			skippedUnmanagedSessions.add(sessionKey);
			logEvent("checkpoint_flush_skipped_unmanaged_session", {
				session_id: sessionKey,
				reason,
			});
		}
		return;
	}
	const existing = pendingFlushes.get(sessionKey);
	if (existing?.timer) clearTimeout(existing.timer);

	const dirty: DirtySession = {
		firstDirtyAt: existing?.firstDirtyAt ?? Date.now(),
		reason: existing ? chooseReason(existing.reason, reason) : reason,
		timer: null,
		marker: options.marker ?? existing?.marker ?? null,
	};
	pendingFlushes.set(sessionKey, dirty);

	const waitMs = options.immediate ? 0 : debounceMs;
	dirty.timer = setTimeout(() => {
		void flushSession(sessionKey, dirty.reason);
	}, waitMs);
}

function getSessionKey(fileName: string): string | null {
	if (fileName.endsWith(".conversation.jsonl") || fileName.endsWith(".history.json")) {
		return null;
	}
	for (const suffix of [".meta.json", ".jsonl"]) {
		if (fileName.endsWith(suffix)) return fileName.slice(0, -suffix.length);
	}
	if (fileName.endsWith(".thread.json")) {
		const parsed = readJsonObject(path.join(sessionDir, fileName));
		const runtimeSessionId = parsed?.runtimeSessionId;
		return typeof runtimeSessionId === "string" && runtimeSessionId.trim() ? runtimeSessionId.trim() : null;
	}
	return null;
}

function getManifestSessionKey(fileName: string): string | null {
	const suffix = ".manifest.json";
	return fileName.endsWith(suffix) ? fileName.slice(0, -suffix.length) : null;
}

function readCheckpointMarker(fileName: string): CheckpointMarker | null {
	const markerSuffix = ".marker.json";
	if (!fileName.endsWith(markerSuffix)) return null;
	const markerPath = path.join(checkpointDir, fileName);
	let shouldUnlink = false;
	try {
		const parsed = JSON.parse(readFileSync(markerPath, "utf8"));
		const sessionId = parsed?.session_id;
		if (typeof sessionId === "string" && sessionId.trim()) {
			const reason = normalizeMarkerReason(parsed?.reason);
			shouldUnlink = true;
			return {
				sessionKey: sessionId.trim(),
				reason,
				leaseHolderId:
					typeof parsed?.lease_holder_id === "string" && parsed.lease_holder_id.trim()
						? parsed.lease_holder_id.trim()
						: null,
				leaseToken:
					typeof parsed?.lease_token === "string" && parsed.lease_token.trim()
						? parsed.lease_token.trim()
						: null,
				checkpointVersion: normalizeNumericId(parsed?.checkpoint_version),
				bundleHash:
					typeof parsed?.bundle_hash === "string" && parsed.bundle_hash.trim()
						? parsed.bundle_hash.trim()
					: null,
				agentSessionTerminalState:
					parseAgentSessionTerminalState(parsed?.agent_session_terminal_state) ??
					defaultAgentSessionTerminalState(reason),
			};
		}
		shouldUnlink = true;
		logEvent("checkpoint_marker_invalid", {
			file_name: fileName,
			error: "missing_session_id",
		});
		return null;
	} catch (error) {
		logEvent("checkpoint_marker_read_failed", {
			file_name: fileName,
			error: error instanceof Error ? error.message : String(error),
		});
		return null;
	} finally {
		if (shouldUnlink) {
			try {
				unlinkSync(markerPath);
			} catch {
				// Marker removal is best-effort. Periodic scanning still protects durability.
			}
		}
	}
	return null;
}

function listDiscoveredSessionKeys(): string[] {
	const keys = new Set<string>();
	if (existsSync(manifestDir)) {
		for (const entry of readdirSync(manifestDir, { withFileTypes: true })) {
			if (!entry.isFile()) continue;
			const key = getManifestSessionKey(entry.name);
			if (key) keys.add(key);
		}
	}
	return [...keys].sort();
}

function maybeScheduleCompactionFlush(sessionKey: string) {
	const result = readCompleteJsonl(getSessionPath(sessionKey));
	if (!result.ok) return;
	const previous = latestCompactionIds.get(sessionKey);
	if (result.latestCompactionId) latestCompactionIds.set(sessionKey, result.latestCompactionId);
	if (previous && result.latestCompactionId && previous !== result.latestCompactionId) {
		scheduleFlush(sessionKey, "compaction", { immediate: true });
	}
}

function scanExistingSessions(reason: CheckpointReason) {
	for (const sessionKey of listDiscoveredSessionKeys()) {
		ensureSessionOverrideWatcher(sessionKey);
		observeRestoreManifest(sessionKey);
		maybeScheduleCompactionFlush(sessionKey);
		scheduleFlush(sessionKey, reason);
	}
}

function scanCheckpointMarkers() {
	if (!existsSync(checkpointDir)) return;
	for (const entry of readdirSync(checkpointDir, { withFileTypes: true })) {
		if (!entry.isFile()) continue;
		const marker = readCheckpointMarker(entry.name);
		if (marker) scheduleFlush(marker.sessionKey, marker.reason, { immediate: true, marker });
	}
}

type SidecarLogSeverity = "INFO" | "WARNING" | "ERROR";

const sidecarLogMessages: Record<string, string> = {
	checkpoint_flush_completed: "Checkpoint bundle flushed to backend.",
	checkpoint_flush_error: "Checkpoint bundle flush failed with an unexpected error.",
	checkpoint_flush_local_validation_failed: "Checkpoint bundle failed local validation before flush.",
	checkpoint_flush_rejected: "Checkpoint bundle flush was rejected by the backend. See backend_error and local_bundle_summary for diagnostics.",
	checkpoint_flush_skipped_marker_missing_lease: "Terminal checkpoint marker was skipped because it did not include lease identity.",
	checkpoint_flush_skipped_expired_lease: "Checkpoint bundle flush skipped because the local lease is expired.",
	checkpoint_flush_skipped_stale_marker: "Terminal checkpoint marker was skipped because it does not match the active manifest lease.",
	checkpoint_flush_skipped_unmanaged_session: "Checkpoint flush skipped because no checkpoint manifest exists for this session.",
	checkpoint_flush_started: "Checkpoint bundle flush started.",
	checkpoint_lease_release_failed: "Checkpoint lease release failed after terminal flush.",
	checkpoint_lease_released: "Checkpoint lease released after terminal flush.",
	checkpoint_marker_invalid: "Checkpoint marker was invalid and could not be used.",
	checkpoint_marker_read_failed: "Checkpoint marker could not be read yet; it will be retried.",
	checkpoint_restore_observed: "Restored checkpoint manifest observed by sidecar.",
	checkpoint_sidecar_metrics: "Checkpoint sidecar metrics snapshot.",
	checkpoint_sidecar_started: "Checkpoint sidecar started.",
	checkpoint_sidecar_stopped: "Checkpoint sidecar stopped.",
	checkpoint_sidecar_watch_failed: "Checkpoint sidecar could not watch a directory.",
	checkpoint_sidecar_watch_started: "Checkpoint sidecar watch started.",
	session_insights_upload_failed: "Session insights upload was rejected by the backend.",
	session_insights_upload_skipped: "Session insights upload skipped because local insight projection is unavailable.",
	session_insights_uploaded: "Session insights snapshot uploaded to backend.",
	shutdown_flush_completed: "Shutdown checkpoint bundle flushed to backend.",
};

function sidecarLogSeverity(event: string): SidecarLogSeverity {
	if (event === "checkpoint_flush_error" || event === "checkpoint_sidecar_watch_failed") return "ERROR";
	if (
		event === "checkpoint_flush_local_validation_failed" ||
		event === "checkpoint_flush_rejected" ||
		event === "checkpoint_flush_skipped_expired_lease" ||
		event === "checkpoint_flush_skipped_marker_missing_lease" ||
		event === "checkpoint_flush_skipped_stale_marker" ||
		event === "checkpoint_lease_release_failed" ||
		event === "checkpoint_marker_invalid" ||
		event === "checkpoint_marker_read_failed" ||
		event === "session_insights_upload_failed" ||
		event === "session_insights_upload_skipped"
	) {
		return "WARNING";
	}
	return "INFO";
}

function logEvent(event: string, data: Record<string, unknown>) {
	const severity = sidecarLogSeverity(event);
	if (!shouldEmitStructuredLog({ severity, component: "astro-checkpoint-sidecar", event })) {
		return;
	}
	const normalizedData = normalizeStructuredLogRecord(data) ?? {};
	const sessionId = resolveStructuredLogSessionId(normalizedData);
	const payload = {
		severity,
		time: new Date().toISOString(),
		component: "astro-checkpoint-sidecar",
		event,
		message: sidecarLogMessages[event] ?? event,
		...(sessionId ? { session_id: sessionId } : {}),
		...normalizedData,
	};
	const serialized = JSON.stringify(payload);
	if (severity === "ERROR" || severity === "WARNING") {
		console.error(serialized);
		return;
	}
	console.log(serialized);
}

function logMetrics() {
	logEvent("checkpoint_sidecar_metrics", {
		observed_restore_manifest_count: observedRestoreManifestCount,
		flush_attempt_count: flushAttemptCount,
		flush_success_count: flushSuccessCount,
		flush_noop_count: flushNoopCount,
		flush_rejected_count: flushRejectedCount,
		lease_failure_count: leaseFailureCount,
		local_validation_reject_count: localValidationRejectCount,
		compaction_flush_count: compactionFlushCount,
		shutdown_flush_count: shutdownFlushCount,
		session_insights_upload_attempt_count: insightsUploadAttemptCount,
		session_insights_upload_success_count: insightsUploadSuccessCount,
		session_insights_upload_failure_count: insightsUploadFailureCount,
		session_insights_upload_skipped_count: insightsUploadSkippedCount,
		active_dirty_sessions: pendingFlushes.size,
		in_flight_flushes: inFlightFlushes.size,
		last_dirty_age_ms: lastDirtyAgeMs,
		last_checkpoint_version: lastCheckpointVersion,
		last_bundle_hash: lastBundleHash,
	});
}

function closeWatchers() {
	while (watchers.length) watchers.pop()?.close();
	watchedOverrideDirs.clear();
}

function watchDirectory(
	dir: string,
	label: string,
	handler: (fileName: string) => void,
) {
	mkdirSync(dir, { recursive: true });
	const watcher = watch(dir, { persistent: true }, (_eventType, fileName) => {
		if (!fileName) return;
		handler(fileName.toString());
	});
	watchers.push(watcher);
	logEvent("checkpoint_sidecar_watch_started", { label, dir });
}

function ensureSessionOverrideWatcher(sessionKey: string) {
	const dir = path.join(sessionOverridesDir, sessionKey);
	if (watchedOverrideDirs.has(dir) || !existsSync(dir)) return;
	try {
		const stat = statSync(dir);
		if (!stat.isDirectory()) return;
		const watcher = watch(dir, { persistent: true }, (_eventType, fileName) => {
			if (!fileName || fileName.toString() !== "settings.json") return;
			scheduleFlush(sessionKey, "periodic");
		});
		watchers.push(watcher);
		watchedOverrideDirs.add(dir);
		logEvent("checkpoint_sidecar_watch_started", { label: "session-overrides", dir, session_id: sessionKey });
	} catch {
		// The override directory may be created and removed during session setup. Scans retry later.
	}
}

async function shutdown(signal: NodeJS.Signals | "preStop") {
	if (closed) return;
	closed = true;
	closeWatchers();

	for (const dirty of pendingFlushes.values()) {
		if (dirty.timer) clearTimeout(dirty.timer);
	}

	const sessionKeys = new Set([...listDiscoveredSessionKeys(), ...pendingFlushes.keys()]);
	const flushes = [...sessionKeys].map((sessionKey) => flushSession(sessionKey, "shutdown"));
	await Promise.race([
		Promise.allSettled(flushes),
		new Promise((resolve) => setTimeout(resolve, shutdownTimeoutMs)),
	]);
	pendingFlushes.clear();
	logMetrics();
	logEvent("checkpoint_sidecar_stopped", { signal, shutdown_timeout_ms: shutdownTimeoutMs });
	process.exit(0);
}

mkdirSync(sessionDir, { recursive: true });
mkdirSync(manifestDir, { recursive: true });
mkdirSync(checkpointDir, { recursive: true });
mkdirSync(lifecycleDir, { recursive: true });
mkdirSync(sessionOverridesDir, { recursive: true });

logEvent("checkpoint_sidecar_started", {
	sessions_dir: sessionDir,
	manifests_dir: manifestDir,
	checkpoints_dir: checkpointDir,
	lifecycle_dir: lifecycleDir,
	session_overrides_dir: sessionOverridesDir,
	debounce_ms: debounceMs,
	scan_interval_ms: scanIntervalMs,
	periodic_flush_ms: periodicFlushMs,
	metrics_interval_ms: metricsIntervalMs,
});

watchDirectory(sessionDir, "sessions", (fileName) => {
	const sessionKey = getSessionKey(fileName);
	if (!sessionKey) return;
	if (fileName.endsWith(".jsonl")) maybeScheduleCompactionFlush(sessionKey);
	scheduleFlush(sessionKey, "periodic");
});

watchDirectory(manifestDir, "manifests", (fileName) => {
	const sessionKey = getManifestSessionKey(fileName);
	if (sessionKey) observeRestoreManifest(sessionKey);
});

watchDirectory(checkpointDir, "checkpoints", (fileName) => {
	const marker = readCheckpointMarker(fileName);
	if (marker) scheduleFlush(marker.sessionKey, marker.reason, { immediate: true, marker });
});

watchDirectory(sessionOverridesDir, "session-overrides-root", (fileName) => {
	if (!fileName || fileName.includes(".")) return;
	ensureSessionOverrideWatcher(fileName);
	scheduleFlush(fileName, "periodic");
});

scanExistingSessions("periodic");
scanCheckpointMarkers();

const scanTimer = setInterval(() => {
	scanExistingSessions("periodic");
	scanCheckpointMarkers();
}, scanIntervalMs);

const periodicFlushTimer = setInterval(() => {
	scanExistingSessions("periodic");
}, periodicFlushMs);

const metricsTimer = setInterval(logMetrics, metricsIntervalMs);

process.once("SIGINT", (signal) => {
	void shutdown(signal);
});
process.once("SIGTERM", (signal) => {
	void shutdown(signal);
});
process.once("exit", () => {
	clearInterval(scanTimer);
	clearInterval(periodicFlushTimer);
	clearInterval(metricsTimer);
	closeWatchers();
});
