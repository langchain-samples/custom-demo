/* Node test for the conversation recorder (frontend/src/lib/voiceRecorder.ts).
 *
 * This is what makes a voice trace PLAYABLE, and the bug it can have is silent: audio that
 * is present but garbled (chunks laid on top of each other) or drifting (two sides on
 * different timelines). Both look fine in code and only fail when someone listens.
 *
 * Run: node dashboard_agent/tests/voice_recorder_test.js
 */
const assert = require("node:assert");
const path = require("node:path");

const MOD = path.join(__dirname, "..", "..", "frontend", "src", "lib", "voiceRecorder.ts");

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log("  ok -", name);
}

(async () => {
  const { layout, resample, stereoWav } = await import(MOD);

  ok("bursts stay contiguous instead of overlapping", () => {
    // Model audio arrives faster than real time: three 1s chunks can all land within a few
    // ms. Placed at arrival time they would overwrite each other into a garbled blip.
    const chunk = (v) => new Float32Array(24000).fill(v);
    const track = layout(
      [
        { at: 0.0, samples: chunk(0.1) },
        { at: 0.01, samples: chunk(0.2) },
        { at: 0.02, samples: chunk(0.3) },
      ],
      24000,
    );
    assert.equal(track.length, 72000, "three seconds of audio must occupy three seconds");
    assert.ok(Math.abs(track[100] - 0.1) < 1e-6);
    assert.ok(Math.abs(track[24100] - 0.2) < 1e-6);
    assert.ok(Math.abs(track[48100] - 0.3) < 1e-6);
  });

  ok("a real pause leaves real silence", () => {
    const track = layout(
      [
        { at: 0, samples: new Float32Array(2400).fill(0.5) },
        { at: 5, samples: new Float32Array(2400).fill(0.5) },
      ],
      24000,
    );
    assert.equal(track.length, 5 * 24000 + 2400);
    assert.equal(track[24000 * 2], 0, "the gap is silence, not the previous chunk");
  });

  ok("empty chunks and no input are handled", () => {
    assert.equal(layout([], 24000).length, 0);
    assert.equal(layout([{ at: 0, samples: new Float32Array(0) }], 24000).length, 0);
  });

  ok("the user track is resampled onto the model's rate", () => {
    // The two sides run at different rates (16k in, 24k out). Mixing them without this
    // makes the user's audio play 1.5x fast, drifting further apart the longer you talk.
    const oneSecondAt16k = new Float32Array(16000).fill(0.25);
    const out = resample(oneSecondAt16k, 16000, 24000);
    assert.equal(out.length, 24000, "one second stays one second");
    assert.ok(Math.abs(out[12000] - 0.25) < 1e-6);
    // Same rate is a no-op, not a copy-with-rounding.
    assert.equal(resample(oneSecondAt16k, 16000, 16000), oneSecondAt16k);
  });

  ok("the WAV is a valid 16-bit stereo header with the sides split", () => {
    const left = new Float32Array([1, 1, 1]);
    const right = new Float32Array([-1, -1, -1]);
    const wav = stereoWav(left, right, 24000);
    const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);
    const tag = (o) => String.fromCharCode(...wav.subarray(o, o + 4));
    assert.equal(tag(0), "RIFF");
    assert.equal(tag(8), "WAVE");
    assert.equal(tag(36), "data");
    assert.equal(view.getUint16(22, true), 2, "stereo");
    assert.equal(view.getUint32(24, true), 24000);
    assert.equal(view.getUint16(34, true), 16, "16-bit");
    assert.equal(view.getUint32(4, true), wav.length - 8, "RIFF size counts the rest");
    assert.equal(view.getUint32(40, true), 3 * 4, "data size is frames * 4");
    // Left is the user, right is the assistant. Swapping them is invisible in code and
    // obvious the moment anyone puts on headphones.
    assert.equal(view.getInt16(44, true), 32767);
    assert.equal(view.getInt16(46, true), -32767);
  });

  ok("mismatched lengths pad rather than truncate", () => {
    // One side always stops talking first; the tail must survive.
    const wav = stereoWav(new Float32Array(10).fill(0.5), new Float32Array(2), 24000);
    const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);
    assert.equal(view.getUint32(40, true), 10 * 4);
    assert.ok(view.getInt16(44 + 9 * 4, true) > 0, "the longer side keeps its tail");
  });

  console.log(`\n${passed} passed`);
})();
