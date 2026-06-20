import assert from "node:assert/strict";
import { test } from "node:test";
import { resolveBackendAdapter } from "../adapters/backend.js";
import type { BackendAdapter } from "../adapters/types.js";
import { requireBackendCapability } from "../adapters/types.js";

test("backend adapter defaults to Main Sequence for current deployments", () => {
	const adapter = resolveBackendAdapter({});

	assert.equal(adapter.name, "mainsequence");
	assert.ok(requireBackendCapability(adapter, "auth", adapter.auth));
	assert.ok(requireBackendCapability(adapter, "identity", adapter.identity));
	assert.ok(requireBackendCapability(adapter, "sessions", adapter.sessions));
	assert.ok(requireBackendCapability(adapter, "checkpoints", adapter.checkpoints));
	assert.ok(requireBackendCapability(adapter, "providerCredentials", adapter.providerCredentials));
	assert.ok(requireBackendCapability(adapter, "capabilities", adapter.capabilities));
	assert.ok(requireBackendCapability(adapter, "modelCatalog", adapter.modelCatalog));
	assert.ok(requireBackendCapability(adapter, "bootstrap", adapter.bootstrap));
});

test("backend adapter rejects unsupported backend names explicitly", () => {
	assert.throws(
		() => resolveBackendAdapter({ ASTRO_BACKEND: "custom" }),
		/Unsupported ASTRO_BACKEND "custom"/,
	);
});

test("backend adapter reports missing required capabilities explicitly", () => {
	const adapter = { name: "mainsequence" } as BackendAdapter;

	assert.throws(
		() => requireBackendCapability(adapter, "checkpoints", adapter.checkpoints),
		/Backend adapter "mainsequence" does not provide required capability "checkpoints"/,
	);
});
