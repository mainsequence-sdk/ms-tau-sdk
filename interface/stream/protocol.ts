export type StreamErrorSource =
	| "astro"
	| "backend"
	| "checkpoint"
	| "client"
	| "pi"
	| "provider"
	| "runtime"
	| "tool"
	| "unknown";

export type StreamEvent =
	| { type: "start"; messageId: string; threadId?: string }
	| {
			type: "new_session";
			new_session: {
				agent_session_id: number;
				session_key: string;
				runtime_session_id: string;
				runtime_agent_name: string;
				agent_unique_id?: string;
				thread_id: string;
				agent_id: number;
			};
	  }
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
	| {
		type: "error";
		error: string;
		error_source?: StreamErrorSource | null;
		status?: number | null;
		error_code?: string | null;
		error_detail?: string | null;
		field_errors?: unknown;
		forensics?: {
			backend_request_url?: string | null;
			backend_response_text?: string | null;
			backend_response_body?: unknown;
			backend_checkpoint_version?: unknown;
			backend_bundle_hash?: unknown;
		};
	};

export type StreamChunk = StreamEvent & {
	agent_id: number | null;
};

export type SessionStatus = {
	state: "started" | "stopped";
	code: number | null;
	signal: string | null;
};

export function attachAgentId(chunk: StreamEvent, agentId: number | null): StreamChunk {
	return {
		...chunk,
		agent_id: agentId,
	};
}

export function serializeSse(id: number, chunk: StreamChunk): string {
	const lines = [
		`id: ${id}`,
		"event: message",
		`data: ${JSON.stringify(chunk)}`,
		"",
	];
	return `${lines.join("\n")}\n`;
}
