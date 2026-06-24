import assert from "node:assert/strict";
import { mkdtemp, readFile, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
	normalizeA2AMessageInput,
} from "../interface/stream/a2a-message-input.js";

function pdfBase64(text = "%PDF-1.4\n% test pdf\n"): string {
	return Buffer.from(text, "utf8").toString("base64");
}

async function tempAssetsRoot(): Promise<string> {
	return mkdtemp(path.join(tmpdir(), "astro-a2a-input-"));
}

test("A2A message input materializes standard inline PDF raw parts", async () => {
	const sessionAssetsRoot = await tempAssetsRoot();
	const result = normalizeA2AMessageInput({
		parts: [
			{ text: "Please summarize this PDF." },
			{
				raw: pdfBase64(),
				filename: "report.pdf",
				mediaType: "application/pdf",
			},
		],
		contextId: "session-uid",
		messageId: "msg-001",
		sessionAssetsRoot,
	});

	assert.equal(result.ok, true);
	if (result.ok !== true) throw new Error("Expected valid A2A input.");
	assert.equal(result.input.text, "Please summarize this PDF.");
	assert.equal(result.input.files.length, 1);
	const file = result.input.files[0];
	assert.equal(file?.filename, "report.pdf");
	assert.equal(file?.mediaType, "application/pdf");
	assert.equal(file?.source, "raw");
	assert.match(file?.path ?? "", /session-uid\/a2a-inputs\/msg-001\/1-[a-f0-9]{12}-report\.pdf$/);
	assert.equal((await readFile(file?.path ?? "", "utf8")).startsWith("%PDF-"), true);
	assert.equal((await stat(file?.path ?? "")).mode & 0o777, 0o600);
	assert.deepEqual(result.input.partFingerprints[1], {
		kind: "file",
		partIndex: 1,
		filename: "report.pdf",
		mediaType: "application/pdf",
		sha256: file?.sha256,
		sizeBytes: file?.sizeBytes,
		source: "raw",
	});
	assert.equal(JSON.stringify(result.input.partFingerprints).includes(pdfBase64()), false);
});

test("A2A message input accepts file-only PDF requests", async () => {
	const sessionAssetsRoot = await tempAssetsRoot();
	const result = normalizeA2AMessageInput({
		parts: [
			{
				raw: pdfBase64(),
				filename: "report.pdf",
				mediaType: "application/pdf",
			},
		],
		contextId: "session-uid",
		messageId: "msg-file-only",
		sessionAssetsRoot,
	});

	assert.equal(result.ok, true);
	if (result.ok === true) {
		assert.equal(result.input.text, "");
		assert.equal(result.input.files.length, 1);
	}
});

test("A2A message input rejects non-standard file wrappers", async () => {
	const result = normalizeA2AMessageInput({
		parts: [
			{ text: "summarize" },
			{
				kind: "file",
				file: {
					name: "report.pdf",
					mimeType: "application/pdf",
					bytes: pdfBase64(),
				},
			},
		],
		contextId: "session-uid",
		messageId: "msg-001",
		sessionAssetsRoot: await tempAssetsRoot(),
	});

	assert.equal(result.ok, false);
	if (result.ok === false) {
		assert.equal(result.field, "message.parts[1]");
		assert.match(result.message, /standard Part\.raw or Part\.url/);
	}
});

test("A2A message input rejects invalid PDF raw parts", async () => {
	const cases: Array<{ name: string; part: Record<string, unknown>; field: string }> = [
		{
			name: "missing media type",
			part: { raw: pdfBase64(), filename: "report.pdf" },
			field: "message.parts[0].mediaType",
		},
		{
			name: "non-pdf media type",
			part: { raw: pdfBase64(), filename: "report.pdf", mediaType: "text/plain" },
			field: "message.parts[0].mediaType",
		},
		{
			name: "invalid base64",
			part: { raw: "not base64", filename: "report.pdf", mediaType: "application/pdf" },
			field: "message.parts[0].raw",
		},
		{
			name: "path traversal filename",
			part: { raw: pdfBase64(), filename: "../report.pdf", mediaType: "application/pdf" },
			field: "message.parts[0].filename",
		},
		{
			name: "non-pdf bytes",
			part: { raw: Buffer.from("hello", "utf8").toString("base64"), filename: "report.pdf", mediaType: "application/pdf" },
			field: "message.parts[0].raw",
		},
	];

	for (const testCase of cases) {
		const result = normalizeA2AMessageInput({
			parts: [testCase.part],
			contextId: "session-uid",
			messageId: `msg-${testCase.name}`,
			sessionAssetsRoot: await tempAssetsRoot(),
		});
		assert.equal(result.ok, false, testCase.name);
		if (result.ok === false) assert.equal(result.field, testCase.field, testCase.name);
	}
});

test("A2A message input rejects oversized inline PDFs and URL file parts", async () => {
	const oversized = normalizeA2AMessageInput({
		parts: [
			{
				raw: pdfBase64("%PDF-1.4\n123456789"),
				filename: "report.pdf",
				mediaType: "application/pdf",
			},
		],
		contextId: "session-uid",
		messageId: "msg-oversize",
		sessionAssetsRoot: await tempAssetsRoot(),
		maxFileBytes: 8,
	});
	assert.equal(oversized.ok, false);
	if (oversized.ok === false) assert.equal(oversized.field, "message.parts[0].raw");

	const url = normalizeA2AMessageInput({
		parts: [
			{
				url: "https://example.com/report.pdf",
				filename: "report.pdf",
				mediaType: "application/pdf",
			},
		],
		contextId: "session-uid",
		messageId: "msg-url",
		sessionAssetsRoot: await tempAssetsRoot(),
	});
	assert.equal(url.ok, false);
	if (url.ok === false) {
		assert.equal(url.field, "message.parts[0].url");
		assert.match(url.message, /not supported yet/);
	}
});
