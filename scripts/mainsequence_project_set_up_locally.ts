import process from "node:process";
import { ExitCodeError, setupProjectLocally } from "./mainsequence_project_local_setup.js";

function fail(message: string): never {
	console.error(`[astro] ${message}`);
	process.exit(1);
}

async function main() {
	const forwardedArgs = process.argv.slice(2);
	if (forwardedArgs.length === 0) {
		fail(
			"Usage: tsx /app/scripts/mainsequence_project_set_up_locally.ts <project-id> [--base-dir <dir>] [--scaffold-docker|--no-scaffold-docker]",
		);
	}

	await setupProjectLocally(forwardedArgs, { streamOutput: true });
}

try {
	await main();
} catch (error) {
	if (error instanceof ExitCodeError) {
		process.exit(error.exitCode);
	}
	fail(error instanceof Error ? error.message : String(error));
}
