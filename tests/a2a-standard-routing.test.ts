import assert from "node:assert/strict";
import { test } from "node:test";

import {
	A2A_STANDARD_REST_BASE,
	matchA2AStandardRoute,
} from "../interface/stream/a2a-standard-routing.js";

test("A2A route matcher accepts only the standard public A2A surface", () => {
	assert.deepEqual(matchA2AStandardRoute(`${A2A_STANDARD_REST_BASE}/message:send`), {
		kind: "message_send",
	});
	assert.deepEqual(matchA2AStandardRoute(`${A2A_STANDARD_REST_BASE}/message:stream`), {
		kind: "message_stream",
	});
	assert.deepEqual(matchA2AStandardRoute(`${A2A_STANDARD_REST_BASE}/tasks`), {
		kind: "tasks",
	});
	assert.deepEqual(matchA2AStandardRoute(`${A2A_STANDARD_REST_BASE}/tasks/task-1`), {
		kind: "task_get",
		taskId: "task-1",
	});
	assert.deepEqual(matchA2AStandardRoute(`${A2A_STANDARD_REST_BASE}/tasks/task-1:cancel`), {
		kind: "task_cancel",
		taskId: "task-1",
	});
	assert.deepEqual(matchA2AStandardRoute(`${A2A_STANDARD_REST_BASE}/tasks/task-1:subscribe`), {
		kind: "task_subscribe",
		taskId: "task-1",
	});
	assert.deepEqual(
		matchA2AStandardRoute(`${A2A_STANDARD_REST_BASE}/tasks/task-1/pushNotificationConfigs`),
		{
			kind: "push_config_list",
			taskId: "task-1",
		},
	);
	assert.deepEqual(
		matchA2AStandardRoute(`${A2A_STANDARD_REST_BASE}/tasks/task-1/pushNotificationConfigs/config-1`),
		{
			kind: "push_config_get",
			taskId: "task-1",
			configId: "config-1",
		},
	);
	assert.deepEqual(matchA2AStandardRoute(`${A2A_STANDARD_REST_BASE}/extendedAgentCard`), {
		kind: "extended_agent_card",
	});
	assert.deepEqual(matchA2AStandardRoute("/api/a2a/rpc"), { kind: "rpc" });
});

test("A2A route matcher rejects removed legacy runtime attach endpoints", () => {
	assert.equal(matchA2AStandardRoute("/api/a2a/chat"), null);
	assert.equal(matchA2AStandardRoute("/api/a2a/sessions/session-1/runtime"), null);
	assert.equal(matchA2AStandardRoute("/api/a2a/sessions/session-1/runtime/chat"), null);
	assert.equal(matchA2AStandardRoute("/api/a2a/v1/sessions/session-1/runtime"), null);
	assert.equal(matchA2AStandardRoute("/api/a2a/v1/runtime"), null);
});
