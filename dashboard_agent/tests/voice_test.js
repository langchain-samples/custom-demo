/* Node test for the voice shell's pure helpers (frontend/src/lib/voice.ts).
 *
 * Imports the REAL module the app ships via Node's native type stripping, like
 * stream_event_test.js. The class in that module needs a browser (WebSocket, AudioContext);
 * everything tested here is the pure part: PCM conversion, frame parsing, and the two
 * messages whose exact shape the Live API refuses to be flexible about.
 *
 * Run: node dashboard_agent/tests/voice_test.js
 */
const assert = require("node:assert");
const path = require("node:path");

const MOD = path.join(__dirname, "..", "..", "frontend", "src", "lib", "voice.ts");

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log("  ok -", name);
}

(async () => {
  const {
    floatToPcm16,
    pcm16ToFloat,
    encodeBase64,
    decodeBase64,
    setupMessage,
    supportsNonBlocking,
    toolResponseMessage,
    toolCallsFrom,
    audioChunksFrom,
    transcriptsFrom,
    wasInterrupted,
    resumptionHandle,
    spokenResult,
    liveUrl,
    downsampleTo16k,
    realtimeAudioMessage,
    INVOKE_TOOL,
    RESUME_TOOL,
  } = await import(MOD);

  ok("float samples round-trip through 16-bit PCM", () => {
    const input = new Float32Array([0, 0.5, -0.5, 1, -1]);
    const back = pcm16ToFloat(floatToPcm16(input));
    assert.equal(back.length, input.length);
    for (let i = 0; i < input.length; i++) {
      assert.ok(Math.abs(back[i] - input[i]) < 0.001, `sample ${i}: ${back[i]} vs ${input[i]}`);
    }
  });

  ok("out-of-range samples clamp instead of wrapping", () => {
    // Web Audio can hand back values just outside -1..1. Letting those wrap turns a
    // quiet moment into a loud click, so they must saturate.
    const view = new DataView(floatToPcm16(new Float32Array([2, -2])));
    assert.equal(view.getInt16(0, true), 32767);
    assert.equal(view.getInt16(2, true), -32767);
  });

  ok("base64 round-trips a buffer larger than one chunk", () => {
    // The encoder batches through String.fromCharCode, which blows up on a long
    // utterance if the chunking is wrong.
    const bytes = new Uint8Array(100000).map((_, i) => i % 256);
    const back = new Uint8Array(decodeBase64(encodeBase64(bytes.buffer)));
    assert.equal(back.length, bytes.length);
    assert.deepEqual(back.subarray(0, 300), bytes.subarray(0, 300));
    assert.deepEqual(back.subarray(99700), bytes.subarray(99700));
  });

  ok("setup declares both tools and prefixes the model as a resource name", () => {
    const setup = setupMessage("gemini-3.1-flash-live-preview").setup;
    assert.equal(setup.model, "models/gemini-3.1-flash-live-preview");
    const names = setup.tools[0].functionDeclarations.map((d) => d.name);
    assert.deepEqual(names, [INVOKE_TOOL, RESUME_TOOL]);
    // Resumption is always on: a Live connection dies at ~10 minutes, a demo does not.
    assert.deepEqual(setup.sessionResumption, {});
  });

  ok("a resume handle is carried into setup when we have one", () => {
    const setup = setupMessage("m", "handle-1").setup;
    assert.deepEqual(setup.sessionResumption, { handle: "handle-1" });
  });

  ok("NON_BLOCKING is declared only where the docs support it", () => {
    // 2.5 documents `behavior`; 3.1's function calling is synchronous-only. Sending it
    // anyway is a risk for no gain, because the two-phase tool response is what actually
    // keeps the model talking during a long run.
    assert.equal(supportsNonBlocking("gemini-2.5-flash-native-audio-preview-12-2025"), true);
    assert.equal(supportsNonBlocking("gemini-3.1-flash-live-preview"), false);
    const on = setupMessage("gemini-2.5-flash-native-audio-preview-12-2025").setup;
    const off = setupMessage("gemini-3.1-flash-live-preview").setup;
    assert.equal(on.tools[0].functionDeclarations[0].behavior, "NON_BLOCKING");
    assert.equal("behavior" in off.tools[0].functionDeclarations[0], false);
  });

  ok("a tool response carries the call id and its scheduling", () => {
    const call = { id: "call-1", name: INVOKE_TOOL, args: {} };
    const msg = toolResponseMessage(call, { answer: "done" }, "INTERRUPT");
    const [response] = msg.toolResponse.functionResponses;
    assert.equal(response.id, "call-1");
    assert.equal(response.name, INVOKE_TOOL);
    // `scheduling` rides INSIDE the response payload, not beside it.
    assert.equal(response.response.scheduling, "INTERRUPT");
    assert.equal(response.response.answer, "done");
  });

  ok("tool calls are read out of a server frame", () => {
    const calls = toolCallsFrom({
      toolCall: {
        functionCalls: [
          { id: "a", name: INVOKE_TOOL, args: { question: "why" } },
          { id: "b" }, // nameless: not a call we can run
        ],
      },
    });
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0], { id: "a", name: INVOKE_TOOL, args: { question: "why" } });
    assert.deepEqual(toolCallsFrom({}), []);
    assert.deepEqual(toolCallsFrom(null), []);
  });

  ok("audio chunks and transcripts are read out of a server frame", () => {
    const frame = {
      serverContent: {
        modelTurn: { parts: [{ inlineData: { data: "AAA" } }, { text: "ignored" }] },
        inputTranscription: { text: "what happened" },
        outputTranscription: { text: "units fell" },
      },
    };
    assert.deepEqual(audioChunksFrom(frame), ["AAA"]);
    assert.deepEqual(transcriptsFrom(frame), { input: "what happened", output: "units fell" });
    assert.deepEqual(transcriptsFrom({}), { input: "", output: "" });
  });

  ok("barge-in and resumption handles are detected", () => {
    assert.equal(wasInterrupted({ serverContent: { interrupted: true } }), true);
    assert.equal(wasInterrupted({ serverContent: {} }), false);
    assert.equal(
      resumptionHandle({ sessionResumptionUpdate: { resumable: true, newHandle: "h" } }),
      "h",
    );
    // Not resumable yet: offering the handle anyway would mean reconnecting with a
    // handle the server will reject.
    assert.equal(resumptionHandle({ sessionResumptionUpdate: { newHandle: "h" } }), "");
  });

  ok("the spoken result carries prose plus the on-screen figures", () => {
    // The prompt keeps figures in the widgets and the prose short, so prose alone leaves
    // an eyes-free listener with no numbers at all. The digest is how they get them
    // without the agent reciting the dashboard.
    const out = spokenResult("Units fell across three SKUs.", [
      { title: "Units", value: "-12%" },
      { title: "No value" },
    ]);
    assert.equal(out.answer, "Units fell across three SKUs.");
    assert.deepEqual(out.on_screen, ["Units: -12%"]);
  });

  ok("the url uses the constrained RPC and the access_token param", () => {
    // Both halves are load-bearing, and both were verified against the live API:
    // the plain `BidiGenerateContent` refuses an ephemeral token with `1008 Method
    // doesn't allow unregistered callers`, and `?key=` with `1007 Missing or malformed
    // auth token ... pass it in an access_token query parameter`. Either way the socket
    // just opens and closes, which is indistinguishable from a normal hang-up.
    const url = liveUrl("auth_tokens/abc+def");
    assert.ok(url.includes("GenerativeService.BidiGenerateContentConstrained"), url);
    assert.ok(url.includes("access_token=auth_tokens%2Fabc%2Bdef"), url);
    assert.ok(!url.includes("key="), "an ephemeral token is not an API key");
  });

  ok("mic audio is sent as `audio`, never as the deprecated `mediaChunks`", () => {
    // The server rejects mediaChunks outright: `1007 realtime_input.media_chunks is
    // deprecated. Use audio, video, or text instead` - and it only ever fires in front
    // of a real microphone, so no amount of protocol testing without one catches it.
    const msg = realtimeAudioMessage(new Uint8Array([1, 2, 3, 4]).buffer);
    assert.ok(!("mediaChunks" in msg.realtimeInput), "mediaChunks is rejected by the server");
    assert.equal(msg.realtimeInput.audio.mimeType, "audio/pcm;rate=16000");
    assert.equal(typeof msg.realtimeInput.audio.data, "string");
  });

  ok("the downsample averages rather than dropping samples", () => {
    // Picking every Nth sample aliases everything above 8kHz into the speech band. An
    // alternating +1/-1 signal is the worst case: averaging cancels it to ~0, while
    // picking preserves it at full amplitude as a phantom tone.
    const alternating = new Float32Array(48).map((_, i) => (i % 2 ? 1 : -1));
    const out = downsampleTo16k(alternating, 48000);
    assert.equal(out.length, 16);
    assert.ok(Math.max(...out.map(Math.abs)) < 0.4, `aliased: ${out.slice(0, 4)}`);
    // Already at (or below) the target rate: nothing to do.
    const same = new Float32Array([0.5, -0.5]);
    assert.equal(downsampleTo16k(same, 16000), same);
  });

  console.log(`\n${passed} passed`);
})();
