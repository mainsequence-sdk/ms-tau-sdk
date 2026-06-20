import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { buildAgentUniqueId } from "../interface/stream/mainsequence-agent-registration.js";

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
	assert.match(remoteWorkerDockerfile, /ASTRO_PI_PACKAGE_PATHS=\/app\/tmp_ms_pi/);
	assert.match(localWorkerDockerfile, /ASTRO_PI_PACKAGE_PATHS=\/app\/tmp_ms_pi/);
});

test("Main Sequence backend identity override preserves project-executor unique id", () => {
	assert.equal(
		buildAgentUniqueId({
			agentType: "project-executor",
			userId: "user-1",
			projectId: "project-1",
		}),
		"project-executor",
	);
	assert.equal(
		buildAgentUniqueId({
			agentType: "astro-orchestrator",
			userId: "user-1",
			projectId: "project-1",
		}),
		"astro-orchestrator_user-1_project-1",
	);
});
