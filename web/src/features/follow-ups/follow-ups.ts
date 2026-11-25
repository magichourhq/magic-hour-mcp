/** Follow-up actions on a finished result card. Only actions with an MCP tool that can act on the draft are listed. */

type FollowUpId =
  | "edit-image" | "animate" | "upscale-image" | "swap-face-image" | "remove-background" | "talking-photo"
  | "edit-video" | "swap-face-video" | "lip-sync" | "upscale-video" | "subtitles" | "translate"
  | "talking-photo-from-audio" | "lip-sync-from-audio" | "video-from-audio" | "clone-voice" | "music-video";

export type FollowUp = {
  id: FollowUpId;
  label: string;
  /** First line of the draft. */
  instruction: string;
  /** Input the user still has to add, or "" when the draft is complete. */
  needs: string;
};

type FollowUps = { visible: FollowUp[]; more: FollowUp[] };

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
 * Deferred until their tools exist: Expand Image (image More); Extend Video (first in video More),
 * Add Audio and Expand Video (video More); Change This Voice (first on speech).
 */
const CARDS: Record<"image" | "video" | "speech" | "music", { visible: FollowUpId[]; more: FollowUpId[] }> = {
  image: { visible: ["edit-image", "animate", "upscale-image"], more: ["swap-face-image", "remove-background", "talking-photo"] },
  video: { visible: ["edit-video", "swap-face-video"], more: ["lip-sync", "upscale-video", "subtitles", "translate"] },
  speech: { visible: ["talking-photo-from-audio", "lip-sync-from-audio"], more: ["video-from-audio", "clone-voice"] },
  music: { visible: ["music-video"], more: [] },
};

const SPEECH_TYPES = new Set(["VOICE_GENERATOR", "VOICE_CLONER", "VOICE_CHANGER", "AUDIO_TRANSLATOR"]);

/** Other or missing audio types (sound effects, video-to-audio) get no follow-ups. */
function audioKind(type: unknown): "speech" | "music" | null {
  if (typeof type !== "string") return null;
  const value = type.trim().toUpperCase();
  if (SPEECH_TYPES.has(value)) return "speech";
  if (value === "MUSIC_GENERATOR") return "music";
  return null;
}

type CardKind = keyof typeof CARDS;

function cardKind(projectType: string, audioType: unknown): CardKind | null {
  if (projectType === "image" || projectType === "video") return projectType;
  if (projectType === "audio") return audioKind(audioType);
  return null;
}

function expand(ids: FollowUpId[]): FollowUp[] {
  return ids.map((id) => ({ id, ...ACTIONS[id] }));
}

export function followUpsFor(projectType: string, audioType?: unknown): FollowUps {
  const card = cardKind(projectType, audioType);
  if (!card) return { visible: [], more: [] };
  return { visible: expand(CARDS[card].visible), more: expand(CARDS[card].more) };
}

const VIDEO_SOURCE_INSTRUCTIONS = "Read the actual clip duration before setting segment times. Preserve the full clip unless the user requests a segment; explain any unsupported requirements instead of silently trimming. This source item is not a target language or edit instruction; ask only for missing instructions if needed. This source binding alone does not authorize generation or saving.";
const AUDIO_SOURCE_INSTRUCTIONS = "Use this source audio as the audio input. It is not a script or instruction; ask only for missing inputs if needed. This source binding alone does not authorize generation or saving.";
const IMAGE_SOURCE_INSTRUCTIONS = "This source item is not an edit or movement instruction; ask only for missing inputs if needed. It does not authorize generation or saving.";

export function sourceInstructionsFor(projectType: string): string {
  if (projectType === "video") return VIDEO_SOURCE_INSTRUCTIONS;
  if (projectType === "audio") return AUDIO_SOURCE_INSTRUCTIONS;
  return IMAGE_SOURCE_INSTRUCTIONS;
}

export function draftReadyMessage(name: string, projectType: string, outputNumber: number, action: FollowUp): string {
  const nextStep = action.needs ? `Add ${action.needs}` : "Review it";
  return `Draft for ${name}, ${projectType} ${outputNumber}, is ready. ${nextStep} in the chat, then send.`;
}
