import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

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
		console.error("[astro-session-model] Ignoring invalid ASTRO_SESSION_MODEL_PROVIDER_REGISTRATION payload.");
		return;
	}

	try {
		pi.registerProvider(registration.provider, registration.config as any);
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		console.error(
			`[astro-session-model] Failed to register provider "${registration.provider}" from session binding: ${message}`,
		);
	}
}
