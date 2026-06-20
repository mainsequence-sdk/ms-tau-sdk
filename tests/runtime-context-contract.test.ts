import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { resolveBackendAdapter } from "../adapters/backend.js";
import { requireBackendCapability } from "../adapters/types.js";

const repoRoot = new URL("..", import.meta.url).pathname;

function readRepoFile(path: string): string {
	return readFileSync(join(repoRoot, path), "utf8");
}

test("stream runtime no longer branches local architecture on project-executor identity", () => {
	const server = readRepoFile("interface/stream/server.ts");

	assert.doesNotMatch(server, /agentType\s*===\s*["']project-executor["']/);
	assert.doesNotMatch(server, /runtimeProfile\.kind/);
	assert.doesNotMatch(server, /RuntimeProfileKind/);
	assert.doesNotMatch(server, /isImageBackedProjectExecutor/);
	assert.doesNotMatch(server, /fixedWorkerRequiresBackendSessionAuthority/);
	assert.match(server, /type RuntimeContext =/);
	assert.match(server, /projectAttached: boolean/);
});

test("project-attached worker image uses fixed cwd instead of legacy runtime mode envs", () => {
	const remoteWorkerDockerfile = readRepoFile("Dockerfile.remote-worker");
	const localWorkerDockerfile = readRepoFile("Dockerfile.remote-worker.local");
	const compose = readRepoFile("docker-compose.yml");

	for (const source of [remoteWorkerDockerfile, localWorkerDockerfile, compose]) {
		assert.doesNotMatch(source, /ASTRO_EXECUTION_MODE[=:]\s*remote_project_worker/);
		assert.doesNotMatch(source, /ASTRO_FIXED_AGENT_TYPE[=:]\s*project-executor/);
	}
	assert.match(remoteWorkerDockerfile, /ASTRO_FIXED_PROJECT_CWD/);
	assert.match(localWorkerDockerfile, /ASTRO_FIXED_PROJECT_CWD/);
	assert.match(remoteWorkerDockerfile, /ASTRO_PI_PACKAGE_PATHS=\/app\/adapters\/mainsequence\/pi/);
	assert.match(localWorkerDockerfile, /ASTRO_PI_PACKAGE_PATHS=\/app\/adapters\/mainsequence\/pi/);
});

test("Main Sequence backend identity override preserves project-executor unique id", () => {
	const adapter = resolveBackendAdapter({ ASTRO_BACKEND: "mainsequence" });
	const identity = requireBackendCapability(adapter, "identity", adapter.identity);

	assert.equal(
		identity.buildAgentUniqueId({
			agentType: "project-executor",
			userId: "user-1",
			projectId: "project-1",
		}),
		"project-executor",
	);
	assert.equal(
		identity.buildAgentUniqueId({
			agentType: "astro-orchestrator",
			userId: "user-1",
			projectId: "project-1",
		}),
		"astro-orchestrator_user-1_project-1",
	);
});

test("stream runtime depends on backend adapter seam for Main Sequence auth and sessions", () => {
	const server = readRepoFile("interface/stream/server.ts");

	assert.match(server, /resolveBackendAdapter/);
	assert.match(server, /backendAuth\.buildSubprocessEnv/);
	assert.match(server, /backendSessions\.fetchByUid/);
	assert.match(server, /backendCheckpoints\.createClient/);
	assert.match(server, /backendProviderCredentials\.hydrateScoped/);
	assert.match(server, /backendCapabilities\.materializeSession/);
	assert.match(server, /backendModelCatalog\.collectAvailableModels/);
	assert.doesNotMatch(server, /from "\.\.\/\.\.\/adapters\/mainsequence\/runtime-auth\.js"/);
	assert.doesNotMatch(server, /from "\.\/mainsequence-agent-registration\.js"/);
	assert.doesNotMatch(server, /from "\.\/session-checkpoint-client\.js"/);
	assert.doesNotMatch(server, /from "\.\/model-provider-auth\.js"/);
	assert.doesNotMatch(server, /from "\.\/model-provider-scoped-auth\.js"/);
	assert.doesNotMatch(server, /from "\.\/model-provider-signin\.js"/);
});

test("Main Sequence bootstrap behavior lives behind backend adapter bootstrap", () => {
	const coreBootstrap = readRepoFile("runtime/bootstrap/pi-agent-dir.mjs");
	const streamEntrypoint = readRepoFile("bin/astro-stream.ts");
	const localEntrypoint = readRepoFile("bin/astro-pi-local.ts");
	const adapter = readRepoFile("adapters/mainsequence/adapter.ts");
	const adapterBootstrap = readRepoFile("adapters/mainsequence/bootstrap.ts");

	assert.doesNotMatch(coreBootstrap, /MAINSEQUENCE/);
	assert.doesNotMatch(coreBootstrap, /mainsequence/);
	assert.match(streamEntrypoint, /backendAdapter\.bootstrap\?\.prepareRuntime/);
	assert.match(localEntrypoint, /backendBootstrap\?\.prepareRuntime/);
	assert.match(adapter, /bootstrap:\s*{/);
	assert.match(adapterBootstrap, /ASTRO_MAINSEQUENCE_CONFIG_DIR/);
	assert.match(adapterBootstrap, /MAINSEQUENCE_PI_CLI_AUTH_REPAIR_COMMAND/);
	assert.match(adapterBootstrap, /mainsequence-dev/);
});

test("Docker targets separate Astro Core from Main Sequence deployment composition", () => {
	const dockerfile = readRepoFile("Dockerfile");
	const coreStart = dockerfile.indexOf("FROM python:3.11-slim AS astro-core");
	const mainsequenceSourceStart = dockerfile.indexOf("FROM astro-core AS astro-mainsequence-source");

	assert.notEqual(coreStart, -1);
	assert.notEqual(mainsequenceSourceStart, -1);
	const coreStage = dockerfile.slice(coreStart, mainsequenceSourceStart);

	assert.doesNotMatch(coreStage, /adapters\/mainsequence\/pi/);
	assert.doesNotMatch(coreStage, /MAINSEQUENCE_PIP_SPEC/);
	assert.doesNotMatch(coreStage, /ASTRO_MAINSEQUENCE_CONFIG_DIR/);
	assert.match(dockerfile, /FROM astro-core AS astro-mainsequence-source/);
	assert.match(dockerfile, /COPY adapters\/mainsequence\/pi-overlay \.\/adapters\/mainsequence\/pi-overlay/);
	assert.match(dockerfile, /FROM astro-mainsequence-source AS astro-mainsequence/);
	assert.match(dockerfile, /ARG MAINSEQUENCE_PIP_SPEC=mainsequence/);
	assert.match(dockerfile, /FROM astro-mainsequence-runtime AS astro-mainsequence-pi-stream/);
	assert.match(dockerfile, /FROM astro-mainsequence-runtime AS astro-mainsequence-session-checkpoint-sidecar/);
	assert.match(dockerfile, /FROM astro-mainsequence-source AS astro-base/);
	assert.match(dockerfile, /FROM astro-mainsequence-pi-stream AS astro-pi-stream/);
});

test("deployment configs choose explicit Main Sequence image targets", () => {
	const compose = readRepoFile("docker-compose.yml");
	const cloudbuild = readRepoFile("deployment/gcp/cloudbuild.yaml");

	assert.match(compose, /target:\s*astro-mainsequence-pi-stream/);
	assert.match(compose, /target:\s*astro-mainsequence-session-checkpoint-sidecar/);
	assert.match(compose, /ASTRO_BACKEND:\s*mainsequence/);
	assert.match(cloudbuild, /_DOCKER_TARGET:\s*"astro-mainsequence-pi-stream"/);
	assert.match(cloudbuild, /_IMAGE_NAME:\s*"astro\/astro-pi-stream"/);
});
