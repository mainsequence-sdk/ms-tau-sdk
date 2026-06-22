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
				agent_session_uid: string;
				session_key: string;
				runtime_session_uid: string;
				agent_type: string;
				thread_id: string;
				agent_uid: string;
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
			json_repair_attempts?: number | null;
			json_validation_error?: string | null;
			json_mode?: string | null;
		};
	};

export type StreamChunk = StreamEvent & {
	agent_uid: string | null;
};

export type SessionStatus = {
	state: "started" | "stopped";
	code: number | null;
	signal: string | null;
};

export function attachAgentUid(chunk: StreamEvent, agentUid: string | null): StreamChunk {
	return {
		...chunk,
		agent_uid: agentUid,
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
