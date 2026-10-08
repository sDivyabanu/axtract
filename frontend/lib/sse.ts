// Minimal server-sent-events reader for fetch() responses (EventSource cannot POST a file).

export interface SseMessage {
  event: string;
  data: string;
}

/** Incremental parser: feed it text chunks, get complete messages back. Comments (": ping") are dropped. */
export class SseParser {
  private buffer = "";

  push(chunk: string): SseMessage[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const out: SseMessage[] = [];
    let index: number;
    while ((index = this.buffer.indexOf("\n\n")) !== -1) {
      const block = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue;
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) out.push({ event, data: data.join("\n") });
    }
    return out;
  }
}

/** Read a whole SSE body, calling `onMessage` for each message. Resolves when the stream closes. */
export async function readSse(
  body: ReadableStream<Uint8Array>,
  onMessage: (m: SseMessage) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const m of parser.push(decoder.decode(value, { stream: true }))) onMessage(m);
  }
}
