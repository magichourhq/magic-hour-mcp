# Magic Hour API Reference (for MCP tool design)

Source: https://docs.magichour.ai/api-reference/openapi.json (fetched and saved as `docs/openapi.json`). Regenerate this file with `python docs/build_reference.py` if the spec changes.

## Authentication

- Every request requires `Authorization: Bearer <api_key>`.
- Get a key at https://magichour.ai/developer?tab=api-keys (Developer Hub → API Keys → Create key). Key is shown once.
- Base URL: `https://api.magichour.ai` (all paths below are relative to this, e.g. `/v1/ai-image-generator`).
- No API-level OAuth/session — it's a single static bearer token per account.
- **Mock server for dev/testing** (Python SDK): `Environment.MOCK_SERVER` (`https://api.sideko.dev/v1/mock/magichour/magic-hour/0.66.0`) — returns instant mock data, any string works as the token, zero credits consumed. Use this while building/testing the MCP server.

## Async job lifecycle (applies to every `create`/generation endpoint)

1. `POST /v1/<tool>` → returns immediately with `{id, credits_charged}`. Credits are charged at request time (refunded if the job later errors).
2. Poll `GET /v1/{image,video,audio}-projects/{id}` until `status` leaves `queued`/`rendering`.
3. Status enum: `draft | queued | rendering | complete | error | canceled`.
4. On `complete`, `downloads[]` is populated: `{url, expires_at}` — **download URLs expire after 24h**; re-call GET for fresh ones.
5. On `error`, the `error: {message, code}` object is populated and credits are refunded.
6. `DELETE /v1/{image,video,audio}-projects/{id}` permanently deletes rendered output (irreversible).

This means every category (image/video/audio) needs exactly one shared `get_details(id)` and one shared `delete(id)` tool — they're identical in shape across all ~28 "create" tools in that category.

## File inputs — 3 supported methods

Any `*_file_path` field in a request body accepts one of:
1. A direct public URL to the file.
2. A Magic Hour library reference (file from a prior generation/upload in the account).
3. A `file_path` obtained via the presigned-upload flow:
   - `POST /v1/files/upload-urls` with `{"items":[{"type":"video","extension":"mp4"}]}` → returns `{upload_url, expires_at, file_path}` per item.
   - `PUT` the raw file bytes to `upload_url`.
   - Use the returned `file_path` in the actual generation call.

**MCP design implication:** an LLM tool-call argument is JSON text, not binary — so an MCP tool generally can't accept raw file bytes directly. In practice tools should accept a URL string for `*_file_path` params; the presigned-upload flow (`/v1/files/upload-urls`) is only useful as an MCP tool if the *host application* uploads bytes out-of-band and just needs the MCP server to mint the URL, or if the calling agent already has a hosted URL for the asset. Worth confirming with the user whether raw upload needs to be in scope.

## Output delivery

- Downloads are URLs (not bytes) with `expires_at` ~24h out — same MCP constraint as above applies in reverse: tools should return the URL/metadata, not attempt to stream file bytes back through the tool result, unless the MCP host explicitly supports binary resources.

## Official Python SDK shape (`pip install magic_hour`)

Useful if the MCP server wraps the SDK instead of calling `httpx`/`requests` directly (matches "we just need to instantiate a client").

```python
from magic_hour import Client          # or AsyncClient
client = Client(token=API_KEY)         # or environment=Environment.MOCK_SERVER for testing
```

- `client.v1.<resource>.create(**params)` — 1:1 with each POST endpoint below. Returns immediately (`id`, `credits_charged`). Resource names are the snake_case form of the path, e.g. `client.v1.ai_image_generator.create(...)`, `client.v1.face_swap_photo.create(...)`.
- `client.v1.<resource>.generate(**params, wait_for_completion=True, download_outputs=True, download_directory=".")` — convenience wrapper: calls `create`, polls `check_result` internally (default poll interval 0.5s, override via `MAGIC_HOUR_POLL_INTERVAL` env var), and **downloads files to local disk**. ⚠️ Not directly reusable server-side as-is: "local disk" means the MCP server's disk, not the end caller's — would need `download_outputs=False` and return the URLs instead if exposed as an MCP tool.
- `client.v1.image_projects` / `.video_projects` / `.audio_projects` — each has `.get(id=...)`, `.delete(id=...)`, `.check_result(id=..., wait_for_completion, download_outputs, download_directory)`.
- `client.v1.files.upload_urls.create(...)`, `client.v1.face_detection.create(...)` / `.get(id=...)`.
- Both sync (`Client`) and async (`AsyncClient`) variants exist with identical method shapes — `AsyncClient` is the natural fit inside an async MCP server (e.g. FastMCP).

## Note: Magic Hour's own official MCP server is documentation-only

Magic Hour hosts `https://docs.magichour.ai/mcp` — an MCP server that lets coding assistants (Cursor/VS Code/Claude Code) pull accurate docs/code snippets while *writing integration code*. It does **not** execute API calls or expose action tools. It's unrelated to (and won't conflict with) the action-execution MCP server we're building, which lets an agent actually *call* the Magic Hour API at runtime. Worth knowing so nobody confuses the two.

## Webhooks (likely out of scope for v1)

Magic Hour supports webhooks (image/video/audio `completed`/`errored`/`started` events, HMAC-SHA256 signed payloads) for async completion notification. Not needed if the MCP tool design uses agent-driven polling (`get_details`), but flagging since it's an alternative to polling if tool-call latency becomes a problem for long video jobs.

## Full endpoint index

| Method | Path | Category | Summary |
|---|---|---|---|
| POST | `/v1/face-detection` | Files | Face Detection |
| GET | `/v1/face-detection/{id}` | Files | Get face detection details |
| POST | `/v1/files/upload-urls` | Files | Generate asset upload urls |
| POST | `/v1/ai-talking-photo` | Video Projects | AI Talking Photo |
| POST | `/v1/animation` | Video Projects | Animation |
| POST | `/v1/audio-to-video` | Video Projects | Audio-to-Video |
| POST | `/v1/auto-subtitle-generator` | Video Projects | Auto Subtitle Generator |
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

Detect faces in an image or video. Use this API to get the list of faces detected in the image or video to use in the [face swap photo](https://docs.magichour.ai/api-reference/image-projects/face-swap-photo) or [face swap video](https://docs.magichour.ai/api-reference/video-projects/face-swap-video) API calls for multi-face swaps.


**Request Body:**
- `confidence_score` (number, optional) default=0.5 range=[0,1]: Confidence threshold for filtering detected faces. * Higher values (e.g., 0.9) include only faces detected with high certainty, reducing false positives. * Lower values (e.g., 0.3) include more faces, but may increase...
- `assets` (object, required): Provide the assets for face detection
  - `target_file_path` (string, required): This is the image or video where the face will be detected. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): The id of the task. Use this value in the [get face detection details API](https://docs.magichour.ai/api-reference/files/get-face-detection-details) to get the details of the face detection task.
- `credits_charged` (integer, required): The credits charged for the task.

#### GET /v1/face-detection/{id}
`operationId: faceDetection.getDetails`

Get the details of a face detection task. 

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

Generates a list of pre-signed upload URLs for the assets required. This API is only necessary if you want to upload to Magic Hour's storage. Refer to the [Input Files Guide](/integration/input-files) for more details.


**Request Body:**
- `items` (array, required): The list of assets to upload. The response array will match the order of items in the request body.
  items:
    - `type` (string, required) enum=['video', 'audio', 'image']: The type of asset to upload. Possible types are video, audio, image
    - `extension` (string, required): The extension of the file to upload. Do not include the dot (.) before the extension. Possible extensions are...

**Response 200:**
- `items` (array, required): The list of upload URLs and file paths for the assets. The response array will match the order of items in the request body. Refer to the [Input Files Guide](/integration/input-files) for more details.
  items:
    - `upload_url` (string, required): Used to upload the file to storage, send a PUT request with the file as data to upload.
    - `expires_at` (string, required): when the upload url expires, and will need to request a new one.
    - `file_path` (string, required): this value is used in APIs that needs assets, such as image_file_path, video_file_path, and audio_file_path

### Video Projects


#### POST /v1/ai-talking-photo
`operationId: aiTalkingPhoto.createTalkingPhoto`

Create a talking photo from an image and audio or text input.


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

#### POST /v1/animation
`operationId: animation.createVideo`

Create a Animation video. The estimated frame cost is calculated based on the `fps` and `end_seconds` input.


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

**What this API does**


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

Automatically generate subtitles for your video in multiple languages.


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

#### POST /v1/face-swap
`operationId: faceSwap.createVideo`

**What this API does**


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

**What this API does**


**Request Body:**
- `name` (string, optional) default=Image To Video - dateTime: Give your video a custom name for easy identification.
- `end_seconds` (number, required) range=[1,60]: The total duration of the output video in seconds. Supported durations depend on the chosen model:
- `model` (string, optional) enum=[14 values, e.g. ['default', 'ltx-2', 'ltx-2.3', 'wan-2.2', 'seedance', 'seedance-2.0'], ...] default=default: The AI model to use for video generation.
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

**What this API does**


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

**What this API does**


**Request Body:**
- `name` (string, optional) default=Text To Video - dateTime: Give your video a custom name for easy identification.
- `end_seconds` (number, required) range=[1,60]: The total duration of the output video in seconds. Supported durations depend on the chosen model:
- `aspect_ratio` (string, optional) enum=['16:9', '9:16', '1:1']: Determines the aspect ratio of the output video.
- `resolution` (string, optional) enum=['480p', '720p', '1080p', '4k']: Controls the output video resolution. Defaults to `720p` on paid tiers and `480p` on free tiers.
- `model` (string, optional) enum=[14 values, e.g. ['default', 'ltx-2', 'ltx-2.3', 'wan-2.2', 'seedance', 'seedance-2.0'], ...] default=default: The AI model to use for video generation.
- `audio` (boolean, optional): Whether to include audio in the video. Defaults to `false` if not specified.
- `style` (object, required): 
  - `prompt` (string, required): The prompt used for the video.

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the video. If the status is not 'complete', this value is an estimate and may be adjusted upon completion based on the actual FPS of the output video. 

#### GET /v1/video-projects/{id}
`operationId: videoProjects.getDetails`

Check the progress of a video project. The `downloads` field is populated after a successful render. **Statuses** - `queued` — waiting to start - `rendering` — in progress - `complete` — ready; see `downloads` - `error` — a failure occurred (see `error`) - `canceled` — user canceled - `draft` — not used

**Path Parameters:**
- `id` (path, required): Unique ID of the video project. This value is returned by all of the POST APIs that create a video.

**Response 200:**
- `id` (string, required): Unique ID of the video. Use it with the [Get video Project API](https://docs.magichour.ai/api-reference/video-projects/get-video-details) to fetch status and downloads.
- `name` (string, required): The name of the video.
- `status` (string, required) enum=['draft', 'queued', 'rendering', 'complete', 'error', 'canceled']: The status of the video.
- `type` (string, required): The type of the video project. Possible values are ANIMATION, AUTO_SUBTITLE, VIDEO_TO_VIDEO, FACE_SWAP, TEXT_TO_VIDEO, IMAGE_TO_VIDEO, LIP_SYNC, TALKING_PHOTO, VIDEO_UPSCALER, EXTEND, AUDIO_TO_VIDEO, VIDEO_EXPANDER,...
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

Permanently delete the rendered video. This action is not reversible, please be sure before deleting.

**Path Parameters:**
- `id` (path, required): Unique ID of the video project. This value is returned by all of the POST APIs that create a video.

#### POST /v1/video-to-video
`operationId: videoToVideo.createVideo`

**What this API does**


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

### Image Projects


#### POST /v1/ai-clothes-changer
`operationId: aiClothesChanger.createImage`

Change outfits in photos in seconds with just a photo reference. Each photo costs 25 credits.


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

Edit facial features of an image using AI. Each edit costs 1 frame. The height/width of the output image depends on your subscription. Please refer to our [pricing](https://magichour.ai/pricing) page for more details


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

Create an AI GIF. Each GIF costs 50 credits.


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

Create an AI headshot. Each headshot costs 50 credits.


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

Edit images with AI.


**Request Body:**
- `name` (string, optional) default=Ai Image Editor - dateTime: Give your image a custom name for easy identification.
- `image_count` (number, optional) enum=[1, 4, 9, 16] default=1: Number of images to generate. Maximum varies by model. Defaults to 1 if not specified.
- `model` (string, optional) enum=[9 values, e.g. ['default', 'qwen-edit', 'flux-2-klein', 'nano-banana', 'nano-banana-2', 'seedream-v4'], ...]: The AI model to use for image editing. Each model has different capabilities and costs.
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

Create an AI image with advanced model selection and quality controls.


**Request Body:**
- `name` (string, optional) default=Ai Image - dateTime: Give your image a custom name for easy identification.
- `image_count` (integer, required) range=[1,16]: Number of images to generate. Maximum varies by model.
- `model` (string, optional) enum=[10 values, e.g. ['default', 'flux-schnell', 'flux-2-klein', 'z-image-turbo', 'seedream-v4', 'nano-banana'], ...]: The AI model to use for image generation. Each model has different capabilities and costs.
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

Upscale your image using AI. Each 2x upscale costs 50 credits, and 4x upscale costs 200 credits.


**Request Body:**
- `name` (string, optional) default=Image Upscaler - dateTime: Give your image a custom name for easy identification.
- `scale_factor` (number, required): How much to scale the image. Must be either 2 or 4. Note: 4x upscale is only available on Creator, Pro, or Business tier.
- `style` (object, required): Style settings for the upscale. Use `mode` to select between `"pro"` (faster, no enhancement required) and `"creative"` (defaults to `"Balanced"` enhancement). Defaults to `"creative"`.
  - `mode` (string, optional) enum=['pro', 'creative']: The upscaling mode. `"pro"` is faster and does not require `enhancement`. `"creative"` requires `enhancement`. Defaults to `"creative"`.
  - `enhancement` (string, optional) enum=['Resemblance', 'Balanced', 'Creative']: 
  - `prompt` (string, optional): A prompt to guide the final image. This value is ignored if `enhancement` is not Creative
- `assets` (object, required): Provide the assets for upscaling
  - `image_file_path` (string, required): The image to upscale. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls API](https://docs.magichour.ai/api-reference/files/generate-asset-upload-urls).

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made. 

#### POST /v1/ai-meme-generator
`operationId: aiMemeGenerator.createImage`

Create an AI generated meme. Each meme costs 10 credits.


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

Create an AI QR code. Each QR code costs 0 credits.


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

Swap a person into a scene image using Nano Banana 2. Credits depend on `resolution` (from 100 credits at 640px upward).


**Request Body:**
- `name` (string, optional) default=Body Swap - dateTime: Give your image a custom name for easy identification.
- `resolution` (string, required) enum=['640px', '1k', '2k', '4k']: Output resolution. Determines credits charged for the run.
- `assets` (object, required): Person image and scene image for body swap
  - `person_file_path` (string, required): Image of the person to place into the scene. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...
  - `scene_file_path` (string, required): Target scene image (background). This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made. 

#### POST /v1/face-swap-photo
`operationId: faceSwapPhoto.createImage`

Create a face swap photo. Each photo costs 10 credits. The height/width of the output image depends on your subscription. Please refer to our [pricing](https://magichour.ai/pricing) page for more details


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

Swap a head onto a body image. Each image costs 10 credits. Output resolution depends on your subscription; you may set `max_resolution` lower than your plan maximum if desired.


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

Remove background from image. Each image costs 5 credits.


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

Check the progress of a image project. The `downloads` field is populated after a successful render. **Statuses** - `queued` — waiting to start - `rendering` — in progress - `complete` — ready; see `downloads` - `error` — a failure occurred (see `error`) - `canceled` — user canceled - `draft` — not used

**Path Parameters:**
- `id` (path, required): Unique ID of the image project. This value is returned by all of the POST APIs that create an image.

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `name` (string, required): The name of the image.
- `status` (string, required) enum=['draft', 'queued', 'rendering', 'complete', 'error', 'canceled']: The status of the image.
- `image_count` (integer, required): Number of images generated
- `type` (string, required): The type of the image project. Possible values are FACE_EDITOR, AI_IMAGE_EDITOR, AI_SELFIE, AI_HEADSHOT, AI_IMAGE, AI_MEME, CLOTHES_CHANGER, BACKGROUND_REMOVER, FACE_SWAP, IMAGE_UPSCALER, AI_GIF, QR_CODE, PHOTO_EDITOR,...
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

Permanently delete the rendered image(s). This action is not reversible, please be sure before deleting.

**Path Parameters:**
- `id` (path, required): Unique ID of the image project. This value is returned by all of the POST APIs that create an image.

#### POST /v1/photo-colorizer
`operationId: photoColorizer.createImage`

Colorize image. Each image costs 10 credits.


**Request Body:**
- `name` (string, optional) default=Photo Colorizer - dateTime: Give your image a custom name for easy identification.
- `assets` (object, required): Provide the assets for photo colorization
  - `image_file_path` (string, required): The image used to generate the colorized image. This value is either - a direct URL to the video file - `file_path` field from the response of the [upload urls...

**Response 200:**
- `id` (string, required): Unique ID of the image. Use it with the [Get image Project API](https://docs.magichour.ai/api-reference/image-projects/get-image-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the image. We charge credits right when the request is made. 

### Audio Projects


#### POST /v1/ai-voice-cloner
`operationId: aiVoiceCloner.createAudio`

Clone a voice from an audio sample and generate speech. * Each character costs 0.05 credits. * The cost is rounded up to the nearest whole number


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

Generate speech from text. Each character costs 0.05 credits. The cost is rounded up to the nearest whole number.


**Request Body:**
- `name` (string, optional) default=Voice Generator - dateTime: Give your audio a custom name for easy identification.
- `style` (object, required): The content used to generate speech.
  - `prompt` (string, required): Text used to generate speech. The character limit is 1000 characters.
  - `voice_name` (string, required) enum=[494 values, e.g. ['Elon Musk', 'Mark Zuckerberg', 'Joe Rogan', 'Barack Obama', 'Morgan Freeman', 'Kanye West'], ...]: The voice to use for the speech. Available voices: Elon Musk, Mark Zuckerberg, Joe Rogan, Barack Obama, Morgan Freeman, Kanye West, Donald Trump, Joe Biden, Kim Kardashian, Taylor Swift, James Earl Jones, Samuel L....

**Response 200:**
- `id` (string, required): Unique ID of the audio. Use it with the [Get audio Project API](https://docs.magichour.ai/api-reference/audio-projects/get-audio-details) to fetch status and downloads.
- `credits_charged` (integer, required): The amount of credits deducted from your account to generate the audio. We charge credits right when the request is made. 

#### GET /v1/audio-projects/{id}
`operationId: audioProjects.getDetails`

Check the progress of a audio project. The `downloads` field is populated after a successful render. **Statuses** - `queued` — waiting to start - `rendering` — in progress - `complete` — ready; see `downloads` - `error` — a failure occurred (see `error`) - `canceled` — user canceled - `draft` — not used

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

Permanently delete the rendered audio file(s). This action is not reversible, please be sure before deleting.

**Path Parameters:**
- `id` (path, required): Unique ID of the audio project. This value is returned by all of the POST APIs that create an audio.