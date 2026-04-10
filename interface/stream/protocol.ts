export type StreamChunk =
	| { type: "start"; messageId: string; threadId?: string }
	| { type: "reasoning-start"; id: string }
	| { type: "reasoning-delta"; delta: string }
	| { type: "reasoning-end" }
	| { type: "text-start"; id: string }
	| { type: "text-delta"; textDelta: string }
	| { type: "text-end" }
	| { type: "tool-call-start"; id: string; toolCallId: string; toolName: string }
	| { type: "tool-call-delta"; argsText: string; toolCallId?: string }
	| { type: "tool-call-end"; toolCallId?: string }
	| { type: "tool-result"; toolCallId: string; result: unknown }
	| { type: "finish"; finishReason: string; usage?: unknown }
	| { type: "error"; error: string };

export type SessionStatus = {
	state: "started" | "stopped";
	code: number | null;
	signal: string | null;
};

export function serializeSse(id: number, chunk: StreamChunk): string {
	const lines = [
		`id: ${id}`,
		"event: message",
		`data: ${JSON.stringify(chunk)}`,
		"",
	];
	return `${lines.join("\n")}\n`;
}
