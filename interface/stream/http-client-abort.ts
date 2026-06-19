import type { IncomingMessage, ServerResponse } from "node:http";

export function createHttpClientAbortSignal(
	req: IncomingMessage,
	res: ServerResponse,
): { signal: AbortSignal; dispose: () => void } {
	const controller = new AbortController();
	const abort = () => {
		if (!controller.signal.aborted) {
			controller.abort(new Error("client_disconnected"));
		}
	};
	const onResponseClose = () => {
		if (!res.writableEnded) abort();
	};
	req.once("aborted", abort);
	res.once("close", onResponseClose);
	return {
		signal: controller.signal,
		dispose: () => {
			req.off("aborted", abort);
			res.off("close", onResponseClose);
		},
	};
}

export function canWriteHttpResponse(
	res: Pick<ServerResponse, "destroyed" | "writableEnded">,
	signal?: AbortSignal,
): boolean {
	return !res.destroyed && !res.writableEnded && signal?.aborted !== true;
}
