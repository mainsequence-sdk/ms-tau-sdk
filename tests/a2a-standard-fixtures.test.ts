import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const FIXTURE_ROOT = path.resolve("tests/fixtures/a2a");

async function readFixture(name: string): Promise<Record<string, any>> {
	const text = await readFile(path.join(FIXTURE_ROOT, name), "utf8");
	const parsed = JSON.parse(text);
	assert.equal(typeof parsed, "object");
	assert.notEqual(parsed, null);
	return parsed;
}

function sourceSection(source: string, start: string, end: string): string {
	const startIndex = source.indexOf(start);
	const endIndex = source.indexOf(end, startIndex + start.length);
	assert.notEqual(startIndex, -1, `Missing source marker: ${start}`);
	assert.notEqual(endIndex, -1, `Missing source marker: ${end}`);
	return source.slice(startIndex, endIndex);
}

function assertA2AMessage(message: Record<string, any>) {
	assert.equal(typeof message.messageId, "string");
	assert.ok(message.messageId.length > 0);
	assert.match(message.role, /^ROLE_(USER|AGENT)$/);
	assert.equal(typeof message.contextId, "string");
	assert.ok(message.contextId.length > 0);
	assert.ok(Array.isArray(message.parts));
	assert.ok(message.parts.length > 0);
}

function assertA2ATask(task: Record<string, any>) {
	assert.equal(typeof task.id, "string");
	assert.equal(typeof task.contextId, "string");
	assert.equal(typeof task.status, "object");
	assert.match(task.status.state, /^TASK_STATE_/);
	if (task.artifacts !== undefined) assert.ok(Array.isArray(task.artifacts));
}

test("A2A REST message:send fixture uses Message request and message response", async () => {
	const request = await readFixture("message-send-request.json");
	const response = await readFixture("message-send-response.json");

	assertA2AMessage(request.message);
	assert.deepEqual(Object.keys(response), ["message"]);
	assertA2AMessage(response.message);
	assert.equal(response.message.role, "ROLE_AGENT");
});

test("A2A task fixture uses Task response with artifact output", async () => {
	const response = await readFixture("task-response.json");

	assert.deepEqual(Object.keys(response), ["task"]);
	assertA2ATask(response.task);
	assert.ok(response.task.artifacts.length > 0);
});

test("A2A JSON-RPC fixture wraps SendMessage result in JSON-RPC 2.0 envelope", async () => {
	const request = await readFixture("json-rpc-send-message-request.json");
	const response = await readFixture("json-rpc-send-message-response.json");

	assert.equal(request.jsonrpc, "2.0");
	assert.equal(request.method, "SendMessage");
	assertA2AMessage(request.params.message);
	assert.equal(response.jsonrpc, "2.0");
	assert.equal(response.id, request.id);
	assert.deepEqual(Object.keys(response.result), ["message"]);
	assertA2AMessage(response.result.message);
});

test("public A2A exposes message send without runtime attach or user identity coupling", async () => {
	const source = await readFile("interface/stream/server.ts", "utf8");
	const payloadBuilder = sourceSection(
		source,
		"function buildA2AStandardRuntimeChatPayload",
		"async function resolveA2AStandardRuntimeIdentity",
	);
	const identityResolver = sourceSection(
		source,
		"async function resolveA2AStandardRuntimeIdentity",
		"async function runA2AStandardRuntimeTurn",
	);
	const messageSendExecutor = sourceSection(
		source,
		"async function executeA2AStandardMessageSend",
		"function writeA2AStreamHeaders",
	);
	const runtimeTurn = sourceSection(
		source,
		"async function runA2AStandardRuntimeTurn",
		"function createCapturingRuntimeResponse",
	);
	const standardRequestHandler = sourceSection(
		source,
		"async function handleA2AStandardRequest",
		"async function handleA2AStandardJsonRpcRequest",
	);
	const jsonRpcHandler = sourceSection(
		source,
		"async function handleA2AStandardJsonRpcRequest",
		"async function handleStreamRequest",
	);

	assert.doesNotMatch(payloadBuilder, /\buser_uid\b|userUid/);
	assert.doesNotMatch(identityResolver, /resolveUserIdFromRequest|userUid/);
	assert.doesNotMatch(
		source,
		new RegExp(["handleA2A", "SessionRuntimeRequest|matchA2A", "SessionRuntimeRoute"].join("")),
	);
	assert.doesNotMatch(source, /\/api\/a2a\/sessions\/\{agent_session_uid\}\/runtime/);
	assert.match(messageSendExecutor, /buildA2AStandardMessageSendKey\(prepared\)/);
	assert.match(messageSendExecutor, /a2aStandardMessageSends\.get\(key\)/);
	assert.match(messageSendExecutor, /replayA2AStandardMessageSend\(existing\)/);
	assert.match(messageSendExecutor, /buildA2AStandardMessageSendConflictResponse\(prepared\)/);
	assert.match(messageSendExecutor, /runA2AStandardRuntimeTurn\(prepared, options\)/);
	assert.match(source, /clientAbortSignal\?: AbortSignal/);
	assert.match(runtimeTurn, /ctx\.cancelOnClientDisconnect = true/);
	assert.doesNotMatch(runtimeTurn, /ctx\.cancelOnClientDisconnect = false/);
	assert.match(runtimeTurn, /attachA2AStandardRuntimeClientAbort/);
	assert.match(standardRequestHandler, /createHttpClientAbortSignal\(req, res\)/);
	assert.match(standardRequestHandler, /canWriteHttpResponse\(res, clientAbort\.signal\)/);
	assert.match(jsonRpcHandler, /createHttpClientAbortSignal\(req, res\)/);
	assert.match(jsonRpcHandler, /canWriteHttpResponse\(res, clientAbort\.signal\)/);
});
