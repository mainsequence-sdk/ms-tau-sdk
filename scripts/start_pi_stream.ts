import { bootstrapPiAgentDir } from "./bootstrap_pi_agent_dir.mjs";

bootstrapPiAgentDir();

await import("../interface/stream/server.js");
