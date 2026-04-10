export const TELEMETRY_PREFIX = process.env.ASTRO_TELEMETRY_PREFIX ?? "__ASTRO_EVENT__:";

export type TelemetryPayload = {
	name: string;
	time: string;
	role: "parent" | "child";
	specialist?: string;
	data?: Record<string, unknown>;
};

export function emitTelemetryEvent(name: string, data?: Record<string, unknown>) {
	if (process.env.ASTRO_TELEMETRY !== "1") return;

	const payload: TelemetryPayload = {
		name,
		time: new Date().toISOString(),
		role: process.env.ASTRO_SUBAGENT_CHILD === "1" ? "child" : "parent",
		specialist: process.env.ASTRO_ACTIVE_SPECIALIST || undefined,
		data,
	};

	try {
		process.stdout.write(`${TELEMETRY_PREFIX}${JSON.stringify(payload)}\n`);
	} catch {
		// Avoid breaking the main process if telemetry serialization fails.
	}
}
