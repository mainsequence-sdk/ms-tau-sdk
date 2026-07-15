import { bootstrapMainsequenceCliAuth } from "../runtime-auth.js";

try {
	await bootstrapMainsequenceCliAuth({
		env: process.env,
		log: (message) => {
			console.error(`[mainsequence-auth-repair] ${message}`);
		},
	});
} catch (error) {
	const message = error instanceof Error ? error.message : String(error);
	console.error(`[mainsequence-auth-repair] ${message}`);
	process.exit(1);
}
