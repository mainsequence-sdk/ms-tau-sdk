import assert from "node:assert/strict";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { once } from "node:events";
import { test } from "node:test";

import {
	type ActiveWarmRunnerTurn,
	releaseActiveWarmRunnerTurn,
	SameSessionTurnQueue,
	type WarmRunnerTurnHolder,
} from "../interface/stream/a2a-turn-lifecycle.js";
import { createHttpClientAbortSignal } from "../interface/stream/http-client-abort.js";

type HarnessContext = {
	messageId: string;
	sessionId: string;
	res: ServerResponse;
	finished: boolean;
};

type HarnessTurn = ActiveWarmRunnerTurn<HarnessContext> & {
	messageId: string;
};

type HarnessRunner = WarmRunnerTurnHolder<HarnessContext, HarnessTurn> & {
	id: string;
	abortCommands: string[];
	stopReasons: string[];
};

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readJsonBody(req: IncomingMessage): Promise<Record<string, any>> {
	const chunks: Buffer[] = [];
	for await (const chunk of req) {
		chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
	}
	const text = Buffer.concat(chunks).toString("utf8");
	return JSON.parse(text);
}

function waitForLog(logs: string[], predicate: (entry: string) => boolean, timeoutMs = 1000): Promise<void> {
	const startedAt = Date.now();
	return new Promise((resolve, reject) => {
		const poll = () => {
			if (logs.some(predicate)) {
				resolve();
				return;
			}
			if (Date.now() - startedAt > timeoutMs) {
				reject(new Error(`Timed out waiting for log. Logs: ${logs.join(", ")}`));
				return;
			}
			setTimeout(poll, 10);
		};
		poll();
	});
}

function writeA2AMessage(ctx: HarnessContext, text: string) {
	if (ctx.finished || ctx.res.destroyed || ctx.res.writableEnded) return;
	ctx.finished = true;
	ctx.res.writeHead(200, { "Content-Type": "application/a2a+json" });
	ctx.res.end(
		JSON.stringify({
			message: {
				messageId: `agent-${ctx.messageId}`,
				role: "ROLE_AGENT",
				contextId: ctx.sessionId,
				parts: [{ text }],
			},
		}),
	);
}

test("A2A same-session cancel/overlap stress releases queue before next request", async () => {
	const sessionId = "stress-session-uid";
	const queue = new SameSessionTurnQueue();
	const runner: HarnessRunner = {
		id: "runner-1",
		currentTurn: null,
		abortCommands: [],
		stopReasons: [],
	};
	const logs: string[] = [];
	const acceptedAt = new Map<string, number>();
	const completedAt = new Map<string, number>();
	const cancelledAt = new Map<string, number>();

	async function dispatchFakeTurn(ctx: HarnessContext): Promise<void> {
		logs.push(`dispatch:${ctx.messageId}:${runner.id}`);
		await new Promise<void>((resolve) => {
			runner.currentTurn = {
				ctx,
				messageId: ctx.messageId,
				completed: false,
				startedAt: Date.now(),
				resolve,
			};
			acceptedAt.set(ctx.messageId, Date.now());
			logs.push(`accepted:${ctx.messageId}`);

			if (ctx.messageId === "A") {
				// Request A intentionally never completes from the runner. The only way request B
				// can run is if HTTP cancellation releases this turn and same-session queue slot.
				return;
			}

			setTimeout(() => {
				const turn = runner.currentTurn;
				if (!turn || turn.ctx !== ctx || turn.completed) return;
				turn.completed = true;
				runner.currentTurn = null;
				writeA2AMessage(ctx, `answer ${ctx.messageId}`);
				completedAt.set(ctx.messageId, Date.now());
				logs.push(`completed:${ctx.messageId}`);
				resolve();
			}, 25);
		});
	}

	const server = createServer(async (req, res) => {
		if (req.method !== "POST" || req.url !== "/api/a2a/v1/message:send") {
			res.writeHead(404).end();
			return;
		}

		const clientAbort = createHttpClientAbortSignal(req, res);
		try {
			const body = await readJsonBody(req);
			const message = body.message as Record<string, any>;
			const contextId = String(message.contextId);
			const messageId = String(message.messageId);
			const ctx: HarnessContext = {
				messageId,
				sessionId: contextId,
				res,
				finished: false,
			};

			const abortListener = () => {
				runner.abortCommands.push(ctx.messageId);
				logs.push(`abort:${ctx.messageId}:${runner.id}`);
				setTimeout(() => {
					const released = releaseActiveWarmRunnerTurn(runner, ctx, {
						onRelease: (turn) => {
							cancelledAt.set(turn.messageId, Date.now());
							logs.push(`cancelled:${turn.messageId}`);
							ctx.finished = true;
						},
					});
					if (!released && !ctx.finished) {
						ctx.finished = true;
						logs.push(`cancelled-before-dispatch:${ctx.messageId}`);
					}
				}, 50);
			};
			clientAbort.signal.addEventListener("abort", abortListener, { once: true });

			await queue.enqueue(contextId, async () => {
				if (clientAbort.signal.aborted) return;
				await dispatchFakeTurn(ctx);
			});
			clientAbort.dispose();
		} catch (error) {
			if (!res.destroyed && !res.writableEnded) {
				res.writeHead(500, { "Content-Type": "application/json" });
				res.end(JSON.stringify({ error: error instanceof Error ? error.message : String(error) }));
			}
		}
	});

	server.listen(0, "127.0.0.1");
	await once(server, "listening");
	const address = server.address();
	assert.ok(address && typeof address === "object");
	const url = `http://127.0.0.1:${address.port}/api/a2a/v1/message:send`;

	async function postMessage(messageId: string, signal?: AbortSignal): Promise<Record<string, any>> {
		const response = await fetch(url, {
			method: "POST",
			headers: {
				"Content-Type": "application/a2a+json",
				Accept: "application/a2a+json",
			},
			body: JSON.stringify({
				message: {
					messageId,
					role: "ROLE_USER",
					contextId: sessionId,
					parts: [{ text: `request ${messageId}` }],
				},
			}),
			signal,
		});
		assert.equal(response.status, 200);
		return response.json();
	}

	try {
		const abortA = new AbortController();
		const requestA = postMessage("A", abortA.signal).then(
			() => "completed",
			(error) => (error instanceof Error ? error.name : String(error)),
		);
		await waitForLog(logs, (entry) => entry === "accepted:A");
		await sleep(500);
		abortA.abort();
		assert.equal(await requestA, "AbortError");

		const bStartedAt = Date.now();
		const responseBPromise = postMessage("B");
		await waitForLog(logs, (entry) => entry === "abort:A:runner-1");
		assert.equal(logs.includes("dispatch:B:runner-1"), false);
		await waitForLog(logs, (entry) => entry === "cancelled:A");
		const responseB = await responseBPromise;
		const bDurationMs = Date.now() - bStartedAt;
		const responseC = await postMessage("C");

		assert.equal(responseB.message.contextId, sessionId);
		assert.equal(responseB.message.parts[0].text, "answer B");
		assert.equal(responseC.message.contextId, sessionId);
		assert.equal(responseC.message.parts[0].text, "answer C");
		assert.ok(
			bDurationMs < 500,
			`request B waited ${bDurationMs}ms after request A was canceled; logs=${logs.join(",")}`,
		);

		assert.deepEqual(logs, [
			"dispatch:A:runner-1",
			"accepted:A",
			"abort:A:runner-1",
			"cancelled:A",
			"dispatch:B:runner-1",
			"accepted:B",
			"completed:B",
			"dispatch:C:runner-1",
			"accepted:C",
			"completed:C",
		]);
		assert.ok((acceptedAt.get("B") ?? 0) >= (cancelledAt.get("A") ?? Number.MAX_SAFE_INTEGER));
		assert.ok((acceptedAt.get("C") ?? 0) >= (completedAt.get("B") ?? Number.MAX_SAFE_INTEGER));
		assert.equal(queue.has(sessionId), false);
		assert.equal(runner.currentTurn, null);
		assert.deepEqual(runner.abortCommands, ["A"]);
		assert.deepEqual(runner.stopReasons, []);

		const publicPayload = JSON.stringify([responseB, responseC]);
		assert.doesNotMatch(publicPayload, /text-delta|tool-result|reasoning|warm_runner|runtime_turn/);
	} finally {
		await new Promise<void>((resolve, reject) => {
			server.close((error) => (error ? reject(error) : resolve()));
		});
	}
});

test("A2A same-session cancel before dispatch skips abandoned queued turn", async () => {
	const sessionId = "stress-session-uid";
	const queue = new SameSessionTurnQueue();
	const logs: string[] = [];
	let releaseBlocker: (() => void) | null = null;
	const blocker = queue.enqueue(
		sessionId,
		() =>
			new Promise<void>((resolve) => {
				releaseBlocker = resolve;
				logs.push("blocker:started");
			}),
	);
	await waitForLog(logs, (entry) => entry === "blocker:started");

	const abortA = new AbortController();
	const a = queue.enqueue(sessionId, async () => {
		if (abortA.signal.aborted) {
			logs.push("skipped:A");
			return;
		}
		logs.push("dispatch:A");
	});
	const b = queue.enqueue(sessionId, async () => {
		logs.push("dispatch:B");
	});

	await sleep(500);
	abortA.abort();
	logs.push("abort:A");
	releaseBlocker?.();
	await Promise.all([blocker, a, b]);

	assert.deepEqual(logs, ["blocker:started", "abort:A", "skipped:A", "dispatch:B"]);
	assert.equal(queue.has(sessionId), false);
});
