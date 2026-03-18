import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { refreshDocsIndex } from "../extensions/shared/docsIndex.js";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const result = refreshDocsIndex(repoRoot);

for (const filePath of result.generatedFiles) {
	console.log(path.relative(repoRoot, filePath));
}
