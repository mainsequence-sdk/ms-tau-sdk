export type SameSessionTurnTask = () => Promise<void>;

export class SameSessionTurnQueue {
	private readonly queues = new Map<string, Promise<void>>();

	has(key: string): boolean {
		return this.queues.has(key);
	}

	delete(key: string): boolean {
		return this.queues.delete(key);
	}

	enqueue(key: string, task: SameSessionTurnTask): Promise<void> {
		const previous = this.queues.get(key) ?? Promise.resolve();
		const next = previous
			.catch(() => undefined)
			.then(task)
			.finally(() => {
				if (this.queues.get(key) === next) {
					this.queues.delete(key);
				}
			});
		this.queues.set(key, next);
		return next;
	}
}

export type ActiveWarmRunnerTurn<TContext> = {
	ctx: TContext;
	completed: boolean;
	startedAt: number;
	resolve: () => void;
};

export type WarmRunnerTurnHolder<TContext, TTurn extends ActiveWarmRunnerTurn<TContext>> = {
	currentTurn: TTurn | null;
};

export function warmRunnerTurnExpired<TContext>(
	turn: ActiveWarmRunnerTurn<TContext> | null,
	input: {
		nowMs?: number;
		timeoutMs: number;
		isContextFinished: (ctx: TContext) => boolean;
	},
): boolean {
	if (!turn || turn.completed || input.isContextFinished(turn.ctx)) return false;
	return (input.nowMs ?? Date.now()) - turn.startedAt > input.timeoutMs;
}

export function releaseActiveWarmRunnerTurn<
	TContext,
	TTurn extends ActiveWarmRunnerTurn<TContext>,
>(
	holder: WarmRunnerTurnHolder<TContext, TTurn>,
	ctx: TContext,
	input?: {
		onRelease?: (turn: TTurn) => void;
	},
): TTurn | null {
	const turn = holder.currentTurn;
	if (!turn || turn.ctx !== ctx || turn.completed) return null;
	turn.completed = true;
	if (holder.currentTurn === turn) holder.currentTurn = null;
	input?.onRelease?.(turn);
	turn.resolve();
	return turn;
}
