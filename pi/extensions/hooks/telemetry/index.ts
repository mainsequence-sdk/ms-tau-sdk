import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { emitTelemetryEvent } from "../../shared/telemetry.js";

export default function (pi: ExtensionAPI) {
	if (process.env.ASTRO_TELEMETRY !== "1") return;

	pi.on("session_start", async (_event, ctx) => {
		emitTelemetryEvent("session_start", { cwd: ctx.cwd });
	});

	pi.on("session_switch", async (_event, ctx) => {
		emitTelemetryEvent("session_switch", { cwd: ctx.cwd });
	});

	pi.on("session_fork", async (_event, ctx) => {
		emitTelemetryEvent("session_fork", { cwd: ctx.cwd });
	});

	pi.on("message_start", async (event) => {
		emitTelemetryEvent("message_start", { role: event.message?.role });
	});

	pi.on("message_update", async (event) => {
		emitTelemetryEvent("message_update", {
			role: event.message?.role,
			assistantMessageEvent: event.assistantMessageEvent,
		});
	});

	pi.on("message_end", async (event) => {
		emitTelemetryEvent("message_end", { role: event.message?.role });
	});

	pi.on("tool_execution_end", async (event) => {
		emitTelemetryEvent("tool_execution_end", {
			toolCallId: event.toolCallId,
			toolName: event.toolName,
			result: event.result,
			isError: event.isError,
		});
	});
}
