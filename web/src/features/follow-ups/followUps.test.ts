/** Tests for how audio results are sorted into speech, music or no follow-ups (`followUps.ts`). */
import assert from "node:assert/strict";
import test from "node:test";
import { audioKind, followUpsFor } from "./followUps.ts";

test("audioKind maps API types case-insensitively", () => {
  for (const type of ["VOICE_GENERATOR", "VOICE_CLONER", "VOICE_CHANGER", "AUDIO_TRANSLATOR", "voice_generator"]) {
    assert.equal(audioKind(type), "speech", type);
  }
  assert.equal(audioKind("MUSIC_GENERATOR"), "music");
  assert.equal(audioKind(" music_generator "), "music");
  assert.equal(audioKind("SOUND_EFFECT_GENERATOR"), null);
  assert.equal(audioKind(undefined), null);
});

test("other or missing audio types get no actions", () => {
  for (const type of ["SOUND_EFFECT_GENERATOR", "VIDEO_TO_AUDIO", undefined, null, "", 3]) {
    assert.deepEqual(followUpsFor("audio", type), { visible: [], more: [] }, String(type));
  }
});
