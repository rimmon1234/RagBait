import { describe, expect, it } from "vitest";
import { OllamaError } from "@/lib/local-inference/ollama-errors";
import { parseNDJSONStream } from "@/lib/local-inference/ollama-stream";

/**
 * Helper to build a ReadableStream from string chunks.
 */
function createStream(chunks: string[], delayMs = 0): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    async start(controller) {
      for (const chunk of chunks) {
        if (delayMs > 0) {
          await new Promise((r) => setTimeout(r, delayMs));
        }
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

/**
 * Helper to build a stream from raw byte buffers (useful for testing multibyte slicing).
 */
function createByteStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

describe("parseNDJSONStream", () => {
  it("parses multiple JSON objects in a single chunk", async () => {
    const raw =
      '{"message":{"content":"Hello"}}\n{"message":{"content":" world"}}\n{"done":true}\n';
    const stream = createStream([raw]);

    const results: any[] = [];
    await parseNDJSONStream(stream, (chunk) => results.push(chunk));

    expect(results).toHaveLength(3);
    expect(results[0].message.content).toBe("Hello");
    expect(results[1].message.content).toBe(" world");
    expect(results[2].done).toBe(true);
  });

  it("handles JSON objects split across multiple chunks", async () => {
    const stream = createStream([
      '{"mess',
      'age":{"content":"Spl',
      'it test"}}\n{"do',
      'ne":true}\n',
    ]);

    const results: any[] = [];
    await parseNDJSONStream(stream, (chunk) => results.push(chunk));

    expect(results).toHaveLength(2);
    expect(results[0].message.content).toBe("Split test");
    expect(results[1].done).toBe(true);
  });

  it("handles multibyte UTF-8 codepoints split across chunk boundaries", async () => {
    const encoder = new TextEncoder();
    // 🌍 is 4 bytes: [240, 159, 140, 141]
    const fullText = '{"message":{"content":"Hello 🌍 Earth"}}\n';
    const fullBytes = encoder.encode(fullText);

    // Find where the 🌍 starts
    const earthIndex = fullText.indexOf("🌍");
    const earthByteIndex = encoder.encode(fullText.slice(0, earthIndex)).length;

    // Split right in the middle of the 4-byte emoji (e.g. 2 bytes in first chunk, 2 in second)
    const splitPoint = earthByteIndex + 2;
    const chunk1 = fullBytes.slice(0, splitPoint);
    const chunk2 = fullBytes.slice(splitPoint);

    const stream = createByteStream([chunk1, chunk2]);

    const results: any[] = [];
    await parseNDJSONStream(stream, (chunk) => results.push(chunk));

    expect(results).toHaveLength(1);
    expect(results[0].message.content).toBe("Hello 🌍 Earth");
  });

  it("handles CRLF line endings", async () => {
    const raw = '{"line":1}\r\n{"line":2}\r\n';
    const stream = createStream([raw]);

    const results: any[] = [];
    await parseNDJSONStream(stream, (chunk) => results.push(chunk));

    expect(results).toHaveLength(2);
    expect(results[0].line).toBe(1);
    expect(results[1].line).toBe(2);
  });

  it("processes a trailing valid line without a trailing newline", async () => {
    const raw = '{"line":1}\n{"line":2}';
    const stream = createStream([raw]);

    const results: any[] = [];
    await parseNDJSONStream(stream, (chunk) => results.push(chunk));

    expect(results).toHaveLength(2);
    expect(results[0].line).toBe(1);
    expect(results[1].line).toBe(2);
  });

  it("throws PARSE_ERROR on invalid JSON syntax", async () => {
    const raw = '{"valid":true}\n{invalid_json_here}\n';
    const stream = createStream([raw]);

    const results: any[] = [];
    await expect(
      parseNDJSONStream(stream, (chunk) => results.push(chunk))
    ).rejects.toThrow(OllamaError);
  });

  it("aborts cleanly when AbortSignal triggers", async () => {
    const controller = new AbortController();
    const stream = createStream(
      ['{"chunk":1}\n', '{"chunk":2}\n', '{"chunk":3}\n'],
      50
    );

    const results: any[] = [];
    const parsePromise = parseNDJSONStream(
      stream,
      (chunk) => {
        results.push(chunk);
        if (results.length === 1) {
          controller.abort();
        }
      },
      controller.signal
    );

    await expect(parsePromise).rejects.toMatchObject({
      code: "ABORTED",
    });
    expect(results).toHaveLength(1);
  });
});
