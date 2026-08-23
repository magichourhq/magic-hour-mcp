# Magic Hour API reference for MCP tool design

Source: https://docs.magichour.ai/api-reference/openapi.json (fetched and saved as `docs/openapi.json`). Regenerate this file with `python docs/build_reference.py` if the spec changes.

## Authentication

- Every request requires `Authorization: Bearer <api_key>`.
- Get a key at https://magichour.ai/developer?tab=api-keys (Developer Hub → API Keys → Create key). Key is shown once.
- Base URL: `https://api.magichour.ai` (all paths below are relative to this, e.g. `/v1/ai-image-generator`).
- The API uses one static bearer token per account and has no OAuth session.
- **Mock server for dev/testing** (Python SDK): `Environment.MOCK_SERVER` (`https://api.sideko.dev/v1/mock/magichour/magic-hour/0.66.0`) returns instant mock data, accepts any token string, and spends no credits. For this MCP server, point `MAGIC_HOUR_API_BASE_URL` at that URL when you want mock API behavior.

## Async job lifecycle (applies to every `create`/generation endpoint)

1. `POST /v1/<tool>` → returns immediately with `{id, credits_charged}`. Credits are charged at request time (refunded if the job later errors).
2. Poll `GET /v1/{image,video,audio}-projects/{id}` until `status` leaves `queued`/`rendering`, or use the matching custom wait helper: `wait_for_image_project`, `wait_for_video_project`, or `wait_for_audio_project`.
3. Status enum: `draft | queued | rendering | complete | error | canceled`.
4. On `complete`, `downloads[]` contains `{url, expires_at}`. Download URLs expire after 24 hours; call GET again for fresh URLs.
5. On `error`, the `error: {message, code}` object is populated and credits are refunded.
6. `DELETE /v1/{image,video,audio}-projects/{id}` permanently deletes rendered output (irreversible).

The generated project detail and delete tools come from OpenAPI. The custom wait helpers wrap project details polling and return sanitized `exact_download_urls` separately from expiration metadata.

## File inputs - supported methods

Any `*_file_path` field in a request body should preferably use one of:
1. A Magic Hour library reference (file from a prior generation/upload in the account).
2. A `file_path` obtained via the presigned-upload flow:
   - `POST /v1/files/upload-urls` with `{"items":[{"type":"video","extension":"mp4"}]}` returns `{upload_url, expires_at, file_path}` per item.
   - `PUT` the raw file bytes to `upload_url`.
   - Use the returned `file_path` in the actual generation call.

Direct public media URLs can also work when they are stable, fetchable, and return raw file bytes. Treat them as best-effort rather than the default path, because hotlinked URLs can fail or return HTML/auth/redirect responses instead of the file.

MCP tool calls carry JSON, not binary data. Upload raw bytes separately. The flow is `video_assets_generate_presigned_url`, upload bytes, then pass `file_path` to the generated create tool. `video_assets_generate_presigned_url` is the shared `/v1/files/upload-urls` tool for `video`, `audio`, and `image` items.

## Output delivery

- Magic Hour always returns download URLs with `expires_at`.
- This MCP server returns sanitized `exact_download_urls` for completed projects when using `wait_for_*_project`.
- Use `exact_download_urls[n]` or the exact `downloads[n].url` value as the link. Never append `expires_at` or `download_expiration_metadata` to signed URLs.
- For image and audio projects, this MCP server also returns inline bytes when the client supports MCP image or audio content blocks.
- Video projects stay URL-only because MCP has no native video content block.

## Official Python SDK shape (`pip install magic_hour`)

Useful if the MCP server wraps the SDK instead of calling `httpx`/`requests` directly (matches "we just need to instantiate a client").

```python
from magic_hour import Client          # or AsyncClient
client = Client(token=API_KEY)         # or environment=Environment.MOCK_SERVER for testing
```

- `client.v1.<resource>.create(**params)` maps to each POST endpoint below and returns `id` and `credits_charged` immediately. Resource names use the snake_case path, for example `client.v1.ai_image_generator.create(...)` and `client.v1.face_swap_photo.create(...)`.
- `client.v1.<resource>.generate(**params, wait_for_completion=True, download_outputs=True, download_directory=".")` calls `create`, polls `check_result`, and downloads files to local disk. The default poll interval is 0.5 seconds; override it with `MAGIC_HOUR_POLL_INTERVAL`. In server code, set `download_outputs=False` and return URLs so files do not land on the MCP server's disk.
- `client.v1.image_projects`, `.video_projects`, and `.audio_projects` each have `.get(id=...)`, `.delete(id=...)`, and `.check_result(id=..., wait_for_completion, download_outputs, download_directory)`.
- `client.v1.files.upload_urls.create(...)`, `client.v1.face_detection.create(...)` / `.get(id=...)`.
- Sync `Client` and async `AsyncClient` have the same methods. Use `AsyncClient` inside an async MCP server such as FastMCP.

## Voice presets

- The Magic Hour API accepts `voice_name` as a string for AI voice generation.
- The runtime OpenAPI MCP server does not maintain a custom per-voice list tool.
- Use the Magic Hour product/docs as the source of truth for supported voice names, then pass the selected string into `ai_voice_generator_create_audio`.

## Magic Hour's documentation MCP

Magic Hour hosts `https://docs.magichour.ai/mcp` for documentation and code snippets. It does not execute API calls or expose action tools. This repo provides the separate action MCP server that calls the Magic Hour API.

## Webhooks

Magic Hour supports HMAC-SHA256 signed webhooks for image, video, and audio `started`, `completed`, and `errored` events. This server uses agent-driven polling through `get_details`; webhooks are an alternative for hosts that do not want long-running polling calls.

## Full endpoint index

| Method | Path | Category | Summary |
|---|---|---|---|
| POST | `/v1/face-detection` | Files | Face Detection |
| GET | `/v1/face-detection/{id}` | Files | Get face detection details |
| POST | `/v1/files/upload-urls` | Files | Generate asset upload urls |
| POST | `/v1/ai-talking-photo` | Video Projects | AI Talking Photo |
| POST | `/v1/ai-video-editor` | Video Projects | AI Video Editor |
| POST | `/v1/animation` | Video Projects | Animation |
| POST | `/v1/audio-to-video` | Video Projects | Audio-to-Video |
| POST | `/v1/auto-subtitle-generator` | Video Projects | Auto Subtitle Generator |
| POST | `/v1/character-replace` | Video Projects | Character Replace |
| POST | `/v1/face-swap` | Video Projects | Face Swap Video |
| POST | `/v1/image-to-video` | Video Projects | Image-to-Video |
| POST | `/v1/lip-sync` | Video Projects | Lip Sync |
| POST | `/v1/text-to-video` | Video Projects | Text-to-Video |
| GET | `/v1/video-projects/{id}` | Video Projects | Get video details |
| DELETE | `/v1/video-projects/{id}` | Video Projects | Delete video |
| POST | `/v1/video-to-video` | Video Projects | Video-to-Video |
| POST | `/v1/ai-clothes-changer` | Image Projects | AI Clothes Changer |
| POST | `/v1/ai-face-editor` | Image Projects | AI Face Editor |
| POST | `/v1/ai-gif-generator` | Image Projects | AI GIF Generator |
| POST | `/v1/ai-headshot-generator` | Image Projects | AI Headshot Generator |
| POST | `/v1/ai-image-editor` | Image Projects | AI Image Editor |
| POST | `/v1/ai-image-generator` | Image Projects | AI Image Generator |
| POST | `/v1/ai-image-upscaler` | Image Projects | AI Image Upscaler |
| POST | `/v1/ai-meme-generator` | Image Projects | AI Meme Generator |
| POST | `/v1/ai-qr-code-generator` | Image Projects | AI QR Code Generator |
| POST | `/v1/body-swap` | Image Projects | Body Swap |
| POST | `/v1/face-swap-photo` | Image Projects | Face Swap Photo |
| POST | `/v1/head-swap` | Image Projects | Head Swap |
| POST | `/v1/image-background-remover` | Image Projects | Image Background Remover |
| GET | `/v1/image-projects/{id}` | Image Projects | Get image details |
| DELETE | `/v1/image-projects/{id}` | Image Projects | Delete image |
| POST | `/v1/photo-colorizer` | Image Projects | Photo Colorizer |
| POST | `/v1/ai-voice-cloner` | Audio Projects | AI Voice Cloner |
| POST | `/v1/ai-voice-generator` | Audio Projects | AI Voice Generator |
| GET | `/v1/audio-projects/{id}` | Audio Projects | Get audio details |
| DELETE | `/v1/audio-projects/{id}` | Audio Projects | Delete audio |

## Per-endpoint detail


### Files


#### POST /v1/face-detection
`operationId: faceDetection.detectFaces`

Face Detection


**Request Body:**
- `confidence_score` (number, optional) default=0.5 range=[0,1]: Confidence threshold for filtering detected faces. * Higher values (e.g., 0.9) include only faces detected with high certainty, reducing false positives. * Lower values (e.g., 0.3) include more faces, but may increase...
- `assets` (object, required): Provide the assets for face detection
  - `target_file_path` (string, required): This is the image or video where the face will be detected. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): The id of the task. Use this value in the [get face detection details API](https://docs.magichour.ai/api-reference/files/get-face-detection-details) to get the details of the face detection task.
- `credits_charged` (integer, required): The credits charged for the task.

#### GET /v1/face-detection/{id}
`operationId: faceDetection.getDetails`

Get face detection details

**Path Parameters:**
- `id` (path, required): The id of the task. This value is returned by the [face detection API](https://docs.magichour.ai/api-reference/files/face-detection#response-id).

**Response 200:**
- `id` (string, required): The id of the task. This value is returned by the [face detection API](https://docs.magichour.ai/api-reference/files/face-detection#response-id).
- `credits_charged` (integer, required): The credits charged for the task.
- `status` (string, required) enum=['queued', 'rendering', 'complete', 'error']: The status of the detection.
- `faces` (array, required): The faces detected in the image or video. The list is populated as faces are detected.
  items:
    - `path` (string, required): The path to the face image. This should be used in face swap photo/video API calls as `.assets.face_mappings.original_face`
    - `url` (string, required): The url to the face image. This is used to render the image in your applications.

#### POST /v1/files/upload-urls
`operationId: videoAssets.generatePresignedUrl`

Generate asset upload urls


**Request Body:**
- `items` (array, required): The list of assets to upload. The response array will match the order of items in the request body.
  items:
    - `type` (string, required) enum=['video', 'audio', 'image']: The type of asset to upload. Possible types are video, audio, image
    - `extension` (string, required): The extension of the file to upload. Do not include the dot (.) before the extension. Possible extensions are...

**Response 200:**
- `items` (array, required): The list of upload URLs and file paths for the assets. The response array will match the order of items in the request body. Refer to the [Input Files Guide](https://docs.magichour.ai/integration/inputs-and-outputs) for...
  items:
    - `upload_url` (string, required): Used to upload the file to storage, send a PUT request with the file as data to upload.
    - `expires_at` (string, required): when the upload url expires, and will need to request a new one.
    - `file_path` (string, required): this value is used in APIs that needs assets, such as image_file_path, video_file_path, and audio_file_path

### Video projects


#### POST /v1/ai-talking-photo
`operationId: aiTalkingPhoto.createTalkingPhoto`

AI Talking Photo


**Request Body:**
- `name` (string, optional) default=Talking Photo - dateTime: Give your image a custom name for easy identification.
- `start_seconds` (number, required) range=[0,None]: The start time of the input audio in seconds. Maximum clip length depends on style.generation_mode: realistic 180s, prompted 45s.
- `end_seconds` (number, required) range=[0.1,None]: The end time of the input audio in seconds. Maximum clip length depends on style.generation_mode: realistic 180s, prompted 45s.
- `assets` (object, required): Provide the assets for creating a talking photo
  - `image_file_path` (string, required): The source image to animate. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls API](https://docs.magichour.ai/api-reference/files/generate-asset-upload-urls).
  - `audio_file_path` (string, required): The audio file to sync with the image. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
- `style` (object, optional): Attributes used to dictate the style of the output
  - `generation_mode` (string, optional) enum=['realistic', 'prompted', 'pro', 'standard', 'stable', 'expressive'] default=realistic: Controls overall motion style. * `realistic` - Maintains likeness well, high quality, and reliable. * `prompted` - Slightly lower likeness; allows option to prompt scene.
  - `prompt` (string, optional): A text prompt to guide the generation. Only applicable when generation_mode is `prompted`. This field is ignored for other modes.
- `max_resolution` (integer, optional): Constrains the larger dimension (height or width) of the output video. Allows you to set a lower resolution than your plan's maximum if desired. The value is capped by your plan's max resolution.

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### POST /v1/ai-video-editor
`operationId: aiVideoEditor.createVideo`

AI Video Editor


**Request Body:**
- `name` (string, optional) default=Video Editor - dateTime: Give your video a custom name for easy identification.
- `start_seconds` (number, optional) default=0 range=[0,None]: Start time of your clip (seconds). Must be ≥ 0.
- `end_seconds` (number, required) range=[0.1,None]: End time of your clip in seconds. Must be greater than `start_seconds`. Minimum duration depends on model: `gemini-omni`: 3s, `ltx-2.3`: 0.5s. Maximum duration depends on model: `gemini-omni`: 10s, `ltx-2.3`: 45s.
- `model` (string, optional) enum=['gemini-omni', 'ltx-2.3']: Editing model. Defaults to `ltx-2.3` for free tier and `gemini-omni` for paid. Use `ltx-2.3` for LTX video edit.
- `resolution` (string, optional) enum=['480p', '720p', '1080p']: Output resolution. Defaults to `480p` for free tier and `720p` for paid. Google Omni supports 720p only; LTX-2.3 supports 480p, 720p, and 1080p.
- `style` (object, required): 
  - `prompt` (string, required): The prompt used to edit the video.
- `assets` (object, required): Provide the assets for video editing.
  - `video_file_path` (string, required): The video to edit. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls API](https://docs.magichour.ai/api-reference/files/generate-asset-upload-urls).

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### POST /v1/animation
`operationId: animation.createVideo`

Animation


**Request Body:**
- `name` (string, optional) default=Animation - dateTime: Give your video a custom name for easy identification.
- `fps` (number, required) range=[1,None]: The desire output video frame rate
- `end_seconds` (number, required) range=[0.1,None]: This value determines the duration of the output video.
- `height` (integer, required) range=[64,None]: The height of the final output video. The maximum height depends on your subscription. Please refer to our [pricing page](https://magichour.ai/pricing) for more details
- `width` (integer, required) range=[64,None]: The width of the final output video. The maximum width depends on your subscription. Please refer to our [pricing page](https://magichour.ai/pricing) for more details
- `style` (object, required): Defines the style of the output video
  - `art_style` (string, required) enum=[47 values, e.g. ['Custom', 'Painterly Illustration', 'Vibrant Matte Illustration', 'Traditional Watercolor', 'Cyberpunk', 'Ink and Watercolor Portrait'], ...]: The art style used to create the output video
  - `art_style_custom` (string, optional): Describe custom art style. This field is required if `art_style` is `Custom`
  - `camera_effect` (string, required) enum=[52 values, e.g. ['Simple Zoom Out', 'Simple Zoom In', 'Bounce Out', 'Spin Bounce', 'Rolling Bounces', 'Rise and Climb'], ...]: The camera effect used to create the output video
  - `prompt_type` (string, required) enum=['custom', 'use_lyrics', 'ai_choose']: * `custom` - Use your own prompt for the video. * `use_lyrics` - Use the lyrics of the audio to create the prompt. If this option is selected, then `assets.audio_source` must be `file` or `youtube`. * `ai_choose` - Let...
  - `prompt` (string, optional): The prompt used for the video. Prompt is required if `prompt_type` is `custom`. Otherwise this value is ignored
  - `transition_speed` (integer, required) range=[1,10]: Change determines how quickly the video's content changes across frames. * Higher = more rapid transitions. * Lower = more stable visual experience.
- `assets` (object, required): Provide the assets for animation.
  - `audio_source` (string, required) enum=['none', 'file', 'youtube']: Optionally add an audio source if you'd like to incorporate audio into your video
  - `audio_file_path` (string, optional): The path of the input audio. This field is required if `audio_source` is `file`. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `youtube_url` (string, optional): Using a youtube video as the input source. This field is required if `audio_source` is `youtube`
  - `image_file_path` (string, optional): An initial image to use a the first frame of the video. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### POST /v1/audio-to-video
`operationId: audioToVideo.createVideo`

Audio-to-Video


**Request Body:**
- `name` (string, optional) default=Audio To Video - dateTime: Give your video a custom name for easy identification.
- `start_seconds` (number, optional) default=0 range=[0,None]: Start time of your clip (seconds). Must be ≥ 0.
- `end_seconds` (number, required) range=[0.1,None]: End time of your clip (seconds). Must be greater than start_seconds.
- `resolution` (string, optional) enum=['480p', '720p', '1080p']: Output video resolution. Defaults to `720p` on paid tiers and `480p` on free tiers.
- `assets` (object, required): Provide the audio file and an optional reference image.
  - `audio_file_path` (string, required): The path of the audio file. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls API](https://docs.magichour.ai/api-reference/files/generate-asset-upload-urls).
  - `image_file_path` (string, optional): Reference image for the initial frame of the video. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
- `style` (object, optional): Attributes used to dictate the style of the output
  - `prompt` (string, optional): Prompt to guide the visual style of the video.

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### POST /v1/auto-subtitle-generator
`operationId: autoSubtitleGenerator.createVideo`

Auto Subtitle Generator


**Request Body:**
- `name` (string, optional) default=Auto Subtitle - dateTime: Give your video a custom name for easy identification.
- `start_seconds` (number, required) range=[0,None]: Start time of your clip (seconds). Must be ≥ 0.
- `end_seconds` (number, required) range=[0.1,None]: End time of your clip (seconds). Must be greater than start_seconds.
- `assets` (object, required): Provide the assets for auto subtitle generator
  - `video_file_path` (string, required): This is the video used to add subtitles. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
- `style` (object, required): Style of the subtitle. At least one of `.style.template` or `.style.custom_config` must be provided. * If only `.style.template` is provided, default values for the template will be used. * If both are provided, the...
  - `template` (string, optional) enum=['karaoke', 'cinematic', 'minimalist', 'highlight']: Preset subtitle templates. Please visit https://magichour.ai/create/auto-subtitle-generator to see the style of the existing templates.
  - `custom_config` (object, optional): Custom subtitle configuration.
    - `font` (string, optional): Font name from Google Fonts. Not all fonts support all languages or character sets. We recommend verifying language support and appearance directly on https://fonts.google.com before use.
    - `font_size` (number, optional): Font size in pixels. If not provided, the font size is automatically calculated based on the video resolution.
    - `font_style` (string, optional): Font style (e.g., normal, italic, bold)
    - `text_color` (string, optional): Primary text color in hex format
    - `highlighted_text_color` (string, optional): Color used to highlight the current spoken text
    - `stroke_color` (string, optional): Stroke (outline) color of the text
    - `stroke_width` (number, optional): Width of the text stroke in pixels. If `stroke_color` is provided, but `stroke_width` is not, the `stroke_width` will be calculated automatically based on the font size.
    - `vertical_position` (string, optional): Vertical alignment of the text (e.g., top, center, bottom)
    - `horizontal_position` (string, optional): Horizontal alignment of the text (e.g., left, center, right)

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### POST /v1/character-replace
`operationId: characterReplace.createVideo`

Character Replace


**Request Body:**
- `name` (string, optional) default=Character Replace - dateTime: Give your video a custom name for easy identification.
- `start_seconds` (number, optional) default=0 range=[0,None]: Start time of your clip (seconds). Must be ≥ 0.
- `end_seconds` (number, required) range=[0.1,None]: End time of your clip (seconds). Must be greater than start_seconds.
- `resolution` (string, optional) enum=['480p', '720p']: Output video resolution. Defaults to 480p, the lowest resolution available on your plan.
- `assets` (object, required): Source video and reference character image for the job.
  - `video_file_path` (string, required): Source video containing the subject to replace or animate. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `image_file_path` (string, required): Reference character image used as the replacement or animation target. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
- `style` (object, optional): Optional style controls for replace vs animate mode and subject selection.
  - `mode` (string, optional) enum=['replace', 'animate']: Processing mode. `replace` swaps the detected subject with your reference character. `animate` transfers motion from the video onto your character image.
  - `selection_mode` (string, optional) enum=['auto', 'point']: How to locate the subject in the source video. `auto` detects a person automatically. `point` uses your `points` to mark the subject. Defaults to `auto`.
  - `points` (array, optional): On-frame markers for manual subject selection. Required when `selection_mode` is `point`. Ignored when `selection_mode` is `auto` or omitted.
    items:
      - `position_x` (integer, required) range=[0,None]: Horizontal pixel coordinate in the source video frame at `time_seconds`, measured from the left edge.
      - `position_y` (integer, required) range=[0,None]: Vertical pixel coordinate in the source video frame at `time_seconds`, measured from the top edge.
      - `time_seconds` (number, required) range=[0,None]: Timestamp on the source video timeline in seconds. Uses the same clock as `start_seconds` and `end_seconds`.

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### POST /v1/face-swap
`operationId: faceSwap.createVideo`

Face Swap Video


**Request Body:**
- `name` (string, optional) default=Face Swap - dateTime: Give your video a custom name for easy identification.
- `start_seconds` (number, required) range=[0,None]: Start time of your clip (seconds). Must be ≥ 0.
- `end_seconds` (number, required) range=[0.1,None]: End time of your clip (seconds). Must be greater than start_seconds.
- `style` (object, optional): Style of the face swap video.
  - `version` (string, optional) enum=['v1', 'v2', 'default']: * `v1` - May preserve skin detail and texture better, but weaker identity preservation. * `v2` - Faster, sharper, better handling of hair and glasses. stronger identity preservation. * `default` - Use the version we...
- `assets` (object, required): Provide the assets for face swap. For video, The `video_source` field determines whether `video_file_path` or `youtube_url` field is used
  - `face_swap_mode` (string, optional) enum=['all-faces', 'individual-faces'] default=all-faces: Choose how to swap faces: **all-faces** (recommended) — swap all detected faces using one source image (`source_file_path` required) +- **individual-faces** — specify exact mappings using `face_mappings`
  - `image_file_path` (string, optional): The path of the input image with the face to be swapped. The value is required if `face_swap_mode` is `all-faces`.
  - `face_mappings` (array, optional): This is the array of face mappings used for multiple face swap. The value is required if `face_swap_mode` is `individual-faces`.
    items:
      - `original_face` (string, required): The face detected from the image in `target_file_path`. The file name is in the format of `<face_frame>-<face_index>.png`. This value is corresponds to the response in the [face detection...
      - `new_face` (string, required): The face image that will be used to replace the face in the `original_face`. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `video_source` (string, required) enum=['file', 'youtube']: Choose your video source.
  - `video_file_path` (string, optional): Your video file. Required if `video_source` is `file`. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `youtube_url` (string, optional): YouTube URL (required if `video_source` is `youtube`).

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### POST /v1/image-to-video
`operationId: imageToVideo.createVideo`

Image-to-Video


**Request Body:**
- `name` (string, optional) default=Image To Video - dateTime: Give your video a custom name for easy identification.
- `end_seconds` (number, required) range=[1,60]: The total duration of the output video in seconds. Supported durations depend on the chosen model:
- `model` (string, optional) enum=[19 values, e.g. ['default', 'ltx-2', 'ltx-2.3', 'minimax-h3', 'wan-2.2', 'seedance-1.5'], ...] default=default: The AI model to use for video generation.
- `resolution` (string, optional) enum=['480p', '720p', '1080p', '4k']: Controls the output video resolution. Defaults to `720p` on paid tiers and `480p` on free tiers.
- `audio` (boolean, optional): Whether to include audio in the video. Defaults to `false` if not specified.
- `style` (object, optional): Attributed used to dictate the style of the output
  - `prompt` (string, optional): The prompt used for the video.
- `assets` (object, required): Provide the assets for image-to-video. Sora 2 only supports images with an aspect ratio of `9:16` or `16:9`.
  - `image_file_path` (string, required): The path of the image file. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls API](https://docs.magichour.ai/api-reference/files/generate-asset-upload-urls).
  - `end_image_file_path` (string, optional): The image to use as the last frame of the video.

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### POST /v1/lip-sync
`operationId: lipSync.createVideo`

Lip Sync


**Request Body:**
- `name` (string, optional) default=Lip Sync - dateTime: Give your video a custom name for easy identification.
- `start_seconds` (number, required) range=[0,None]: Start time of your clip (seconds). Must be ≥ 0.
- `end_seconds` (number, required) range=[0.1,None]: End time of your clip (seconds). Must be greater than start_seconds.
- `max_fps_limit` (number, optional) range=[1,None]: Defines the maximum FPS (frames per second) for the output video. If the input video's FPS is lower than this limit, the output video will retain the input FPS. This is useful for reducing unnecessary frame usage in...
- `assets` (object, required): Provide the assets for lip-sync. For video, The `video_source` field determines whether `video_file_path` or `youtube_url` field is used
  - `audio_file_path` (string, required): The path of the audio file. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls API](https://docs.magichour.ai/api-reference/files/generate-asset-upload-urls).
  - `video_source` (string, required) enum=['file', 'youtube']: Choose your video source.
  - `video_file_path` (string, optional): Your video file. Required if `video_source` is `file`. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `youtube_url` (string, optional): YouTube URL (required if `video_source` is `youtube`).
- `style` (object, optional): Attributes used to dictate the style of the output
  - `generation_mode` (string, optional) enum=['lite', 'standard', 'pro'] default=lite: A specific version of our lip sync system, optimized for different needs. * `lite` - Fast and affordable lip sync - best for simple videos. Costs 1 credit per frame of video. * `standard` - Natural, accurate lip sync -...

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### POST /v1/text-to-video
`operationId: textToVideo.createVideo`

Text-to-Video


**Request Body:**
- `name` (string, optional) default=Text To Video - dateTime: Give your video a custom name for easy identification.
- `end_seconds` (number, required) range=[1,60]: The total duration of the output video in seconds. Supported durations depend on the chosen model:
- `aspect_ratio` (string, optional) enum=['16:9', '9:16', '1:1']: Determines the aspect ratio of the output video.
- `resolution` (string, optional) enum=['480p', '720p', '1080p', '4k']: Controls the output video resolution. Defaults to `720p` on paid tiers and `480p` on free tiers.
- `model` (string, optional) enum=[19 values, e.g. ['default', 'ltx-2', 'ltx-2.3', 'minimax-h3', 'wan-2.2', 'seedance-1.5'], ...] default=default: The AI model to use for video generation.
- `audio` (boolean, optional): Whether to include audio in the video. Defaults to `false` if not specified.
- `style` (object, required): 
  - `prompt` (string, required): The prompt used for the video.

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

#### GET /v1/video-projects/{id}
`operationId: videoProjects.getDetails`

Get video details

**Path Parameters:**
- `id` (path, required): Unique ID of the video project. This value is returned by all of the POST APIs that create a video.

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `name` (string, required): The name of the video.
- `status` (string, required) enum=['draft', 'queued', 'rendering', 'complete', 'error', 'canceled']: The status of the video.
- `type` (string, required): The type of the video project. Possible values are ANIMATION, AUTO_SUBTITLE, VIDEO_TO_VIDEO, FACE_SWAP, TEXT_TO_VIDEO, IMAGE_TO_VIDEO, LIP_SYNC, TALKING_PHOTO, AVATAR, VIDEO_UPSCALER, VIDEO_EDITOR, CHARACTER_REPLACE,...
- `created_at` (string, required): 
- `width` (integer, required): The width of the final output video. A value of -1 indicates the width can be ignored.
- `height` (integer, required): The height of the final output video. A value of -1 indicates the height can be ignored.
- `enabled` (boolean, required): Whether this resource is active. If false, it is deleted.
- `start_seconds` (number, required) range=[0,None]: Start time of your clip (seconds). Must be ≥ 0.
- `end_seconds` (number, required) range=[0.1,None]: End time of your clip (seconds). Must be greater than start_seconds.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.
- `fps` (number, required): Frame rate of the video. If the status is not 'complete', the frame rate is an estimate and will be adjusted when the video completes.
- `error` (object, required): In the case of an error, this object will contain the error encountered during video render
  - `message` (string, required): Details on the reason why a failure happened.
  - `code` (string, required): An error code to indicate why a failure happened.
- `downloads` (array, required): 
  items:
    - `url` (string, required): 
    - `expires_at` (string, required): 

#### DELETE /v1/video-projects/{id}
`operationId: videoProjects.delete`

Delete video

**Path Parameters:**
- `id` (path, required): Unique ID of the video project. This value is returned by all of the POST APIs that create a video.

#### POST /v1/video-to-video
`operationId: videoToVideo.createVideo`

Video-to-Video


**Request Body:**
- `name` (string, optional) default=Video To Video - dateTime: Give your video a custom name for easy identification.
- `start_seconds` (number, required) range=[0,None]: Start time of your clip (seconds). Must be ≥ 0.
- `end_seconds` (number, required) range=[0.1,None]: End time of your clip (seconds). Must be greater than start_seconds.
- `fps_resolution` (string, optional) enum=['FULL', 'HALF'] default=HALF: Determines whether the resulting video will have the same frame per second as the original video, or half. * `FULL` - the result video will have the same FPS as the input video * `HALF` - the result video will have half...
- `style` (object, required): 
  - `art_style` (string, required) enum=[75 values, e.g. ['Minecraft', 'Watercolor', 'Pixel', 'Retro Sci-Fi', 'Lego', 'Origami'], ...]: 
  - `version` (string, optional) enum=['v1', 'v2', 'default'] default=default: * `v1` - more detail, closer prompt adherence, and frame-by-frame previews. * `v2` - faster, more consistent, and less noisy. * `default` - use the default version for the selected art style.
  - `prompt_type` (string, optional) enum=['default', 'custom', 'append_default'] default=default: * `default` - Use the default recommended prompt for the art style. * `custom` - Only use the prompt passed in the API. Note: for v1, lora prompt will still be auto added to apply the art style properly. *...
  - `prompt` (string, optional): The prompt used for the video. Prompt is required if `prompt_type` is `custom` or `append_default`. If `prompt_type` is `default`, then the `prompt` value passed will be ignored.
  - `model` (string, optional) enum=['Dreamshaper', 'Absolute Reality', 'Flat 2D Anime', 'Soft Anime', 'Kaywaii', 'Western Anime', '3D Anime', 'default'] default=default: * `Dreamshaper` - a good all-around model that works for both animations as well as realism. * `Absolute Reality` - better at realism, but you'll often get similar results with Dreamshaper as well. * `Flat 2D Anime` -...
- `assets` (object, required): Provide the assets for video-to-video. For video, The `video_source` field determines whether `video_file_path` or `youtube_url` field is used
  - `video_source` (string, required) enum=['file', 'youtube']: Choose your video source.
  - `video_file_path` (string, optional): Your video file. Required if `video_source` is `file`. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `youtube_url` (string, optional): YouTube URL (required if `video_source` is `youtube`).

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video.

### Image projects


#### POST /v1/ai-clothes-changer
`operationId: aiClothesChanger.createImage`

AI Clothes Changer


**Request Body:**
- `name` (string, optional) default=Clothes Changer - dateTime: Give your image a custom name for easy identification.
- `assets` (object, required): Provide the assets for clothes changer
  - `person_file_path` (string, required): The image with the person. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls API](https://docs.magichour.ai/api-reference/files/generate-asset-upload-urls).
  - `garment_file_path` (string, required): The image of the outfit. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls API](https://docs.magichour.ai/api-reference/files/generate-asset-upload-urls).
  - `garment_type` (string, optional) enum=['entire_outfit', 'upper_body', 'lower_body', 'dresses']: Type of garment to swap. If not provided, swaps the entire outfit. * `upper_body` - for shirts/jackets * `lower_body` - for pants/skirts * `dresses` - for entire outfit (deprecated, use `entire_outfit` instead) *...

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/ai-face-editor
`operationId: aiFaceEditor.editImage`

AI Face Editor


**Request Body:**
- `name` (string, optional) default=Face Editor - dateTime: Give your image a custom name for easy identification.
- `assets` (object, required): Provide the assets for face editor
  - `image_file_path` (string, required): This is the image whose face will be edited. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
- `style` (object, required): Face editing parameters
  - `enhance_face` (boolean, optional) default=False: Enhance face features
  - `eyebrow_direction` (number, optional) default=0 range=[-100,100]: Eyebrow direction (-100 to 100), in increments of 5
  - `eye_gaze_horizontal` (number, optional) default=0 range=[-100,100]: Horizontal eye gaze (-100 to 100), in increments of 5
  - `eye_gaze_vertical` (number, optional) default=0 range=[-100,100]: Vertical eye gaze (-100 to 100), in increments of 5
  - `eye_open_ratio` (number, optional) default=0 range=[-100,100]: Eye open ratio (-100 to 100), in increments of 5
  - `lip_open_ratio` (number, optional) default=0 range=[-100,100]: Lip open ratio (-100 to 100), in increments of 5
  - `head_roll` (number, optional) default=0 range=[-100,100]: Head roll (-100 to 100), in increments of 5
  - `mouth_grim` (number, optional) default=0 range=[-100,100]: Mouth grim (-100 to 100), in increments of 5
  - `mouth_pout` (number, optional) default=0 range=[-100,100]: Mouth pout (-100 to 100), in increments of 5
  - `mouth_purse` (number, optional) default=0 range=[-100,100]: Mouth purse (-100 to 100), in increments of 5
  - `mouth_smile` (number, optional) default=0 range=[-100,100]: Mouth smile (-100 to 100), in increments of 5
  - `mouth_position_horizontal` (number, optional) default=0 range=[-100,100]: Horizontal mouth position (-100 to 100), in increments of 5
  - `mouth_position_vertical` (number, optional) default=0 range=[-100,100]: Vertical mouth position (-100 to 100), in increments of 5
  - `head_pitch` (number, optional) default=0 range=[-100,100]: Head pitch (-100 to 100), in increments of 5
  - `head_yaw` (number, optional) default=0 range=[-100,100]: Head yaw (-100 to 100), in increments of 5

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/ai-gif-generator
`operationId: aiGifGenerator.createImage`

AI GIF Generator


**Request Body:**
- `name` (string, optional) default=Ai Gif - dateTime: Give your gif a custom name for easy identification.
- `style` (object, required): 
  - `prompt` (string, required): The prompt used for the GIF.
- `output_format` (string, optional) enum=['gif', 'mp4', 'webm'] default=gif: The output file format for the generated animation.

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/ai-headshot-generator
`operationId: aiHeadshotGenerator.createImage`

AI Headshot Generator


**Request Body:**
- `name` (string, optional) default=Ai Headshot - dateTime: Give your image a custom name for easy identification.
- `style` (object, optional): 
  - `prompt` (string, optional): Prompt used to guide the style of your headshot. We recommend omitting the prompt unless you want to customize your headshot. You can visit [AI headshot generator](https://magichour.ai/create/ai-headshot-generator) to...
- `assets` (object, required): Provide the assets for headshot photo
  - `image_file_path` (string, required): The image used to generate the headshot. This image must contain one detectable face. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/ai-image-editor
`operationId: aiImageEditor.createImage`

AI Image Editor


**Request Body:**
- `name` (string, optional) default=Ai Image Editor - dateTime: Give your image a custom name for easy identification.
- `image_count` (number, optional) enum=[1, 4, 9, 16] default=1: Number of images to generate. Maximum varies by model. Defaults to 1 if not specified.
- `model` (string, optional) enum=[11 values, e.g. ['default', 'nano-banana-2', 'gpt-image-2', 'flux-2-klein', 'nano-banana-2-lite', 'qwen-edit'], ...]: The AI model to use for image editing. Each model has different capabilities and costs.
- `aspect_ratio` (string, optional) enum=['auto', '16:9', '9:16', '4:3', '3:2', '1:1', '4:5', '2:3']: The aspect ratio of the output image(s). If not specified, defaults to `auto`.
- `resolution` (string, optional) enum=['auto', '640px', '1k', '2k', '4k']: Maximum resolution (longest edge) for the output image.
- `style` (object, required): 
  - `prompt` (string, required): The prompt used to edit the image.
- `assets` (object, required): Provide the assets for image edit
  - `image_file_paths` (array, optional): The image(s) used in the edit, maximum of 10 images. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
    items: type=string

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/ai-image-generator
`operationId: aiImageGenerator.createImage`

AI Image Generator


**Request Body:**
- `name` (string, optional) default=Ai Image - dateTime: Give your image a custom name for easy identification.
- `image_count` (integer, required) range=[1,16]: Number of images to generate. Maximum varies by model.
- `model` (string, optional) enum=[12 values, e.g. ['default', 'nano-banana-2', 'gpt-image-2', 'z-image-turbo', 'flux-2-klein', 'nano-banana-2-lite'], ...]: The AI model to use for image generation. Each model has different capabilities and costs.
- `aspect_ratio` (string, optional) enum=['1:1', '16:9', '9:16']: The aspect ratio of the output image(s). If not specified, defaults to `1:1` (square).
- `resolution` (string, optional) enum=['auto', '640px', '1k', '2k', '4k'] default=auto: Maximum resolution (longest edge) for the output image.
- `style` (object, required): The art style to use for image generation.
  - `prompt` (string, required): The prompt used for the image(s).
  - `tool` (string, optional) enum=[35 values, e.g. ['ai-anime-generator', 'ai-art-generator', 'ai-background-generator', 'ai-character-generator', 'ai-face-generator', 'ai-fashion-generator'], ...] default=general: The art style to use for image generation. Defaults to 'general' if not provided.

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/ai-image-upscaler
`operationId: aiImageUpscaler.createImage`

AI Image Upscaler


**Request Body:**
- `name` (string, optional) default=Image Upscaler - dateTime: Give your image a custom name for easy identification.
- `scale_factor` (number, required): How much to scale the image. Must be either 2 or 4. Note: 4x upscale is only available on Creator, Pro, or Business tier.
- `style` (object, optional) default={}: Style settings for the upscale. Use `mode` (`"preserve"`, `"balanced"`, or `"creative"`). Defaults to `"balanced"`.
  - `mode` (string, optional) enum=['pro', 'preserve', 'balanced', 'creative']: The upscaling mode. `"preserve"` uses the fast pro pipeline (1× credit multiplier). `"balanced"` and `"creative"` use the creative pipeline (2× credit multiplier). `"pro"` is deprecated and maps to `"preserve"`....
  - `prompt` (string, optional): A prompt to guide the final image. Only used when mode is `creative`.
- `assets` (object, required): Provide the assets for upscaling
  - `image_file_path` (string, required): The image to upscale. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls API](https://docs.magichour.ai/api-reference/files/generate-asset-upload-urls).

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/ai-meme-generator
`operationId: aiMemeGenerator.createImage`

AI Meme Generator


**Request Body:**
- `name` (string, optional): The name of the meme.
- `style` (object, required): 
  - `topic` (string, required): The topic of the meme.
  - `template` (string, required) enum=[13 values, e.g. ['Random', 'Drake Hotline Bling', 'Galaxy Brain', 'Two Buttons', "Gru's Plan", 'Tuxedo Winnie The Pooh'], ...]: To use our templates, pass in one of the enum values.
  - `searchWeb` (boolean, optional) default=False: Whether to search the web for meme content.

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/ai-qr-code-generator
`operationId: aiQrCodeGenerator.createImage`

AI QR Code Generator


**Request Body:**
- `name` (string, optional) default=Qr Code - dateTime: Give your image a custom name for easy identification.
- `content` (string, required): The content of the QR code.
- `style` (object, required): 
  - `art_style` (string, required): To use our templates, pass in one of Watercolor, Cyberpunk City, Ink Landscape, Interior Painting, Japanese Street, Mech, Minecraft, Picasso Painting, Game Map, Spaceship, Chinese Painting, Winter Village, or pass any...

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/body-swap
`operationId: bodySwap.createImage`

Body Swap


**Request Body:**
- `name` (string, optional) default=Body Swap - dateTime: Give your image a custom name for easy identification.
- `resolution` (string, required) enum=['640px', '1k', '2k', '4k']: Output resolution. Determines credits charged for the run.
- `assets` (object, required): Person image and scene image for body swap
  - `person_file_path` (string, required): Image of the person to place into the scene. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `scene_file_path` (string, required): Original scene image (background). This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/face-swap-photo
`operationId: faceSwapPhoto.createImage`

Face Swap Photo


**Request Body:**
- `name` (string, optional) default=Face Swap - dateTime: Give your image a custom name for easy identification.
- `assets` (object, required): Provide the assets for face swap photo
  - `face_swap_mode` (string, optional) enum=['all-faces', 'individual-faces'] default=all-faces: Choose how to swap faces: **all-faces** (recommended) — swap all detected faces using one source image (`source_file_path` required) +- **individual-faces** — specify exact mappings using `face_mappings`
  - `source_file_path` (string, optional): This is the image from which the face is extracted. The value is required if `face_swap_mode` is `all-faces`.
  - `face_mappings` (array, optional): This is the array of face mappings used for multiple face swap. The value is required if `face_swap_mode` is `individual-faces`.
    items:
      - `original_face` (string, required): The face detected from the image in `target_file_path`. The file name is in the format of `<face_frame>-<face_index>.png`. This value is corresponds to the response in the [face detection...
      - `new_face` (string, required): The face image that will be used to replace the face in the `original_face`. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `target_file_path` (string, required): This is the image where the face from the source image will be placed. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/head-swap
`operationId: headSwap.createImage`

Head Swap


**Request Body:**
- `name` (string, optional) default=Head Swap - dateTime: Give your image a custom name for easy identification.
- `max_resolution` (integer, optional): Constrains the larger dimension (height or width) of the output. Omit to use the maximum allowed for your plan (capped at 2048px). Values above your plan maximum are clamped down to your plan's maximum.
- `assets` (object, required): Provide the body and head images for head swap
  - `body_file_path` (string, required): Image that receives the swapped head. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `head_file_path` (string, required): Image of the head to place on the body. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### POST /v1/image-background-remover
`operationId: imageBackgroundRemover.createImage`

Image Background Remover


**Request Body:**
- `name` (string, optional) default=Background Remover - dateTime: Give your image a custom name for easy identification.
- `assets` (object, required): Provide the assets for background removal
  - `image_file_path` (string, required): The image to remove the background. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `background_image_file_path` (string, optional): The image used as the new background for the image_file_path. This image will be resized to match the image in image_file_path. Please make sure the resolution between the images are similar.

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

#### GET /v1/image-projects/{id}
`operationId: imageProjects.getDetails`

Get image details

**Path Parameters:**
- `id` (path, required): Unique ID of the image project. This value is returned by all of the POST APIs that create an image.

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `name` (string, required): The name of the image.
- `status` (string, required) enum=['draft', 'queued', 'rendering', 'complete', 'error', 'canceled']: The status of the image.
- `image_count` (integer, required): Number of images generated
- `type` (string, required): The type of the image project. Possible values are FACE_EDITOR, AI_IMAGE_EDITOR, AI_SELFIE, AI_HEADSHOT, AI_INFLUENCER, AI_IMAGE, AI_MEME, CLOTHES_CHANGER, BACKGROUND_REMOVER, FACE_SWAP, IMAGE_UPSCALER, IMAGE_ENHANCER,...
- `created_at` (string, required): 
- `enabled` (boolean, required): Whether this resource is active. If false, it is deleted.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.
- `downloads` (array, required): 
  items:
    - `url` (string, required): 
    - `expires_at` (string, required): 
- `error` (object, required): In the case of an error, this object will contain the error encountered during video render
  - `message` (string, required): Details on the reason why a failure happened.
  - `code` (string, required): An error code to indicate why a failure happened.

#### DELETE /v1/image-projects/{id}
`operationId: imageProjects.delete`

Delete image

**Path Parameters:**
- `id` (path, required): Unique ID of the image project. This value is returned by all of the POST APIs that create an image.

#### POST /v1/photo-colorizer
`operationId: photoColorizer.createImage`

Photo Colorizer


**Request Body:**
- `name` (string, optional) default=Photo Colorizer - dateTime: Give your image a custom name for easy identification.
- `assets` (object, required): Provide the assets for photo colorization
  - `image_file_path` (string, required): The image used to generate the colorized image. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made.

### Audio projects


#### POST /v1/ai-voice-cloner
`operationId: aiVoiceCloner.createAudio`

AI Voice Cloner


**Request Body:**
- `name` (string, optional) default=Voice Cloner - dateTime: Give your audio a custom name for easy identification.
- `assets` (object, required): Provide the assets for voice cloning.
  - `audio_file_path` (string, required): The audio used to clone the voice. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
- `style` (object, required): 
  - `prompt` (string, required): Text used to generate speech from the cloned voice. The character limit is 1000 characters.

**Response 200:**
- `id` (string, required): Unique ID of the audio. Use it with the [Get audio Project API](https://docs.magichour.ai/api-reference/audio-projects/get-audio-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the audio. We charge credits right when the request is made.

#### POST /v1/ai-voice-generator
`operationId: aiVoiceGenerator.createAudio`

AI Voice Generator


**Request Body:**
- `name` (string, optional) default=Voice Generator - dateTime: Give your audio a custom name for easy identification.
- `style` (object, required): The content used to generate speech.
  - `prompt` (string, required): Text used to generate speech. The character limit is 1000 characters.
  - `voice_name` (string, required) enum=[492 values, e.g. ['Elon Musk', 'Mark Zuckerberg', 'Joe Rogan', 'Barack Obama', 'Morgan Freeman', 'Kanye West'], ...]: The voice to use for the speech. Available voices: Elon Musk, Mark Zuckerberg, Joe Rogan, Barack Obama, Morgan Freeman, Kanye West, Donald Trump, Joe Biden, Kim Kardashian, Taylor Swift, James Earl Jones, Samuel L....

**Response 200:**
- `id` (string, required): Unique ID of the audio. Use it with the [Get audio Project API](https://docs.magichour.ai/api-reference/audio-projects/get-audio-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the audio. We charge credits right when the request is made.

#### GET /v1/audio-projects/{id}
`operationId: audioProjects.getDetails`

Get audio details

**Path Parameters:**
- `id` (path, required): Unique ID of the audio project. This value is returned by all of the POST APIs that create an audio.

**Response 200:**
- `id` (string, required): Unique ID of the audio. Use it with the [Get audio Project API](https://docs.magichour.ai/api-reference/audio-projects/get-audio-details) to fetch status and downloads.
- `name` (string, required): The name of the audio.
- `status` (string, required) enum=['draft', 'queued', 'rendering', 'complete', 'error', 'canceled']: The status of the audio.
- `type` (string, required): The type of the audio project. Possible values are VOICE_GENERATOR, VOICE_CHANGER, VOICE_CLONER, VIDEO_TO_AUDIO, MUSIC_GENERATOR
- `created_at` (string, required): 
- `enabled` (boolean, required): Whether this resource is active. If false, it is deleted.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the audio. We charge credits right when the request is made.
- `downloads` (array, required): 
  items:
    - `url` (string, required): 
    - `expires_at` (string, required): 
- `error` (object, required): In the case of an error, this object will contain the error encountered during video render
  - `message` (string, required): Details on the reason why a failure happened.
  - `code` (string, required): An error code to indicate why a failure happened.

#### DELETE /v1/audio-projects/{id}
`operationId: audioProjects.delete`

Delete audio

**Path Parameters:**
- `id` (path, required): Unique ID of the audio project. This value is returned by all of the POST APIs that create an audio.