export type ModelProviderAuthKind = "api_key" | "oauth";
export type ModelProviderSignInMode = "none" | "api_key_sync" | "oauth";
export type ModelProviderOAuthPrompt = {
	message: string;
	placeholder?: string;
	allowEmpty?: boolean;
};

export type AuthBackedProviderDefinition = {
	provider: string;
	authKind: ModelProviderAuthKind;
	signInMode: ModelProviderSignInMode;
	exposeInAstroUi: boolean;
	isConfiguredFromEnv(env: NodeJS.ProcessEnv): boolean;
	readEnvApiKey(env: NodeJS.ProcessEnv): string | null;
	resolveDefaultOAuthPromptInput?(prompt: ModelProviderOAuthPrompt): string | undefined;
};

const ENV_API_KEY_VARIABLES: Record<string, string[]> = {
	openai: ["OPENAI_API_KEY"],
	anthropic: ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
	google: ["GEMINI_API_KEY"],
	groq: ["GROQ_API_KEY"],
	cerebras: ["CEREBRAS_API_KEY"],
	xai: ["XAI_API_KEY"],
	openrouter: ["OPENROUTER_API_KEY"],
	"vercel-ai-gateway": ["AI_GATEWAY_API_KEY"],
	zai: ["ZAI_API_KEY"],
	mistral: ["MISTRAL_API_KEY"],
	minimax: ["MINIMAX_API_KEY"],
	"minimax-cn": ["MINIMAX_CN_API_KEY"],
	huggingface: ["HF_TOKEN"],
	opencode: ["OPENCODE_API_KEY"],
	"opencode-go": ["OPENCODE_API_KEY"],
	"kimi-coding": ["KIMI_API_KEY"],
	"azure-openai-responses": ["AZURE_OPENAI_API_KEY"],
};

function normalizeNonEmptyString(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function readGenericEnvApiKeyForProvider(provider: string, env: NodeJS.ProcessEnv): string | null {
	const variableNames = ENV_API_KEY_VARIABLES[provider] ?? [];
	for (const variableName of variableNames) {
		const resolved = normalizeNonEmptyString(env[variableName]);
		if (resolved) return resolved;
	}
	return null;
}

function hasGenericEnvApiKey(provider: string, env: NodeJS.ProcessEnv): boolean {
	return Boolean(readGenericEnvApiKeyForProvider(provider, env));
}

function readGenericEnvApiKey(provider: string, env: NodeJS.ProcessEnv): string | null {
	return readGenericEnvApiKeyForProvider(provider, env);
}

const AUTH_BACKED_PROVIDER_DEFINITIONS: AuthBackedProviderDefinition[] = [
	{
		provider: "openai",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: true,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("openai", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("openai", env),
	},
	{
		provider: "anthropic",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: true,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("anthropic", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("anthropic", env),
	},
	{
		provider: "google",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("google", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("google", env),
	},
	{
		provider: "groq",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("groq", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("groq", env),
	},
	{
		provider: "cerebras",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("cerebras", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("cerebras", env),
	},
	{
		provider: "xai",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("xai", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("xai", env),
	},
	{
		provider: "openrouter",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("openrouter", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("openrouter", env),
	},
	{
		provider: "vercel-ai-gateway",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("vercel-ai-gateway", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("vercel-ai-gateway", env),
	},
	{
		provider: "zai",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("zai", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("zai", env),
	},
	{
		provider: "mistral",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("mistral", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("mistral", env),
	},
	{
		provider: "minimax",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("minimax", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("minimax", env),
	},
	{
		provider: "minimax-cn",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("minimax-cn", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("minimax-cn", env),
	},
	{
		provider: "huggingface",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("huggingface", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("huggingface", env),
	},
	{
		provider: "opencode",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("opencode", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("opencode", env),
	},
	{
		provider: "opencode-go",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("opencode-go", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("opencode-go", env),
	},
	{
		provider: "kimi-coding",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) => hasGenericEnvApiKey("kimi-coding", env),
		readEnvApiKey: (env) => readGenericEnvApiKey("kimi-coding", env),
	},
	{
		provider: "azure-openai-responses",
		authKind: "api_key",
		signInMode: "api_key_sync",
		exposeInAstroUi: false,
		isConfiguredFromEnv: (env) =>
			Boolean(
				readGenericEnvApiKeyForProvider("azure-openai-responses", env) &&
					(normalizeNonEmptyString(env.AZURE_OPENAI_BASE_URL) ||
						normalizeNonEmptyString(env.AZURE_OPENAI_RESOURCE_NAME)),
			),
		readEnvApiKey: (env) => readGenericEnvApiKey("azure-openai-responses", env),
	},
	{
		provider: "github-copilot",
		authKind: "oauth",
		signInMode: "oauth",
		exposeInAstroUi: true,
		isConfiguredFromEnv: () => false,
		readEnvApiKey: () => null,
		resolveDefaultOAuthPromptInput: (prompt) =>
			prompt.allowEmpty && prompt.message === "GitHub Enterprise URL/domain (blank for github.com)"
				? ""
				: undefined,
	},
	{
		provider: "openai-codex",
		authKind: "oauth",
		signInMode: "oauth",
		exposeInAstroUi: true,
		isConfiguredFromEnv: () => false,
		readEnvApiKey: () => null,
	},
];

export function listAuthBackedProviderDefinitions(): AuthBackedProviderDefinition[] {
	return AUTH_BACKED_PROVIDER_DEFINITIONS;
}

export function resolveProviderDefinition(provider: string): AuthBackedProviderDefinition | null {
	return AUTH_BACKED_PROVIDER_DEFINITIONS.find((entry) => entry.provider === provider) ?? null;
}

export function shouldExposeAuthBackedProviderInAstroUi(provider: string): boolean {
	const definition = resolveProviderDefinition(provider);
	return definition?.exposeInAstroUi === true;
}
