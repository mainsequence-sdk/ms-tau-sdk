export type StreamEvent =
	| { type: "start"; messageId: string; threadId?: string }
	| {
			type: "new_session";
			new_session: {
				agent_session_id: number;
				session_key: string;
				agent_unique_id: string;
				thread_id: string;
				agent_id: number;
			};
	  }
	| {
			type: "session_switch";
			session_switch: {
				from_agent_name: string;
				to_agent_name: string;
				project_id: string;
				cwd: string;
				thread_id: string;
				agent_id: number;
				agent_unique_id: string;
				agent_session_id: number;
				session_key: string;
				runtime_session_id: string;
				initial_task: string | null;
				summary: string | null;
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
	| { type: "error"; error: string };

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
