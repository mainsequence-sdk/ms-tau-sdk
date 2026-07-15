import type { StreamEvent } from "./protocol.js";

export type StreamErrorLogSeverity = "INFO" | "WARNING" | "ERROR";

export type StreamErrorLogClassification = {
	event: "pi.cancellation" | "pi.chunk";
	chunkType: "cancelled" | "error";
	severity: StreamErrorLogSeverity;
	cancellationReason?: string;
};

type StreamErrorChunk = Extract<StreamEvent, { type: "error" }>;

function normalizeString(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function classifyStreamErrorForLog(chunk: StreamErrorChunk): StreamErrorLogClassification {
	const errorCode = normalizeString(chunk.error_code);
	const errorSource = normalizeString(chunk.error_source);

	if (errorCode === "session_cancelled_by_user") {
		return {
			event: "pi.cancellation",
			chunkType: "cancelled",
			severity: "INFO",
			cancellationReason: "user_requested",
		};
	}

	if (errorCode === "client_disconnected") {
		return {
			event: "pi.cancellation",
			chunkType: "cancelled",
			severity: "INFO",
			cancellationReason: "client_disconnected",
		};
	}

	if (errorCode === "session_cancelled") {
		return {
			event: "pi.cancellation",
			chunkType: "cancelled",
			severity: "WARNING",
			cancellationReason: "runtime_cancelled",
		};
	}

	if (errorSource === "client" && chunk.status === 499) {
		return {
			event: "pi.cancellation",
			chunkType: "cancelled",
			severity: "INFO",
			cancellationReason: "client_disconnected",
		};
	}

	return {
		event: "pi.chunk",
		chunkType: "error",
		severity: "ERROR",
	};
}
