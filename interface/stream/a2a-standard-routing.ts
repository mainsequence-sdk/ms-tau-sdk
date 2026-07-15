export type A2AStandardRoute =
	| { kind: "message_send" }
	| { kind: "message_stream" }
	| { kind: "tasks" }
	| { kind: "task_get"; taskId: string }
	| { kind: "task_cancel"; taskId: string }
	| { kind: "task_subscribe"; taskId: string }
	| { kind: "push_config_list"; taskId: string }
	| { kind: "push_config_get"; taskId: string; configId: string }
	| { kind: "extended_agent_card" }
	| { kind: "rpc" };

export const A2A_STANDARD_REST_BASE = "/api/a2a/v1";
export const A2A_STANDARD_RPC_PATH = "/api/a2a/rpc";

export function matchA2AStandardRoute(pathname: string): A2AStandardRoute | null {
	if (pathname === `${A2A_STANDARD_REST_BASE}/message:send`) return { kind: "message_send" };
	if (pathname === `${A2A_STANDARD_REST_BASE}/message:stream`) return { kind: "message_stream" };
	if (pathname === `${A2A_STANDARD_REST_BASE}/tasks`) return { kind: "tasks" };
	if (pathname === `${A2A_STANDARD_REST_BASE}/extendedAgentCard`) {
		return { kind: "extended_agent_card" };
	}
	if (pathname === A2A_STANDARD_RPC_PATH) return { kind: "rpc" };

	const taskCancelMatch = pathname.match(/^\/api\/a2a\/v1\/tasks\/([^/]+):cancel$/);
	if (taskCancelMatch) return { kind: "task_cancel", taskId: decodeURIComponent(taskCancelMatch[1]) };

	const taskSubscribeMatch = pathname.match(/^\/api\/a2a\/v1\/tasks\/([^/]+):subscribe$/);
	if (taskSubscribeMatch) {
		return { kind: "task_subscribe", taskId: decodeURIComponent(taskSubscribeMatch[1]) };
	}

	const pushConfigMatch = pathname.match(
		/^\/api\/a2a\/v1\/tasks\/([^/]+)\/pushNotificationConfigs(?:\/([^/]+))?$/,
	);
	if (pushConfigMatch) {
		const taskId = decodeURIComponent(pushConfigMatch[1]);
		const configId = pushConfigMatch[2] ? decodeURIComponent(pushConfigMatch[2]) : null;
		if (configId) {
			return { kind: "push_config_get", taskId, configId };
		}
		return { kind: "push_config_list", taskId };
	}

	const taskGetMatch = pathname.match(/^\/api\/a2a\/v1\/tasks\/([^/]+)$/);
	if (taskGetMatch) return { kind: "task_get", taskId: decodeURIComponent(taskGetMatch[1]) };

	return null;
}
