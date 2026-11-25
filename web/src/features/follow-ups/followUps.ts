/**
 * Follow-up actions on a finished result card (ENG-6714). Each one opens an unsent
 * ChatGPT draft. Only actions with an MCP tool that can finish the request are listed.
 * Pure data and functions, with no React or DOM imports, so it can be tested with `node --test`.
 * @module
 */

/** Stable id of a follow-up action, used as the React key and to look up its copy. */
type FollowUpId =
  | "edit-image" | "animate" | "upscale-image" | "swap-face-image" | "remove-background" | "talking-photo"
  | "edit-video" | "swap-face-video" | "lip-sync" | "upscale-video" | "subtitles" | "translate"
  | "talking-photo-from-audio" | "lip-sync-from-audio" | "video-from-audio" | "clone-voice" | "music-video";

/** One follow-up action as the card shows it and as its draft starts. */
export type FollowUp = {
  id: FollowUpId;
  /** Button or menu item text */
  label: string;
  /** First line of the draft. */
  instruction: string;
  /** The input the user still has to add in the draft, or "" when the draft is complete. */
  needs: string;
};

/** The actions for one card: `visible` on the card, left to right; `more` under More, top to bottom. */
type FollowUps = { visible: FollowUp[]; more: FollowUp[] };

/** Label, draft instruction and missing input for every follow-up action. */
const ACTIONS: Record<FollowUpId, Omit<FollowUp, "id">> = {
  "edit-image": { label: "Edit", instruction: "Edit this image:", needs: "your edit" },
  animate: { label: "Animate", instruction: "Animate this image. The movement should be:", needs: "the movement" },
  "upscale-image": { label: "Upscale", instruction: "Upscale this image.", needs: "" },
  "swap-face-image": { label: "Swap Faces", instruction: "Swap the face in this image with this face:", needs: "the face image" },
  "remove-background": { label: "Remove Background", instruction: "Remove the background from this image.", needs: "" },
  "talking-photo": { label: "Make a Talking Photo", instruction: "Make a talking photo from this image with this audio:", needs: "the audio" },
  "edit-video": { label: "Edit Video", instruction: "Edit this video:", needs: "your edit" },
  "swap-face-video": { label: "Swap Faces", instruction: "Swap the face in this video with this face:", needs: "the face image" },
  "lip-sync": { label: "Lip Sync", instruction: "Lip sync this video to this audio:", needs: "the audio" },
  "upscale-video": { label: "Upscale", instruction: "Upscale this video, preserving the full clip.", needs: "" },
  subtitles: { label: "Add Subtitles", instruction: "Add subtitles to this video, preserving the full clip.", needs: "" },
  translate: { label: "Translate", instruction: "Translate this video's speech into:", needs: "the target language" },
  "talking-photo-from-audio": { label: "Make a Talking Photo", instruction: "Make a talking photo that speaks this audio. Use this portrait:", needs: "the portrait" },
  "lip-sync-from-audio": { label: "Lip Sync a Video", instruction: "Lip sync a video to this audio. Use this video:", needs: "the video" },
  "video-from-audio": { label: "Make a Video from Audio", instruction: "Make a video from this audio. The visuals should be:", needs: "the visuals" },
  "clone-voice": { label: "Clone Voice", instruction: "Clone this voice. The new speech should say:", needs: "the text to speak" },
  "music-video": { label: "Create a Music Video", instruction: "Create a music video for this track. The visuals should be:", needs: "the visuals" },
};

/**
 * Left to right on the card, then top to bottom under More.
 * Deferred until their handoffs exist: Expand Image (image More); Extend Video (first in
 * video More), Add Audio and Expand Video (video More); Change This Voice (first on speech).
 */
const CARDS: Record<"image" | "video" | "speech" | "music", { visible: FollowUpId[]; more: FollowUpId[] }> = {
  image: { visible: ["edit-image", "animate", "upscale-image"], more: ["swap-face-image", "remove-background", "talking-photo"] },
  video: { visible: ["edit-video", "swap-face-video"], more: ["lip-sync", "upscale-video", "subtitles", "translate"] },
  speech: { visible: ["talking-photo-from-audio", "lip-sync-from-audio"], more: ["video-from-audio", "clone-voice"] },
  music: { visible: ["music-video"], more: [] },
};

/** Audio project `type` values from the API that count as speech. */
const SPEECH_TYPES = new Set(["VOICE_GENERATOR", "VOICE_CLONER", "VOICE_CHANGER", "AUDIO_TRANSLATOR"]);

/**
 * Tells speech from music using the API's audio project `type`, ignoring case and spaces.
 * @param type The audio project's `type`, for example `"VOICE_GENERATOR"`. Any value is accepted.
 * @returns `"speech"` or `"music"`, or `null` for any other or missing type (sound effects,
 * video-to-audio), so those results get no follow-ups rather than a guess.
 */
export function audioKind(type: unknown): "speech" | "music" | null {
  if (typeof type !== "string") return null;
  const value = type.trim().toUpperCase();
  if (SPEECH_TYPES.has(value)) return "speech";
  if (value === "MUSIC_GENERATOR") return "music";
  return null;
}

/** The four card layouts: image, video, speech and music. */
type CardKind = keyof typeof CARDS;

/**
 * Picks the card layout for a finished result.
 * @param projectType The card's media type: `"image"`, `"video"`, `"audio"` or anything else.
 * @param audioType The audio project's `type`, only read for audio results.
 * @returns The layout, or `null` when the result gets no follow-ups.
 */
function cardKind(projectType: string, audioType: unknown): CardKind | null {
  if (projectType === "image" || projectType === "video") return projectType;
  if (projectType === "audio") return audioKind(audioType);
  return null;
}

/**
 * Turns action ids into full actions, keeping their order.
 * @param ids Action ids in display order.
 * @returns Each id with its label, instruction and missing input from `ACTIONS`.
 */
function expand(ids: FollowUpId[]): FollowUp[] {
  return ids.map((id) => ({ id, ...ACTIONS[id] }));
}

/**
 * Lists the follow-up actions a finished result's card shows.
 * @param projectType The card's media type: `"image"`, `"video"` or `"audio"`.
 * @param audioType The audio project's `type`, which decides speech vs. music for audio.
 * @returns The actions on the card and under More, in display order. Both lists are empty
 * for results that get no follow-ups; `more` is empty for music.
 */
export function followUpsFor(projectType: string, audioType?: unknown): FollowUps {
  const card = cardKind(projectType, audioType);
  if (!card) return { visible: [], more: [] };
  return { visible: expand(CARDS[card].visible), more: expand(CARDS[card].more) };
}

/** Draft text telling the model how to use a source video. */
const VIDEO_SOURCE_INSTRUCTIONS = "Read the actual clip duration before setting segment times. Preserve the full clip unless the user requests a segment; explain any unsupported requirements instead of silently trimming. This source item is not a target language or edit instruction; ask only for missing instructions if needed. This source binding alone does not authorize generation or saving.";
/** Draft text telling the model how to use a source audio clip. */
const AUDIO_SOURCE_INSTRUCTIONS = "Use this source audio as the audio input. It is not a script or instruction; ask only for missing inputs if needed. This source binding alone does not authorize generation or saving.";
/** Draft text telling the model how to use a source image. */
const IMAGE_SOURCE_INSTRUCTIONS = "This source item is not an edit or movement instruction; ask only for missing inputs if needed. It does not authorize generation or saving.";

/**
 * Picks the draft text that tells the model how to treat the source file: use it as the
 * input, don't read it as an instruction, and don't generate anything until the user sends.
 * @param projectType The source's media type: `"video"`, `"audio"`, or anything else for an image.
 * @returns The source instructions for that media type.
 */
export function sourceInstructionsFor(projectType: string): string {
  if (projectType === "video") return VIDEO_SOURCE_INSTRUCTIONS;
  if (projectType === "audio") return AUDIO_SOURCE_INSTRUCTIONS;
  return IMAGE_SOURCE_INSTRUCTIONS;
}

/**
 * Builds the message the card shows once a draft is open in the chat.
 * @param name The project's name as the card shows it.
 * @param projectType The source's media type, for example `"image"`.
 * @param outputNumber The selected output, counting from 1.
 * @param action The action whose draft was opened.
 * @returns For example "Draft for Demo image, image 2, is ready. Add your edit in the chat,
 * then send." When the action needs no input, it says "Review it" instead of "Add …".
 */
export function draftReadyMessage(name: string, projectType: string, outputNumber: number, action: FollowUp): string {
  const nextStep = action.needs ? `Add ${action.needs}` : "Review it";
  return `Draft for ${name}, ${projectType} ${outputNumber}, is ready. ${nextStep} in the chat, then send.`;
}
