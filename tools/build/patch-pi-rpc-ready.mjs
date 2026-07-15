import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..", "..");
const rpcModePath = path.join(
	repoRoot,
	"node_modules",
	"@mariozechner",
	"pi-coding-agent",
	"dist",
	"modes",
	"rpc",
	"rpc-mode.js",
);

if (!existsSync(rpcModePath)) {
	throw new Error(`Pi RPC mode file not found at ${rpcModePath}`);
}

const readyFrame = `    output({
        type: "runtime_ready",
        protocol: "pi-rpc",
        version: 1,
        sessionFile: session.sessionFile,
        sessionId: session.sessionId,
    });
`;

const marker = `    detachInput = attachJsonlLineReader(process.stdin, (line) => {
        void handleInputLine(line);
    });
`;

const source = readFileSync(rpcModePath, "utf8");

if (source.includes('type: "runtime_ready"')) {
	console.log("[astro] Pi RPC readiness sentinel patch already applied.");
	process.exit(0);
}

if (!source.includes(marker)) {
	throw new Error("Could not find Pi RPC stdin attachment marker to patch.");
}

writeFileSync(rpcModePath, source.replace(marker, `${marker}${readyFrame}`));
console.log("[astro] Patched Pi RPC mode to emit runtime_ready after stdin is attached.");
