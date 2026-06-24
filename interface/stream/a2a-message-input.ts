import { createHash } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

export const DEFAULT_A2A_FILE_MAX_BYTES = 15 * 1024 * 1024;

export type A2AInputFileManifest = {
	partIndex: number;
	filename: string;
	mediaType: string;
	path: string;
	sha256: string;
	sizeBytes: number;
	source: "raw" | "url";
};

export type A2AMessageInput = {
	text: string;
	files: A2AInputFileManifest[];
	partFingerprints: Array<Record<string, unknown>>;
};

export type A2AMessageInputResult =
	| { ok: true; input: A2AMessageInput }
	| { ok: false; message: string; field: string };

type NormalizeA2AMessageInputOptions = {
	parts: unknown;
	contextId: string;
	messageId: string;
	sessionAssetsRoot: string;
	maxFileBytes?: number;
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function sanitizeScopeKey(value: string): string {
	return value.trim().replace(/[^a-zA-Z0-9._-]+/g, "_");
}

function isInsidePath(parent: string, child: string): boolean {
	const relative = path.relative(parent, child);
	return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function normalizeMediaType(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value.trim().toLowerCase() : null;
}

function normalizeFilename(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	if (!trimmed || trimmed === "." || trimmed === "..") return null;
	if (trimmed.includes("/") || trimmed.includes("\\")) return null;
	if (path.basename(trimmed) !== trimmed) return null;
	const safe = trimmed.replace(/[^a-zA-Z0-9._-]+/g, "_");
	if (!safe || safe === "." || safe === "..") return null;
	return safe.startsWith(".") ? `file${safe}` : safe;
}

function hasUnsupportedFileAliases(part: Record<string, unknown>): boolean {
	return (
		part.kind !== undefined ||
		part.file !== undefined ||
		part.bytes !== undefined ||
		part.mimeType !== undefined ||
		part.name !== undefined
	);
}

function decodeStrictBase64(value: unknown): Buffer | null {
	if (typeof value !== "string") return null;
	const input = value.trim();
	if (!input || input.length % 4 !== 0) return null;
	if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(input)) {
		return null;
	}
	const decoded = Buffer.from(input, "base64");
	if (decoded.length === 0) return null;
	return decoded;
}

function materializePdfRawPart(input: {
	part: Record<string, unknown>;
	partIndex: number;
	contextId: string;
	messageId: string;
	sessionAssetsRoot: string;
	maxFileBytes: number;
}): A2AMessageInputResult {
	const fieldPrefix = `message.parts[${input.partIndex}]`;
	if (input.part.raw !== undefined && input.part.url !== undefined) {
		return {
			ok: false,
			message: "A2A file parts must contain exactly one of raw or url.",
			field: fieldPrefix,
		};
	}
	if (input.part.url !== undefined) {
		return {
			ok: false,
			message: "A2A file Part.url is not supported yet; send inline PDF bytes with Part.raw.",
			field: `${fieldPrefix}.url`,
		};
	}
	if (input.part.raw === undefined) {
		return {
			ok: false,
			message: "A2A file parts must contain raw PDF bytes.",
			field: `${fieldPrefix}.raw`,
		};
	}

	const filename = normalizeFilename(input.part.filename);
	if (!filename) {
		return {
			ok: false,
			message: "A2A PDF file parts require a safe filename.",
			field: `${fieldPrefix}.filename`,
		};
	}
	const mediaType = normalizeMediaType(input.part.mediaType);
	if (mediaType !== "application/pdf") {
		return {
			ok: false,
			message: "Only application/pdf A2A file parts are supported.",
			field: `${fieldPrefix}.mediaType`,
		};
	}
	const decoded = decodeStrictBase64(input.part.raw);
	if (!decoded) {
		return {
			ok: false,
			message: "A2A PDF file part raw content must be non-empty strict base64.",
			field: `${fieldPrefix}.raw`,
		};
	}
	if (decoded.length > input.maxFileBytes) {
		return {
			ok: false,
			message: `A2A PDF file part exceeds the configured ${input.maxFileBytes} byte limit.`,
			field: `${fieldPrefix}.raw`,
		};
	}
	if (decoded.subarray(0, 5).toString("ascii") !== "%PDF-") {
		return {
			ok: false,
			message: "A2A PDF file part content does not start with a PDF header.",
			field: `${fieldPrefix}.raw`,
		};
	}

	const sha256 = createHash("sha256").update(decoded).digest("hex");
	const root = path.resolve(input.sessionAssetsRoot);
	const messageDir = path.resolve(
		root,
		sanitizeScopeKey(input.contextId),
		"a2a-inputs",
		sanitizeScopeKey(input.messageId),
	);
	if (!isInsidePath(root, messageDir)) {
		return {
			ok: false,
			message: "A2A PDF file materialization path escaped the session asset root.",
			field: fieldPrefix,
		};
	}
	mkdirSync(messageDir, { recursive: true, mode: 0o700 });
	const targetPath = path.resolve(messageDir, `${input.partIndex}-${sha256.slice(0, 12)}-${filename}`);
	if (!isInsidePath(messageDir, targetPath)) {
		return {
			ok: false,
			message: "A2A PDF file materialization path escaped the message asset root.",
			field: fieldPrefix,
		};
	}
	if (!existsSync(targetPath)) {
		writeFileSync(targetPath, decoded, { mode: 0o600 });
	}

	return {
		ok: true,
		input: {
			text: "",
			files: [
				{
					partIndex: input.partIndex,
					filename,
					mediaType,
					path: targetPath,
					sha256,
					sizeBytes: decoded.length,
					source: "raw",
				},
			],
			partFingerprints: [
				{
					kind: "file",
					partIndex: input.partIndex,
					filename,
					mediaType,
					sha256,
					sizeBytes: decoded.length,
					source: "raw",
				},
			],
		},
	};
}

export function normalizeA2AMessageInput(options: NormalizeA2AMessageInputOptions): A2AMessageInputResult {
	if (!Array.isArray(options.parts) || options.parts.length === 0) {
		return {
			ok: false,
			message: "A2A message.parts must contain at least one part.",
			field: "message.parts",
		};
	}

	const maxFileBytes =
		Number.isFinite(options.maxFileBytes) && Number(options.maxFileBytes) > 0
			? Math.floor(Number(options.maxFileBytes))
			: DEFAULT_A2A_FILE_MAX_BYTES;
	const textParts: string[] = [];
	const files: A2AInputFileManifest[] = [];
	const partFingerprints: Array<Record<string, unknown>> = [];

	for (const [index, part] of options.parts.entries()) {
		const fieldPrefix = `message.parts[${index}]`;
		if (!isPlainObject(part)) {
			return {
				ok: false,
				message: "A2A message parts must be objects.",
				field: fieldPrefix,
			};
		}
		if (hasUnsupportedFileAliases(part)) {
			return {
				ok: false,
				message: "A2A file parts must use standard Part.raw or Part.url with filename and mediaType.",
				field: fieldPrefix,
			};
		}
		if (typeof part.text === "string") {
			textParts.push(part.text);
			partFingerprints.push({ kind: "text", text: part.text });
			continue;
		}
		if (part.data !== undefined) {
			textParts.push(JSON.stringify(part.data));
			partFingerprints.push({ kind: "data", data: part.data, mediaType: part.mediaType ?? null });
			continue;
		}
		if (part.raw !== undefined || part.url !== undefined) {
			const materialized = materializePdfRawPart({
				part,
				partIndex: index,
				contextId: options.contextId,
				messageId: options.messageId,
				sessionAssetsRoot: options.sessionAssetsRoot,
				maxFileBytes,
			});
			if (materialized.ok === false) return materialized;
			files.push(...materialized.input.files);
			partFingerprints.push(...materialized.input.partFingerprints);
			continue;
		}
		return {
			ok: false,
			message: "Only text, data, and application/pdf raw file A2A message parts are supported.",
			field: fieldPrefix,
		};
	}

	const text = textParts.join("\n").trim();
	if (!text && files.length === 0) {
		return {
			ok: false,
			message: "A2A message.parts did not contain non-empty text, data, or file input.",
			field: "message.parts",
		};
	}
	return {
		ok: true,
		input: {
			text,
			files,
			partFingerprints,
		},
	};
}
