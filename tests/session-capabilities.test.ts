import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
	chmodSync,
	existsSync,
	mkdtempSync,
	mkdirSync,
	readFileSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import {
	AgentCapabilitiesClient,
	clearSessionCapabilityMaterializationCache,
	materializeSessionCapabilities,
} from "../interface/stream/session-capabilities.js";

const originalFetch = globalThis.fetch;

function sha256(value: string): string {
	return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

afterEach(() => {
	globalThis.fetch = originalFetch;
	clearSessionCapabilityMaterializationCache();
});

type JsonRoute = {
	status?: number;
	body: unknown | (() => unknown);
};

function createRuntimeEnv(root: string): NodeJS.ProcessEnv {
	const binDir = path.join(root, "bin");
	mkdirSync(binDir, { recursive: true });
	const pythonPath = path.join(binDir, "python3");
	writeFileSync(pythonPath, "#!/bin/sh\nprintf '%s\\n' '{\"Authorization\":\"Bearer test-token\"}'\n");
	chmodSync(pythonPath, 0o755);
	return {
		...process.env,
		PATH: `${binDir}${path.delimiter}${process.env.PATH ?? ""}`,
		MAINSEQUENCE_BACKEND: "https://backend.test",
	};
}

function installFetchRoutes(routes: Record<string, JsonRoute>) {
	const requests: Array<{ url: string; headers: Record<string, string> }> = [];
	globalThis.fetch = async (input, init) => {
		const url = String(input);
		const headers = Object.fromEntries(new Headers(init?.headers).entries());
		requests.push({ url, headers });
		const route = routes[url];
		if (!route) {
			return new Response(JSON.stringify({ error: `missing route: ${url}` }), { status: 404 });
		}
		const body = typeof route.body === "function" ? route.body() : route.body;
		return new Response(JSON.stringify(body), {
			status: route.status ?? 200,
			headers: { "Content-Type": "application/json" },
		});
	};
	return requests;
}

function capability(options: {
	uid: string;
	kind?: "skill" | "prompt" | "extension";
	sourceType?: "inline" | "registry" | "repository" | "api" | "external";
	capabilityPath?: string;
	hasContent?: boolean;
	contentSha256?: string;
	updatedAt?: string;
}) {
	return {
		uid: options.uid,
		name: options.uid,
		kind: options.kind ?? "skill",
		source_type: options.sourceType ?? "inline",
		source_ref: "",
		capability_path: options.capabilityPath ?? `skills/${options.uid}/SKILL.md`,
		is_editable: true,
		description: "",
		metadata: {},
		content_file: null,
		content_sha256: options.contentSha256 ?? "",
		content_mime_type: "text/markdown",
		content_size: 0,
		has_content: options.hasContent ?? true,
		created_by_user_uid: "user-uid",
		updated_at: options.updatedAt ?? "2026-06-14T00:00:00.000Z",
	};
}

function sessionBinding(options: {
	uid: string;
	sessionUid?: string;
	capabilityUid?: string;
	isEnabled?: boolean;
	bindingSourceType?: "inline" | "registry" | "repository" | "api" | "external";
	capabilitySourceType?: "inline" | "registry" | "repository" | "api" | "external";
	kind?: "skill" | "prompt" | "extension";
	capabilityPath?: string;
	hasContent?: boolean;
	contentSha256?: string;
	updatedAt?: string;
}) {
	const capabilityUid = options.capabilityUid ?? options.uid;
	return {
		uid: options.uid,
		agent_session_uid: options.sessionUid ?? "session-a",
		capability_uid: capabilityUid,
		capability: capability({
			uid: capabilityUid,
			kind: options.kind,
			sourceType: options.capabilitySourceType,
			capabilityPath: options.capabilityPath,
			hasContent: options.hasContent,
			contentSha256: options.contentSha256,
			updatedAt: options.updatedAt,
		}),
		role: "",
		sort_order: 0,
		is_enabled: options.isEnabled ?? true,
		is_locked: false,
		configuration: {},
		source_type: options.bindingSourceType ?? "inline",
		source_ref: "",
		updated_at: options.updatedAt ?? "2026-06-14T00:00:00.000Z",
	};
}

function agentBinding(options: {
	uid: string;
	agentUid?: string;
	capabilityUid?: string;
	bindingSourceType?: "inline" | "registry" | "repository" | "api" | "external";
	capabilitySourceType?: "inline" | "registry" | "repository" | "api" | "external";
}) {
	const capabilityUid = options.capabilityUid ?? options.uid;
	return {
		uid: options.uid,
		agent_uid: options.agentUid ?? "agent-a",
		capability_uid: capabilityUid,
		capability: capability({
			uid: capabilityUid,
			sourceType: options.capabilitySourceType,
		}),
		role: "",
		sort_order: 0,
		is_enabled: true,
		is_locked: false,
		configuration: {},
		source_type: options.bindingSourceType ?? "inline",
		source_ref: "",
		updated_at: "2026-06-14T00:00:00.000Z",
	};
}

test("agent capability bindings preserve repository versus extra classification fields", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-capabilities-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = createRuntimeEnv(root);
	const requests = installFetchRoutes({
		"https://backend.test/orm/api/agents/v1/agents/agent-a/capabilities/": {
			body: [
				agentBinding({
					uid: "repo-binding",
					bindingSourceType: "repository",
					capabilitySourceType: "repository",
				}),
				agentBinding({
					uid: "manual-binding",
					bindingSourceType: "inline",
					capabilitySourceType: "api",
				}),
			],
		},
	});

	const result = await new AgentCapabilitiesClient({ env }).listAgentCapabilities("agent-a");

	if (!result.ok) assert.fail("Expected agent capability list to succeed.");
	assert.equal(result.body.length, 2);
	assert.equal(result.body[0].source_type, "repository");
	assert.equal(result.body[0].capability.source_type, "repository");
	assert.equal(result.body[1].source_type, "inline");
	assert.equal(result.body[1].capability.source_type, "api");
	assert.equal(requests[0].headers.authorization, "Bearer test-token");
});

test("session capability materialization writes only enabled non-repository skills", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-capabilities-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = createRuntimeEnv(root);
	const assetsRoot = path.join(root, "session-assets");
	installFetchRoutes({
		"https://backend.test/orm/api/agents/v1/sessions/session-a/capabilities/": {
			body: [
				sessionBinding({
					uid: "inline-skill",
					capabilityUid: "inline-cap",
					capabilityPath: "skills/custom/inline/SKILL.md",
				}),
				sessionBinding({
					uid: "disabled-skill",
					capabilityUid: "disabled-cap",
					isEnabled: false,
				}),
				sessionBinding({
					uid: "prompt-capability",
					capabilityUid: "prompt-cap",
					kind: "prompt",
				}),
				sessionBinding({
					uid: "repo-binding-skill",
					capabilityUid: "repo-binding-cap",
					bindingSourceType: "repository",
				}),
				sessionBinding({
					uid: "repo-content-skill",
					capabilityUid: "repo-content-cap",
					capabilitySourceType: "repository",
				}),
				sessionBinding({
					uid: "missing-content-skill",
					capabilityUid: "missing-content-cap",
					hasContent: false,
				}),
				sessionBinding({
					uid: "invalid-path-skill",
					capabilityUid: "invalid-path-cap",
					capabilityPath: "../escape/SKILL.md",
				}),
			],
		},
		"https://backend.test/orm/api/agents/v1/capabilities/inline-cap/content/": {
			body: {
				content: "# Inline skill\n",
				content_sha256: "sha256:inline",
				content_mime_type: "text/markdown",
				content_size: 15,
			},
		},
	});

	const result = await materializeSessionCapabilities({
		agentSessionUid: "session-a",
		sessionAssetsRoot: assetsRoot,
		env,
	});

	if (!result.ok) assert.fail("Expected session capability materialization to succeed.");
	assert.equal(result.value.bindingCount, 7);
	assert.equal(result.value.enabledSkillBindingCount, 5);
	assert.equal(result.value.materializedSkillCount, 1);
	assert.deepEqual(result.value.skipped, {
		disabled: 1,
		unsupportedKind: 1,
		repositoryProjectedDefault: 2,
		missingContent: 1,
		invalidPath: 1,
	});
	assert.deepEqual(result.value.settingsSkillPaths, [result.value.skillsRoot]);
	assert.equal(
		readFileSync(path.join(result.value.skillsRoot, "custom", "inline", "SKILL.md"), "utf8"),
		"# Inline skill\n",
	);
	assert.equal(
		existsSync(path.join(assetsRoot, "session-a", ".agents", "skills", "repo-binding-cap", "SKILL.md")),
		false,
	);
});

test("session capability materialization is isolated by session uid", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-capabilities-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = createRuntimeEnv(root);
	const assetsRoot = path.join(root, "session-assets");
	installFetchRoutes({
		"https://backend.test/orm/api/agents/v1/sessions/session-a/capabilities/": {
			body: [
				sessionBinding({
					uid: "session-a-skill",
					sessionUid: "session-a",
					capabilityUid: "session-a-cap",
					capabilityPath: "skills/shared/name/SKILL.md",
				}),
			],
		},
		"https://backend.test/orm/api/agents/v1/sessions/session-b/capabilities/": {
			body: [
				sessionBinding({
					uid: "session-b-skill",
					sessionUid: "session-b",
					capabilityUid: "session-b-cap",
					capabilityPath: "skills/shared/name/SKILL.md",
				}),
			],
		},
		"https://backend.test/orm/api/agents/v1/capabilities/session-a-cap/content/": {
			body: {
				content: "# Session A\n",
				content_sha256: "sha256:a",
				content_mime_type: "text/markdown",
				content_size: 12,
			},
		},
		"https://backend.test/orm/api/agents/v1/capabilities/session-b-cap/content/": {
			body: {
				content: "# Session B\n",
				content_sha256: "sha256:b",
				content_mime_type: "text/markdown",
				content_size: 12,
			},
		},
	});

	const sessionA = await materializeSessionCapabilities({
		agentSessionUid: "session-a",
		sessionAssetsRoot: assetsRoot,
		env,
	});
	const sessionB = await materializeSessionCapabilities({
		agentSessionUid: "session-b",
		sessionAssetsRoot: assetsRoot,
		env,
	});

	if (!sessionA.ok) assert.fail("Expected session A materialization to succeed.");
	if (!sessionB.ok) assert.fail("Expected session B materialization to succeed.");
	assert.notEqual(sessionA.value.skillsRoot, sessionB.value.skillsRoot);
	assert.equal(readFileSync(path.join(sessionA.value.skillsRoot, "shared", "name", "SKILL.md"), "utf8"), "# Session A\n");
	assert.equal(readFileSync(path.join(sessionB.value.skillsRoot, "shared", "name", "SKILL.md"), "utf8"), "# Session B\n");
});

test("session capability materialization rejects conflicting session paths", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-capabilities-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = createRuntimeEnv(root);
	installFetchRoutes({
		"https://backend.test/orm/api/agents/v1/sessions/session-a/capabilities/": {
			body: [
				sessionBinding({
					uid: "first-skill",
					capabilityUid: "first-cap",
					capabilityPath: "skills/collide/SKILL.md",
				}),
				sessionBinding({
					uid: "second-skill",
					capabilityUid: "second-cap",
					capabilityPath: "skills/collide/SKILL.md",
				}),
			],
		},
		"https://backend.test/orm/api/agents/v1/capabilities/first-cap/content/": {
			body: {
				content: "# First\n",
				content_sha256: "sha256:first",
				content_mime_type: "text/markdown",
				content_size: 8,
			},
		},
		"https://backend.test/orm/api/agents/v1/capabilities/second-cap/content/": {
			body: {
				content: "# Second\n",
				content_sha256: "sha256:second",
				content_mime_type: "text/markdown",
				content_size: 9,
			},
		},
	});

	const result = await materializeSessionCapabilities({
		agentSessionUid: "session-a",
		sessionAssetsRoot: path.join(root, "session-assets"),
		env,
	});

	if (result.ok) assert.fail("Expected duplicate session skill paths to be rejected.");
	const failure = result as Extract<typeof result, { ok: false }>;
	assert.equal(failure.statusCode, 409);
	assert.equal(failure.error, "session_capability_path_collision");
});

test("session capability materialization reuses cached zero bindings", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-capabilities-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = {
		...createRuntimeEnv(root),
		ASTRO_SESSION_CAPABILITY_CACHE_TTL_MS: "600000",
	};
	const assetsRoot = path.join(root, "session-assets");
	const requests = installFetchRoutes({
		"https://backend.test/orm/api/agents/v1/sessions/session-empty/capabilities/": {
			body: [],
		},
	});

	const first = await materializeSessionCapabilities({
		agentSessionUid: "session-empty",
		sessionAssetsRoot: assetsRoot,
		env,
	});
	const second = await materializeSessionCapabilities({
		agentSessionUid: "session-empty",
		sessionAssetsRoot: assetsRoot,
		env,
	});

	if (!first.ok) assert.fail("Expected first zero capability materialization to succeed.");
	if (!second.ok) assert.fail("Expected second zero capability materialization to succeed.");
	assert.equal(first.cacheHit, false);
	assert.equal(second.cacheHit, true);
	assert.equal(second.cacheReason, "zero_session_capabilities");
	assert.equal(first.value.bindingCount, 0);
	assert.equal(second.value.bindingCount, 0);
	assert.equal(requests.length, 1);
	assert.equal(existsSync(path.join(assetsRoot, "session-empty", ".agents", "skills")), false);
});

test("session capability materialization reuses unchanged non-zero binding signatures", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-capabilities-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = createRuntimeEnv(root);
	const assetsRoot = path.join(root, "session-assets");
	const requests = installFetchRoutes({
		"https://backend.test/orm/api/agents/v1/sessions/session-cached/capabilities/": {
			body: [
				sessionBinding({
					uid: "cached-skill",
					sessionUid: "session-cached",
					capabilityUid: "cached-cap",
					capabilityPath: "skills/cached/SKILL.md",
					contentSha256: sha256("# Cached\n"),
				}),
			],
		},
		"https://backend.test/orm/api/agents/v1/capabilities/cached-cap/content/": {
			body: {
				content: "# Cached\n",
				content_sha256: sha256("# Cached\n"),
				content_mime_type: "text/markdown",
				content_size: 9,
			},
		},
	});

	const first = await materializeSessionCapabilities({
		agentSessionUid: "session-cached",
		sessionAssetsRoot: assetsRoot,
		env,
	});
	const second = await materializeSessionCapabilities({
		agentSessionUid: "session-cached",
		sessionAssetsRoot: assetsRoot,
		env,
	});

	if (!first.ok) assert.fail("Expected first non-zero materialization to succeed.");
	if (!second.ok) assert.fail("Expected second non-zero materialization to succeed.");
	assert.equal(first.cacheHit, false);
	assert.equal(second.cacheHit, true);
	assert.equal(second.cacheReason, "capability_signature_unchanged");
	assert.equal(second.value.materializedSkillCount, 1);
	assert.equal(readFileSync(path.join(second.value.skillsRoot, "cached", "SKILL.md"), "utf8"), "# Cached\n");
	assert.deepEqual(
		requests.map((request) => request.url),
		[
			"https://backend.test/orm/api/agents/v1/sessions/session-cached/capabilities/",
			"https://backend.test/orm/api/agents/v1/capabilities/cached-cap/content/",
			"https://backend.test/orm/api/agents/v1/sessions/session-cached/capabilities/",
		],
	);
});

test("session capability materialization invalidates cache when binding signature changes", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-capabilities-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = createRuntimeEnv(root);
	const assetsRoot = path.join(root, "session-assets");
	let currentVersion: "first" | "second" = "first";
	const requests = installFetchRoutes({
		"https://backend.test/orm/api/agents/v1/sessions/session-changing/capabilities/": {
			body: () => [
				sessionBinding({
					uid: "changing-skill",
					sessionUid: "session-changing",
					capabilityUid: "changing-cap",
					capabilityPath: "skills/changing/SKILL.md",
					contentSha256: sha256(currentVersion === "first" ? "# First\n" : "# Second\n"),
					updatedAt:
						currentVersion === "first"
							? "2026-06-14T00:00:00.000Z"
							: "2026-06-14T00:01:00.000Z",
				}),
			],
		},
		"https://backend.test/orm/api/agents/v1/capabilities/changing-cap/content/": {
			body: () => ({
				content: currentVersion === "first" ? "# First\n" : "# Second\n",
				content_sha256: sha256(currentVersion === "first" ? "# First\n" : "# Second\n"),
				content_mime_type: "text/markdown",
				content_size: currentVersion === "first" ? 8 : 9,
			}),
		},
	});

	const first = await materializeSessionCapabilities({
		agentSessionUid: "session-changing",
		sessionAssetsRoot: assetsRoot,
		env,
	});
	currentVersion = "second";
	const second = await materializeSessionCapabilities({
		agentSessionUid: "session-changing",
		sessionAssetsRoot: assetsRoot,
		env,
	});

	if (!first.ok) assert.fail("Expected first changing materialization to succeed.");
	if (!second.ok) assert.fail("Expected second changing materialization to succeed.");
	assert.equal(second.cacheHit, false);
	assert.equal(second.cacheInvalidationReason, "capability_signature_changed");
	assert.equal(readFileSync(path.join(second.value.skillsRoot, "changing", "SKILL.md"), "utf8"), "# Second\n");
	assert.equal(
		requests.filter((request) => request.url.endsWith("/sessions/session-changing/capabilities/")).length,
		2,
	);
	assert.equal(
		requests.filter((request) => request.url.endsWith("/capabilities/changing-cap/content/")).length,
		2,
	);
});
