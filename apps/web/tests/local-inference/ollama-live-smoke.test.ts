import { describe, expect, it } from "vitest";
import { OllamaClient } from "@/lib/local-inference/ollama-client";

describe("Live Ollama Smoke Test (Opt-in Ambient Hardware)", () => {
  const client = new OllamaClient();

  it(
    "probes live Ollama and generates a short completion if opted-in and daemon is running",
    async () => {
      // Opt-in gate: only runs full live generation when OLLAMA_LIVE_TEST=true
      const isOptedIn = process.env.OLLAMA_LIVE_TEST?.trim() === "true";
      if (!isOptedIn) {
        console.log("Opt-in live smoke test skipped: OLLAMA_LIVE_TEST is not set.");
        return;
      }

      console.log("Starting live Ollama smoke test...");
      const probe = await client.checkConnection();
      console.log("Live probe result:", probe);

      if (probe.state !== "connected") {
        console.log("Opt-in Live Smoke Test skipped: Ollama probe was not connected:", probe);
        return;
      }

      // 1. Verify tags
      const models = await client.listModels();
      expect(models.length).toBeGreaterThan(0);

      const testModel = models[0].name;

      // 2. Perform a fast chat completion with 30s timeout
      const deltas: string[] = [];
      let statsReceived = false;

      await client.chatStream(
        {
          model: testModel,
          messages: [{ role: "user", content: "Reply with the single word 'ok'" }],
          options: {
            num_predict: 5,
            temperature: 0.1,
          },
        },
        {
          onDelta: (d) => deltas.push(d),
          onStats: (stats) => {
            expect(stats.totalDurationMs).toBeGreaterThan(0);
            expect(stats.evalCount).toBeGreaterThan(0);
            statsReceived = true;
          },
        }
      );

      expect(deltas.length).toBeGreaterThan(0);
      expect(statsReceived).toBe(true);
    },
    30000 // 30s timeout for local model cold-loading
  );
});
