import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { AuthStorage } from "../../node_modules/@mariozechner/pi-coding-agent/dist/index.js";
import { resolveProviderDefinition } from "./model-provider-definitions.js";
import {
	resolvePiAgentDir,
	setProviderSignedOff,
	withScopedPiAgentDir,
	withScopedPiAgentDirAsync,
} from "./model-provider-runtime.js";

export type ModelProviderSignInAttemptStatus =
	| "pending"
	| "awaiting_browser"
	| "awaiting_manual_input"
	| "running"
	| "completed"
	| "failed"
	| "cancelled";

export type ModelProviderSignInNextAction =
	| { type: "none" }
	| { type: "wait"; message: string }
	| { type: "open_url"; url: string; instructions?: string }
	| { type: "prompt_input"; prompt: string; instructions?: string; placeholder?: string; allowEmpty?: boolean }
	| { type: "enter_callback_url"; prompt: string; instructions?: string };

export type ModelProviderSignInProgressEntry = {
	message: string;
	at: string;
};

export type ModelProviderSignInAttempt = {
	id: string;
	provider: string;
	status: ModelProviderSignInAttemptStatus;
	nextAction: ModelProviderSignInNextAction;
	authUrl: string | null;
	authInstructions: string | null;
	progress: ModelProviderSignInProgressEntry[];
	authKind: "api_key" | "oauth";
	createdAt: string;
	updatedAt: string;
	completedAt: string | null;
	error: string | null;
};

type ModelProviderSignInState = {
	attempts?: Record<string, ModelProviderSignInAttempt>;
};

type ActiveSignInAttempt = {
	attemptId: string;
	provider: string;
	abortController: AbortController;
	manualInputRequested: boolean;
	pendingManualInput: string | null;
	resolveManualInput: ((value: string) => void) | null;
	rejectManualInput: ((error: Error) => void) | null;
	cancelled: boolean;
};

type StartInteractiveSignInSuccess = {
	ok: true;
	statusCode: 202;
	provider: string;
	attempt: ModelProviderSignInAttempt;
};

type StartImmediateSignInSuccess = {
	ok: true;
	statusCode: 200;
	provider: string;
	authenticated: true;
	updatedAt: string;
};

type SignInFailure = {
	ok: false;
	statusCode: 400 | 404 | 409 | 500;
	error: string;
	message: string;
	attempt?: ModelProviderSignInAttempt;
};

type AttemptLookupSuccess = {
	ok: true;
	attempt: ModelProviderSignInAttempt;
};

type AttemptLookupFailure = {
	ok: false;
	statusCode: 404;
	error: string;
	message: string;
};

type ManualSubmitFailure = {
	ok: false;
	statusCode: 400 | 404 | 409;
	error: string;
	message: string;
	attempt?: ModelProviderSignInAttempt;
};

type ManualSubmitSuccess = {
	ok: true;
	statusCode: 202;
	attempt: ModelProviderSignInAttempt;
};

type CancelAttemptSuccess = {
	ok: true;
	statusCode: 200;
	attempt: ModelProviderSignInAttempt;
};

type CancelAttemptFailure = {
	ok: false;
	statusCode: 404 | 409;
	error: string;
	message: string;
	attempt?: ModelProviderSignInAttempt;
};

const SIGNIN_STATE_FILE_NAME = "astro-model-provider-signin.json";
const activeAttempts = new Map<string, ActiveSignInAttempt>();

function toErrorMessage(error: unknown): string {
	if (error instanceof Error && error.message.trim()) return error.message.trim();
	return String(error);
}

function isTerminalAttemptStatus(status: ModelProviderSignInAttemptStatus): boolean {
	return status === "completed" || status === "failed" || status === "cancelled";
}

function resolveSignInStatePath(env: NodeJS.ProcessEnv = process.env): string {
	return path.join(resolvePiAgentDir(env), SIGNIN_STATE_FILE_NAME);
}

function readSignInState(env: NodeJS.ProcessEnv = process.env): ModelProviderSignInState {
	const statePath = resolveSignInStatePath(env);
	if (!existsSync(statePath)) return {};
	try {
		const parsed = JSON.parse(readFileSync(statePath, "utf8")) as ModelProviderSignInState;
		return parsed && typeof parsed === "object" ? parsed : {};
	} catch {
		return {};
	}
}

function writeSignInState(nextState: ModelProviderSignInState, env: NodeJS.ProcessEnv = process.env) {
	const statePath = resolveSignInStatePath(env);
	mkdirSync(path.dirname(statePath), { recursive: true, mode: 0o700 });
	writeFileSync(statePath, `${JSON.stringify(nextState, null, 2)}\n`, { mode: 0o600 });
}

function persistAttempt(attempt: ModelProviderSignInAttempt, env: NodeJS.ProcessEnv = process.env) {
	const state = readSignInState(env);
	writeSignInState(
		{
			attempts: {
				...(state.attempts ?? {}),
				[attempt.id]: attempt,
			},
		},
		env,
	);
}

function normalizeAttempt(attempt: ModelProviderSignInAttempt): ModelProviderSignInAttempt {
	return {
		...attempt,
		authUrl: typeof attempt.authUrl === "string" && attempt.authUrl.trim() ? attempt.authUrl : null,
		authInstructions:
			typeof attempt.authInstructions === "string" && attempt.authInstructions.trim()
				? attempt.authInstructions
				: null,
	};
}

function readPersistedAttempt(attemptId: string, env: NodeJS.ProcessEnv = process.env): ModelProviderSignInAttempt | null {
	const state = readSignInState(env);
	const attempt = state.attempts?.[attemptId] ?? null;
	return attempt ? normalizeAttempt(attempt) : null;
}

function materializeAttempt(attemptId: string, env: NodeJS.ProcessEnv = process.env): ModelProviderSignInAttempt | null {
	const attempt = readPersistedAttempt(attemptId, env);
	if (!attempt) return null;
	if (!isTerminalAttemptStatus(attempt.status) && !activeAttempts.has(attempt.id)) {
		const interruptedAttempt: ModelProviderSignInAttempt = {
			...attempt,
			status: "failed",
			nextAction: { type: "none" },
			error: attempt.error ?? "The signin attempt was interrupted before completion.",
			updatedAt: new Date().toISOString(),
			completedAt: attempt.completedAt ?? new Date().toISOString(),
		};
		persistAttempt(interruptedAttempt, env);
		return interruptedAttempt;
	}
	return attempt;
}

function buildAttempt(options: {
	provider: string;
	authKind: "api_key" | "oauth";
	status: ModelProviderSignInAttemptStatus;
	nextAction: ModelProviderSignInNextAction;
}): ModelProviderSignInAttempt {
	const now = new Date().toISOString();
	return {
		id: randomUUID(),
		provider: options.provider,
		status: options.status,
		nextAction: options.nextAction,
		authUrl: null,
		authInstructions: null,
		progress: [],
		authKind: options.authKind,
		createdAt: now,
		updatedAt: now,
		completedAt: null,
		error: null,
	};
}

function updateAttempt(
	attemptId: string,
	env: NodeJS.ProcessEnv,
	mutate: (current: ModelProviderSignInAttempt) => ModelProviderSignInAttempt,
): ModelProviderSignInAttempt | null {
	const current = readPersistedAttempt(attemptId, env);
	if (!current) return null;
	const next = mutate(current);
	persistAttempt(next, env);
	return next;
}

function appendAttemptProgress(
	attemptId: string,
	env: NodeJS.ProcessEnv,
	message: string,
	status?: ModelProviderSignInAttemptStatus,
	nextAction?: ModelProviderSignInNextAction,
): ModelProviderSignInAttempt | null {
	return updateAttempt(attemptId, env, (current) => ({
		...current,
		status: status ?? current.status,
		nextAction: nextAction ?? current.nextAction,
		progress: [
			...current.progress,
			{
				message,
				at: new Date().toISOString(),
			},
		],
		updatedAt: new Date().toISOString(),
	}));
}

function setAttemptStatus(
	attemptId: string,
	env: NodeJS.ProcessEnv,
	status: ModelProviderSignInAttemptStatus,
	options?: {
		nextAction?: ModelProviderSignInNextAction;
		authUrl?: string | null;
		authInstructions?: string | null;
		error?: string | null;
		appendProgress?: string;
		markCompleted?: boolean;
	},
): ModelProviderSignInAttempt | null {
	return updateAttempt(attemptId, env, (current) => ({
		...current,
		status,
		nextAction: options?.nextAction ?? current.nextAction,
		authUrl: options?.authUrl === undefined ? current.authUrl : options.authUrl,
		authInstructions:
			options?.authInstructions === undefined ? current.authInstructions : options.authInstructions,
		error: options?.error === undefined ? current.error : options.error,
		progress: options?.appendProgress
			? [
					...current.progress,
					{
						message: options.appendProgress,
						at: new Date().toISOString(),
					},
				]
			: current.progress,
		updatedAt: new Date().toISOString(),
		completedAt: options?.markCompleted ? new Date().toISOString() : current.completedAt,
	}));
}

function findActiveAttemptByProvider(provider: string): ActiveSignInAttempt | null {
	for (const activeAttempt of activeAttempts.values()) {
		if (activeAttempt.provider === provider) return activeAttempt;
	}
	return null;
}

function createAwaitPromptInputHandler(
	activeAttempt: ActiveSignInAttempt,
	options: {
		type: "prompt_input" | "enter_callback_url";
		prompt: string;
		instructions?: string;
		placeholder?: string;
		allowEmpty?: boolean;
	},
	env: NodeJS.ProcessEnv,
): Promise<string> {
	activeAttempt.manualInputRequested = true;
	setAttemptStatus(activeAttempt.attemptId, env, "awaiting_manual_input", {
		nextAction:
			options.type === "enter_callback_url"
				? {
						type: "enter_callback_url",
						prompt: options.prompt,
						instructions:
							options.instructions ??
							"After you finish sign-in in the browser, copy the full redirect URL from the browser address bar and paste it here.",
					}
				: {
						type: "prompt_input",
						prompt: options.prompt,
						...(options.instructions ? { instructions: options.instructions } : {}),
						...(options.placeholder ? { placeholder: options.placeholder } : {}),
						...(options.allowEmpty ? { allowEmpty: true } : {}),
					},
		appendProgress:
			options.type === "enter_callback_url"
				? "Waiting for manual callback input."
				: "Waiting for provider signin input.",
	});

	return new Promise<string>((resolve, reject) => {
		activeAttempt.resolveManualInput = resolve;
		activeAttempt.rejectManualInput = reject;
		if (activeAttempt.pendingManualInput) {
			const pendingInput = activeAttempt.pendingManualInput;
			activeAttempt.pendingManualInput = null;
			resolve(pendingInput);
		}
	});
}

async function runInteractiveSignIn(
	attempt: ModelProviderSignInAttempt,
	activeAttempt: ActiveSignInAttempt,
	env: NodeJS.ProcessEnv,
) {
	try {
		const definition = resolveProviderDefinition(attempt.provider);
		await withScopedPiAgentDirAsync(env, async () => {
			const authStorage = AuthStorage.create();
			await authStorage.login(attempt.provider, {
				onAuth: (info) => {
					setAttemptStatus(attempt.id, env, "awaiting_browser", {
						nextAction: {
							type: "open_url",
							url: info.url,
							...(info.instructions ? { instructions: info.instructions } : {}),
						},
						authUrl: info.url,
						authInstructions: info.instructions ?? null,
						appendProgress: "Opened provider authorization flow.",
					});
				},
				onPrompt: async (prompt) => {
					const defaultPromptInput = definition?.resolveDefaultOAuthPromptInput?.(prompt);
					if (defaultPromptInput !== undefined) {
						appendAttemptProgress(
							attempt.id,
							env,
							"Using default provider signin option.",
							"running",
							{
								type: "wait",
								message: "Preparing provider authorization flow.",
							},
						);
						return defaultPromptInput;
					}

					return createAwaitPromptInputHandler(
						activeAttempt,
						{
							type: "prompt_input",
							prompt: prompt.message,
							...(prompt.placeholder ? { placeholder: prompt.placeholder } : {}),
							...(prompt.allowEmpty ? { allowEmpty: true } : {}),
						},
						env,
					);
				},
				onProgress: (message) => {
					appendAttemptProgress(attempt.id, env, message, "running", {
						type: "wait",
						message: "Waiting for the provider to finish authentication.",
					});
				},
				onManualCodeInput: async () =>
					createAwaitPromptInputHandler(
						activeAttempt,
						{
							type: "enter_callback_url",
							prompt: "Paste the authorization code or the full redirect URL.",
						},
						env,
					),
				signal: activeAttempt.abortController.signal,
			});
		});

		if (activeAttempt.cancelled) {
			setAttemptStatus(attempt.id, env, "cancelled", {
				nextAction: { type: "none" },
				error: "Signin attempt cancelled.",
				appendProgress: "Signin attempt cancelled.",
				markCompleted: true,
			});
			return;
		}

		setProviderSignedOff(attempt.provider, false, env);
		setAttemptStatus(attempt.id, env, "completed", {
			nextAction: { type: "none" },
			error: null,
			appendProgress: "Provider signin completed.",
			markCompleted: true,
		});
	} catch (error) {
		if (activeAttempt.cancelled || activeAttempt.abortController.signal.aborted) {
			setAttemptStatus(attempt.id, env, "cancelled", {
				nextAction: { type: "none" },
				error: "Signin attempt cancelled.",
				appendProgress: "Signin attempt cancelled.",
				markCompleted: true,
			});
			return;
		}

		setAttemptStatus(attempt.id, env, "failed", {
			nextAction: { type: "none" },
			error: toErrorMessage(error),
			appendProgress: "Provider signin failed.",
			markCompleted: true,
		});
	} finally {
		activeAttempts.delete(attempt.id);
	}
}

export function startModelProviderSignIn(
	provider: string,
	env: NodeJS.ProcessEnv = process.env,
): StartImmediateSignInSuccess | StartInteractiveSignInSuccess | SignInFailure {
	const definition = resolveProviderDefinition(provider);
	if (!definition) {
		return {
			ok: false,
			statusCode: 404,
			error: "provider_not_supported",
			message: `Provider "${provider}" is not supported by model-provider auth controls.`,
		};
	}

	const existingActiveAttempt = findActiveAttemptByProvider(provider);
	if (existingActiveAttempt) {
		return {
			ok: false,
			statusCode: 409,
			error: "provider_signin_in_progress",
			message: `A signin attempt for ${provider} is already in progress.`,
			attempt: materializeAttempt(existingActiveAttempt.attemptId, env) ?? undefined,
		};
	}

	const alreadyAuthenticated = withScopedPiAgentDir(env, () => {
		const authStorage = AuthStorage.create();
		return authStorage.has(provider);
	});
	if (alreadyAuthenticated) {
		setProviderSignedOff(provider, false, env);
		return {
			ok: true,
			statusCode: 200,
			provider,
			authenticated: true,
			updatedAt: new Date().toISOString(),
		};
	}

	if (definition.signInMode === "api_key_sync") {
		if (!definition.isConfiguredFromEnv(env)) {
			return {
				ok: false,
				statusCode: 409,
				error: "provider_env_not_configured",
				message: `${provider} credentials are not available in the runtime environment.`,
			};
		}

		const credential = definition.readEnvApiKey(env);
		if (!credential || credential === "<authenticated>") {
			return {
				ok: false,
				statusCode: 409,
				error: "provider_env_not_syncable",
				message: `${provider} does not expose a syncable API key in the runtime environment.`,
			};
		}

		withScopedPiAgentDir(env, () => {
			const authStorage = AuthStorage.create();
			authStorage.set(provider, {
				type: "api_key",
				key: credential,
			});
		});
		setProviderSignedOff(provider, false, env);

		return {
			ok: true,
			statusCode: 200,
			provider,
			authenticated: true,
			updatedAt: new Date().toISOString(),
		};
	}

	if (definition.signInMode !== "oauth") {
		return {
			ok: false,
			statusCode: 409,
			error: "provider_signin_not_available",
			message: `${provider} cannot be signed in through Astro's current signin flow.`,
		};
	}

	const attempt = buildAttempt({
		provider,
		authKind: definition.authKind,
		status: "pending",
		nextAction: {
			type: "wait",
			message: "Starting provider signin flow.",
		},
	});
	persistAttempt(attempt, env);

	const activeAttempt: ActiveSignInAttempt = {
		attemptId: attempt.id,
		provider,
		abortController: new AbortController(),
		manualInputRequested: false,
		pendingManualInput: null,
		resolveManualInput: null,
		rejectManualInput: null,
		cancelled: false,
	};
	activeAttempts.set(attempt.id, activeAttempt);

	void runInteractiveSignIn(attempt, activeAttempt, env);

	return {
		ok: true,
		statusCode: 202,
		provider,
		attempt: materializeAttempt(attempt.id, env) ?? attempt,
	};
}

export function getModelProviderSignInAttempt(
	provider: string,
	attemptId: string,
	env: NodeJS.ProcessEnv = process.env,
): AttemptLookupSuccess | AttemptLookupFailure {
	const attempt = materializeAttempt(attemptId, env);
	if (!attempt || attempt.provider !== provider) {
		return {
			ok: false,
			statusCode: 404,
			error: "signin_attempt_not_found",
			message: `No signin attempt was found for provider "${provider}" and id "${attemptId}".`,
		};
	}
	return {
		ok: true,
		attempt,
	};
}

export function submitModelProviderSignInManualInput(
	provider: string,
	attemptId: string,
	input: string,
	env: NodeJS.ProcessEnv = process.env,
): ManualSubmitSuccess | ManualSubmitFailure {
	const attempt = materializeAttempt(attemptId, env);
	if (!attempt || attempt.provider !== provider) {
		return {
			ok: false,
			statusCode: 404,
			error: "signin_attempt_not_found",
			message: `No signin attempt was found for provider "${provider}" and id "${attemptId}".`,
		};
	}

	const nextAction = attempt.nextAction;
	const trimmedInput = input.trim();
	const allowEmpty =
		nextAction.type === "prompt_input" ? nextAction.allowEmpty === true : false;
	if (!trimmedInput && !allowEmpty) {
		return {
			ok: false,
			statusCode: 400,
			error: "signin_manual_input_missing",
			message:
				nextAction.type === "enter_callback_url"
					? "Expected a pasted callback URL or authorization code."
					: "Expected input for the current provider signin prompt.",
			attempt,
		};
	}

	if (isTerminalAttemptStatus(attempt.status)) {
		return {
			ok: false,
			statusCode: 409,
			error: "signin_attempt_not_active",
			message: `Signin attempt "${attemptId}" is already ${attempt.status}.`,
			attempt,
		};
	}

	const activeAttempt = activeAttempts.get(attemptId);
	if (!activeAttempt || activeAttempt.provider !== provider) {
		return {
			ok: false,
			statusCode: 409,
			error: "signin_attempt_not_active",
			message: `Signin attempt "${attemptId}" is no longer active.`,
			attempt,
		};
	}

	activeAttempt.pendingManualInput = allowEmpty ? input : trimmedInput;
	if (activeAttempt.resolveManualInput) {
		const resolveManualInput = activeAttempt.resolveManualInput;
		activeAttempt.resolveManualInput = null;
		activeAttempt.rejectManualInput = null;
		activeAttempt.pendingManualInput = null;
		resolveManualInput(allowEmpty ? input : trimmedInput);
	}

	const nextAttempt = setAttemptStatus(attemptId, env, "running", {
		nextAction: {
			type: "wait",
			message: "Manual input received. Waiting for provider confirmation.",
		},
		appendProgress: "Received manual callback input.",
	});

	return {
		ok: true,
		statusCode: 202,
		attempt: nextAttempt ?? attempt,
	};
}

export function cancelActiveProviderSignIn(
	provider: string,
	env: NodeJS.ProcessEnv = process.env,
	options?: { reason?: string; markAttemptCancelled?: boolean },
): ModelProviderSignInAttempt | null {
	const activeAttempt = findActiveAttemptByProvider(provider);
	if (!activeAttempt) return null;

	activeAttempt.cancelled = true;
	activeAttempt.abortController.abort();
	if (activeAttempt.rejectManualInput) {
		const rejectManualInput = activeAttempt.rejectManualInput;
		activeAttempt.rejectManualInput = null;
		activeAttempt.resolveManualInput = null;
		rejectManualInput(new Error(options?.reason ?? "Signin attempt cancelled."));
	}

	if (options?.markAttemptCancelled) {
		return (
			setAttemptStatus(activeAttempt.attemptId, env, "cancelled", {
				nextAction: { type: "none" },
				error: options.reason ?? "Signin attempt cancelled.",
				appendProgress: options.reason ?? "Signin attempt cancelled.",
				markCompleted: true,
			}) ?? materializeAttempt(activeAttempt.attemptId, env)
		);
	}

	return materializeAttempt(activeAttempt.attemptId, env);
}

export function cancelModelProviderSignInAttempt(
	provider: string,
	attemptId: string,
	env: NodeJS.ProcessEnv = process.env,
): CancelAttemptSuccess | CancelAttemptFailure {
	const attempt = materializeAttempt(attemptId, env);
	if (!attempt || attempt.provider !== provider) {
		return {
			ok: false,
			statusCode: 404,
			error: "signin_attempt_not_found",
			message: `No signin attempt was found for provider "${provider}" and id "${attemptId}".`,
		};
	}

	if (isTerminalAttemptStatus(attempt.status)) {
		return {
			ok: false,
			statusCode: 409,
			error: "signin_attempt_not_active",
			message: `Signin attempt "${attemptId}" is already ${attempt.status}.`,
			attempt,
		};
	}

	const cancelledAttempt = cancelActiveProviderSignIn(provider, env, {
		reason: "Signin attempt cancelled by user.",
		markAttemptCancelled: true,
	});
	if (!cancelledAttempt || cancelledAttempt.id !== attemptId) {
		return {
			ok: false,
			statusCode: 409,
			error: "signin_attempt_not_active",
			message: `Signin attempt "${attemptId}" is no longer active.`,
			attempt,
		};
	}

	return {
		ok: true,
		statusCode: 200,
		attempt: cancelledAttempt,
	};
}
