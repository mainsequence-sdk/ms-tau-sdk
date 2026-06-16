export type A2ASessionRuntimeState = "starting" | "ready" | "busy" | "failed" | "detached";

export type A2ASessionRuntimeLastError = {
	code: string;
	message: string;
	at: string;
};

export type A2ASessionRuntimeAttachment = {
	agentSessionUid: string;
	threadId: string | null;
	agentType: string;
	userUid: string;
	state: A2ASessionRuntimeState;
	attachedAt: string;
	updatedAt: string;
	expiresAt: string | null;
	detachedAt: string | null;
	lastError: A2ASessionRuntimeLastError | null;
};

export type A2ASessionRuntimeAttachInput = {
	agentSessionUid: string;
	threadId: string | null;
	agentType: string;
	userUid: string;
	expiresAt?: string | null;
};

export class A2ASessionRuntimeRegistry {
	private readonly records = new Map<string, A2ASessionRuntimeAttachment>();

	constructor(private readonly now: () => Date = () => new Date()) {}

	attach(input: A2ASessionRuntimeAttachInput): A2ASessionRuntimeAttachment {
		const now = this.now().toISOString();
		const existing = this.records.get(input.agentSessionUid);
		if (
				existing &&
				existing.state !== "detached" &&
				existing.agentType === input.agentType
			) {
			const updated = {
				...existing,
				threadId: input.threadId ?? existing.threadId,
				userUid: input.userUid,
				updatedAt: now,
				expiresAt: input.expiresAt ?? existing.expiresAt,
			};
			this.records.set(input.agentSessionUid, updated);
			return updated;
		}

		const next: A2ASessionRuntimeAttachment = {
				agentSessionUid: input.agentSessionUid,
				threadId: input.threadId,
				agentType: input.agentType,
				userUid: input.userUid,
				state: "starting",
			attachedAt: now,
			updatedAt: now,
			expiresAt: input.expiresAt ?? null,
			detachedAt: null,
			lastError: null,
		};
		this.records.set(input.agentSessionUid, next);
		return next;
	}

	get(agentSessionUid: string): A2ASessionRuntimeAttachment | null {
		return this.records.get(agentSessionUid) ?? null;
	}

	update(
		agentSessionUid: string,
		updates: Partial<
			Pick<A2ASessionRuntimeAttachment, "state" | "expiresAt" | "lastError" | "threadId">
		>,
	): A2ASessionRuntimeAttachment | null {
		const existing = this.records.get(agentSessionUid);
		if (!existing) return null;
		const next = {
			...existing,
			...updates,
			updatedAt: this.now().toISOString(),
		};
		this.records.set(agentSessionUid, next);
		return next;
	}

	detach(agentSessionUid: string): A2ASessionRuntimeAttachment | null {
		const existing = this.records.get(agentSessionUid);
		if (!existing) return null;
		const now = this.now().toISOString();
		const next = {
			...existing,
			state: "detached" as const,
			updatedAt: now,
			detachedAt: now,
		};
		this.records.set(agentSessionUid, next);
		return next;
	}
}
