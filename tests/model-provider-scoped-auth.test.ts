import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
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
	clearScopedProviderCredentialCache,
	hydrateScopedProviderCredentials,
	invalidateScopedProviderCredentialCache,
} from "../interface/stream/model-provider-scoped-auth.js";

const originalFetch = globalThis.fetch;

afterEach(() => {
	globalThis.fetch = originalFetch;
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

	const piAgentDir = path.join(root, "base-agent");
	mkdirSync(piAgentDir, { recursive: true });
	writeFileSync(path.join(piAgentDir, "settings.json"), "{}\n");

	return {
		...process.env,
		PATH: `${binDir}${path.delimiter}${process.env.PATH ?? ""}`,
		MAINSEQUENCE_BACKEND: "https://backend.test",
		PI_CODING_AGENT_DIR: piAgentDir,
		ASTRO_PROVIDER_CREDENTIAL_DIR: path.join(root, "provider-auth"),
		ASTRO_PROVIDER_CREDENTIAL_CACHE_TTL_MS: "600000",
	};
}

function installFetchRoutes(routes: Record<string, JsonRoute>) {
	const requests: Array<{ url: string; method: string; body: string; headers: Record<string, string> }> = [];
	globalThis.fetch = async (input, init) => {
		const url = String(input);
		const method = init?.method ?? "GET";
		const headers = Object.fromEntries(new Headers(init?.headers).entries());
		const body = typeof init?.body === "string" ? init.body : "";
		requests.push({ url, method, body, headers });
		const route = routes[`${method} ${url}`];
		if (!route) {
			return new Response(JSON.stringify({ error: `missing route: ${method} ${url}` }), { status: 404 });
		}
		const responseBody = typeof route.body === "function" ? route.body() : route.body;
		return new Response(JSON.stringify(responseBody), {
			status: route.status ?? 200,
			headers: { "Content-Type": "application/json" },
		});
	};
	return requests;
}

function hydratePayload() {
	return {
		credentials: {
			"openai-codex": {
				status: "active",
				credential_kind: "api_key",
				version: 7,
				credential_hash: "sha256:remote",
				last_flushed_at: new Date().toISOString(),
				pi_credential: {
					type: "api_key",
					key: "test-key",
				},
			},
		},
		missing: [],
		revoked: [],
	};
}

test("provider credential hydration reuses a valid scoped credential manifest", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-provider-auth-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = createRuntimeEnv(root);
	const requests = installFetchRoutes({
		"POST https://backend.test/orm/api/agents/v1/model_provider_credentials/hydrate/": {
			body: hydratePayload(),
		},
		"GET https://backend.test/orm/api/agents/v1/model_provider_credentials/status/?created_by_user_uid=user-a": {
			body: {
				providers: {
					"openai-codex": {
						status: "active",
						credential_kind: "api_key",
						version: 7,
						credential_hash: "sha256:remote",
						last_flushed_at: new Date().toISOString(),
					},
				},
			},
		},
	});

	const first = await hydrateScopedProviderCredentials({
		createdByUser: "user-a",
		agentSessionUid: "session-a",
		sessionKey: "session-a",
		provider: "openai-codex",
		holderId: "holder-a",
		sessionConfigOverrides: null,
		env,
	});
	const second = await hydrateScopedProviderCredentials({
		createdByUser: "user-a",
		agentSessionUid: "session-a",
		sessionKey: "session-a",
		provider: "openai-codex",
		holderId: "holder-a",
		sessionConfigOverrides: null,
		env,
	});

	if (first.ok === false) assert.fail(`Expected first hydrate to succeed: ${first.message}`);
	if (second.ok === false) assert.fail(`Expected second hydrate to succeed: ${second.message}`);
	assert.equal(first.value.cacheHit, false);
	assert.equal(second.value.cacheHit, true);
	assert.equal(second.value.cacheReason, "manifest_valid");
	assert.deepEqual(
		requests.map((request) => request.method),
		["POST", "GET"],
	);
	assert.equal(requests[0].headers.authorization, "Bearer test-token");
	assert.equal(
		readFileSync(path.join(first.value.scopedPiAgentDir, "auth.json"), "utf8"),
		`${JSON.stringify({ "openai-codex": { type: "api_key", key: "test-key" } }, null, 2)}\n`,
	);
	assert.equal(existsSync(path.join(second.value.scopedPiAgentDir, ".astro-provider-credentials.json")), true);

	clearScopedProviderCredentialCache({ sessionKey: "session-a", env });
	assert.equal(existsSync(second.value.scopedPiAgentDir), false);
});

test("provider credential remote version change invalidates cached scoped credentials", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-provider-auth-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = createRuntimeEnv(root);
	let hydrateVersion = 7;
	let remoteVersion = 8;
	const requests = installFetchRoutes({
		"POST https://backend.test/orm/api/agents/v1/model_provider_credentials/hydrate/": {
			body: () => ({
				credentials: {
					"openai-codex": {
						status: "active",
						credential_kind: "api_key",
						version: hydrateVersion,
						credential_hash: `sha256:remote-${hydrateVersion}`,
						last_flushed_at: new Date().toISOString(),
						pi_credential: {
							type: "api_key",
							key: `test-key-${hydrateVersion}`,
						},
					},
				},
				missing: [],
				revoked: [],
			}),
		},
		"GET https://backend.test/orm/api/agents/v1/model_provider_credentials/status/?created_by_user_uid=user-a": {
			body: () => ({
				providers: {
					"openai-codex": {
						status: "active",
						credential_kind: "api_key",
						version: remoteVersion,
						credential_hash: `sha256:remote-${remoteVersion}`,
						last_flushed_at: new Date().toISOString(),
					},
				},
			}),
		},
	});

	const first = await hydrateScopedProviderCredentials({
		createdByUser: "user-a",
		agentSessionUid: "session-a",
		sessionKey: "session-a",
		provider: "openai-codex",
		holderId: "holder-a",
		sessionConfigOverrides: null,
		env,
	});
	if (first.ok === false) assert.fail(`Expected first hydrate to succeed: ${first.message}`);

	hydrateVersion = 8;
	const second = await hydrateScopedProviderCredentials({
		createdByUser: "user-a",
		agentSessionUid: "session-a",
		sessionKey: "session-a",
		provider: "openai-codex",
		holderId: "holder-a",
		sessionConfigOverrides: null,
		env,
	});

	if (second.ok === false) assert.fail(`Expected second hydrate to succeed: ${second.message}`);
	assert.equal(first.value.cacheHit, false);
	assert.equal(second.value.cacheHit, false);
	assert.equal(second.value.cacheInvalidationReason, "remote_version_changed");
	assert.deepEqual(
		requests.map((request) => request.method),
		["POST", "GET", "POST"],
	);
	assert.equal(
		readFileSync(path.join(second.value.scopedPiAgentDir, "auth.json"), "utf8"),
		`${JSON.stringify({ "openai-codex": { type: "api_key", key: "test-key-8" } }, null, 2)}\n`,
	);
});

test("provider credential cache invalidation forces the next hydrate to call backend", async (t) => {
	const root = mkdtempSync(path.join(tmpdir(), "astro-provider-auth-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const env = createRuntimeEnv(root);
	let hydrateVersion = 7;
	const requests = installFetchRoutes({
		"POST https://backend.test/orm/api/agents/v1/model_provider_credentials/hydrate/": {
			body: () => ({
				credentials: {
					"openai-codex": {
						status: "active",
						credential_kind: "api_key",
						version: hydrateVersion,
						credential_hash: `sha256:remote-${hydrateVersion}`,
						last_flushed_at: new Date().toISOString(),
						pi_credential: {
							type: "api_key",
							key: `test-key-${hydrateVersion}`,
						},
					},
				},
				missing: [],
				revoked: [],
			}),
		},
	});

	const first = await hydrateScopedProviderCredentials({
		createdByUser: "user-a",
		agentSessionUid: "session-a",
		sessionKey: "session-a",
		provider: "openai-codex",
		holderId: "holder-a",
		sessionConfigOverrides: null,
		env,
	});
	if (first.ok === false) assert.fail(`Expected first hydrate to succeed: ${first.message}`);

	hydrateVersion = 8;
	invalidateScopedProviderCredentialCache({
		sessionKey: "session-a",
		provider: "openai-codex",
		env,
	});
	const second = await hydrateScopedProviderCredentials({
		createdByUser: "user-a",
		agentSessionUid: "session-a",
		sessionKey: "session-a",
		provider: "openai-codex",
		holderId: "holder-a",
		sessionConfigOverrides: null,
		env,
	});

	if (second.ok === false) assert.fail(`Expected second hydrate to succeed: ${second.message}`);
	assert.equal(first.value.cacheHit, false);
	assert.equal(second.value.cacheHit, false);
	assert.equal(second.value.cacheInvalidationReason, "manifest_missing");
	assert.equal(requests.length, 2);
	assert.equal(
		readFileSync(path.join(second.value.scopedPiAgentDir, "auth.json"), "utf8"),
		`${JSON.stringify({ "openai-codex": { type: "api_key", key: "test-key-8" } }, null, 2)}\n`,
	);
});
