import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { logStructuredEvent } from "../../shared/structured-logging.js";

type ProviderRegistration = {
	provider: string;
	config: Record<string, unknown>;
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseProviderRegistration(value: string): ProviderRegistration | null {
	try {
		const parsed = JSON.parse(value) as unknown;
		if (!isPlainObject(parsed)) return null;
		if (typeof parsed.provider !== "string" || !parsed.provider.trim()) return null;
		if (!isPlainObject(parsed.config)) return null;
		return {
			provider: parsed.provider.trim(),
			config: parsed.config,
		};
	} catch {
		return null;
	}
}

export default function (pi: ExtensionAPI) {
	const rawRegistration = process.env.ASTRO_SESSION_MODEL_PROVIDER_REGISTRATION?.trim();
	if (!rawRegistration) return;

	const registration = parseProviderRegistration(rawRegistration);
	if (!registration) {
		logStructuredEvent({
			severity: "WARNING",
			component: "astro-session-model",
			event: "session_model.registration_invalid",
			message: "Ignoring invalid ASTRO_SESSION_MODEL_PROVIDER_REGISTRATION payload.",
		});
		return;
	}

	try {
		pi.registerProvider(registration.provider, registration.config as any);
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		logStructuredEvent({
			severity: "ERROR",
			component: "astro-session-model",
			event: "session_model.provider_registration_failed",
			message: "Failed to register provider from session binding.",
			data: {
				provider: registration.provider,
				error: message,
				errorType: error instanceof Error ? error.name : null,
			},
		});
	}
}
