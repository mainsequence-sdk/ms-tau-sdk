import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";

import {
	canWriteHttpResponse,
	createHttpClientAbortSignal,
} from "../interface/stream/http-client-abort.js";

type FakeRequest = EventEmitter & {
	once: EventEmitter["once"];
	off: EventEmitter["off"];
};

type FakeResponse = EventEmitter & {
	destroyed: boolean;
	writableEnded: boolean;
	once: EventEmitter["once"];
	off: EventEmitter["off"];
};

function fakePair(): { req: FakeRequest; res: FakeResponse } {
	const req = new EventEmitter() as FakeRequest;
	const res = new EventEmitter() as FakeResponse;
	res.destroyed = false;
	res.writableEnded = false;
	return { req, res };
}

test("HTTP client abort signal fires when request is aborted", () => {
	const { req, res } = fakePair();
	const abort = createHttpClientAbortSignal(req as any, res as any);

	assert.equal(abort.signal.aborted, false);
	req.emit("aborted");

	assert.equal(abort.signal.aborted, true);
	abort.dispose();
});

test("HTTP client abort signal fires when response closes before ending", () => {
	const { req, res } = fakePair();
	const abort = createHttpClientAbortSignal(req as any, res as any);

	res.emit("close");

	assert.equal(abort.signal.aborted, true);
	abort.dispose();
});

test("HTTP client abort signal ignores normal response close after end", () => {
	const { req, res } = fakePair();
	const abort = createHttpClientAbortSignal(req as any, res as any);

	res.writableEnded = true;
	res.emit("close");

	assert.equal(abort.signal.aborted, false);
	abort.dispose();
});

test("HTTP client abort signal dispose removes listeners", () => {
	const { req, res } = fakePair();
	const abort = createHttpClientAbortSignal(req as any, res as any);

	abort.dispose();
	req.emit("aborted");
	res.emit("close");

	assert.equal(abort.signal.aborted, false);
});

test("canWriteHttpResponse respects response and abort state", () => {
	const { res } = fakePair();
	const controller = new AbortController();

	assert.equal(canWriteHttpResponse(res as any, controller.signal), true);
	res.writableEnded = true;
	assert.equal(canWriteHttpResponse(res as any, controller.signal), false);
	res.writableEnded = false;
	res.destroyed = true;
	assert.equal(canWriteHttpResponse(res as any, controller.signal), false);
	res.destroyed = false;
	controller.abort();
	assert.equal(canWriteHttpResponse(res as any, controller.signal), false);
});
