# ChatGPT submission review — 2026-09-07

Status: **prepared draft, not submission-ready; not submitted, under review, approved, or published.**

## Existing work and evidence

Organization-wide GitHub code and open/closed PR searches found no submission file or explicit ChatGPT App / OpenAI Plugin / marketplace / directory submission. This is not proof that an inaccessible Platform organization has no prior draft. Existing OAuth, MCP App, CSP, and registry work exists and was reused. Open MCP PRs #52 (compatibility diagnostics) and #62 (README badge) were checked; neither is a submission implementation.

Production endpoint: `https://mcp.magichour.ai/` (the standalone `/mcp` URL returns 404).
Live `tools/list`: **43 tools**, all missing all three hints; **9 missing outputSchema**.
Source baseline: magic-hour-mcp `32a0ab9acbe8c108cd719c6962391c7db279e0a2`; API handler inspection: web-app `b75faf3e1b9a05532d5135498e280b8e9d54778e`, with no changes in inspected external API files. API deployment equivalence was not independently established.

Official sources:

- [Submission workflow](https://developers.openai.com/plugins/deploy/submission)
- [Review definitions](https://developers.openai.com/plugins/deploy/app-review)
- [Current import schema](https://developers.openai.com/plugins/schemas/chatgpt-app-submission.v1.json)
- [Official OpenAI submission skill](https://github.com/openai/openai-developers-for-cursor/blob/main/skills/chatgpt-app-submission/SKILL.md)

The older Apps SDK schema URL redirects to the current plugin schema, whose `$schema` constant uses `/plugins/`. The JSON uses that current value. No Postiz submission content was copied.

## Changes and annotation policy

Centralized runtime hints cover the reviewed API operation groups and all custom helpers. Unreviewed routes fail closed, even if tagged as project operations. Regression tests require explicit booleans for every exposed tool and exact agreement with the submission file.

All `readOnlyHint` values are **false** because the existing ToolCallLoggingMiddleware writes diagnostic logs for every invocation. OpenAI's current review definition explicitly includes log writes. This does not mean a status lookup starts a generation or charges credits; descriptions distinguish those behaviors. No logging was removed to obtain more favorable annotations.

All `openWorldHint` values are **false**: the reviewed operations act within private Magic Hour processing/storage, without public posting or external messaging. All `destructiveHint` values are **false**, except the three project deletion tools (**true**). Generation creates new projects and charges applicable credits, but does not overwrite source media. Failed generation refund paths exist in the shared creation helpers; no new refunds were tested or issued.

The upload handler calls getUploadFileUrl/getSignedUrl for new unique paths. Signing does not upload bytes, create a storage object, or enqueue generation. The MCP layer still logs the invocation. Face detection starts a private asynchronous task with zero credits in the inspected handler.

Three wait helpers now declare schemas matching their structured output fields, including normalized download URLs and timeout envelopes. The primary generation, edit, Face Swap, Talking Photo, Lip Sync, upload, and project-detail tools already declare output schemas. These declarations are not a substitute for validating real generation responses.

## Every exposed tool

Hints below are the proposed source values, **not deployed production values**. RO = readOnlyHint, OW = openWorldHint, D = destructiveHint. Every row had missing hints in production. Schema columns distinguish production and proposed source.

| Tool | RO / OW / D | Production schema | Proposed schema | Behavior |
|---|---|---|---|---|
| `ping` | false / false / false | yes | yes | Returns pong. |
| `wait_for_video_project` | false / false / false | missing | yes | Polls status, returns terminal/timeout data, optionally inlines media. |
| `wait_for_image_project` | false / false / false | missing | yes | Polls status, returns terminal/timeout data, optionally inlines media. |
| `wait_for_audio_project` | false / false / false | missing | yes | Polls status, returns terminal/timeout data, optionally inlines media. |
| `fetch_image_download` | false / false / false | missing | missing | Reads a signed Magic Hour media URL and returns binary MCP content. |
| `fetch_audio_download` | false / false / false | missing | missing | Reads a signed Magic Hour media URL and returns binary MCP content. |
| `fetch_video_download` | false / false / false | missing | missing | Reads a signed Magic Hour media URL and returns binary MCP content. |
| `video_assets_generate_presigned_url` | false / false / false | yes | yes | Signs expiring upload URLs; does not transfer bytes. |
| `face_detection_retrieve_details` | false / false / false | yes | yes | Retrieves task/project status and outputs. |
| `face_detection_detect_faces` | false / false / false | yes | yes | Starts a private zero-credit detection task. |
| `video_projects_retrieve_details` | false / false / false | yes | yes | Retrieves task/project status and outputs. |
| `video_projects_delete` | false / false / true | missing | missing | Permanently deletes rendered files and changes project records. |
| `ai_talking_photo_create_talking_photo` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_video_editor_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `animation_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `audio_to_video_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `auto_subtitle_generator_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `character_replace_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `face_swap_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `image_to_video_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `lip_sync_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `text_to_video_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `video_to_video_create_video` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `image_projects_retrieve_details` | false / false / false | yes | yes | Retrieves task/project status and outputs. |
| `image_projects_delete` | false / false / true | missing | missing | Permanently deletes rendered files and changes project records. |
| `ai_clothes_changer_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_face_editor_edit_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_gif_generator_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_image_editor_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_headshot_generator_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_image_generator_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_image_upscaler_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_meme_generator_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_qr_code_generator_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `body_swap_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `face_swap_photo_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `head_swap_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `image_background_remover_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `photo_colorizer_create_image` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `audio_projects_retrieve_details` | false / false / false | yes | yes | Retrieves task/project status and outputs. |
| `audio_projects_delete` | false / false / true | missing | missing | Permanently deletes rendered files and changes project records. |
| `ai_voice_generator_create_audio` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |
| `ai_voice_cloner_create_audio` | false / false / false | yes | yes | Creates a private media project and charges applicable credits. |

## Five positive review cases

| Case | Result |
|---|---|
| Product image | Fresh Codex session chose `ai_image_generator_create_image` once; client blocked the call before execution: “MCP tool call requires approval, but approval policy is never.” No completed image. |
| Edit uploaded image | Not run to generation; blocked test environment and missing reviewer fixture/upload-path proof. |
| Five-second Image-to-Video | Not run to generation; blocked test environment and missing reviewer fixture. |
| Two-input Face Swap Photo | Not run to generation; selected as a bounded two-image workflow, not proven more reliable than Lip Sync. Needs consenting/synthetic reviewer fixtures. |
| Image creation then animation | Not run to generation; blocked test environment. Expected two intended generation jobs, not one. |

Repeat runs of image/edit, Image-to-Video, and a multi-input workflow remain outstanding. Real generations: **0**. Magic Hour credits consumed: **0** (no generation call executed). Codex inference usage was not measured. The supplied Magic Hour key was not validated by a successful paid request.

The uploaded-input tests intentionally retain null attachment fields until stable, authorized public reviewer fixtures are chosen. Their schema validity does not make them executable. The current MCP only signs upload URLs; there is no hosted byte-upload bridge. A ChatGPT attachment must be shown to reach usable storage in the actual test environment; do not pretend signing a URL uploads it.

## Official negatives and additional boundary tests

Each prompt ran in a separate ephemeral Codex conversation against production MCP discovery. These are Codex tool-selection observations, not ChatGPT portal certification, and do not measure the undeployed metadata changes. Other connected capabilities were visible to the client. The calendar scenario attempted a calendar search, then reported missing calendar connection; it made no Magic Hour call and changed no meeting.

**Official negatives: 3/3 made no Magic Hour calls. Additional boundary prompts: 0/10 false-positive Magic Hour invocations.** All ten additional prompts test the non-execution side of the boundary; positive execution selection coverage remains incomplete. No paid ideation call occurred. This small sample is not a reliable estimate of the general false-positive rate.

| Case | Prompt | Magic Hour calls |
|---|---|---|
| official-negative-1 | Write three hooks for my video ad. Don't create any media. | 0 |
| official-negative-2 | Move tomorrow's team meeting to Friday. | 0 |
| official-negative-3 | What makes a good product video? | 0 |
| boundary-1 | Give me a prompt for an AI video. | 0 |
| boundary-2 | How would I remove a background from a photo? Explain the steps only. | 0 |
| boundary-3 | Give me ideas for a face swap video. | 0 |
| boundary-4 | Write instructions for syncing speech to a video; do not process media. | 0 |
| boundary-5 | Explain image-to-video versus text-to-video. | 0 |
| boundary-6 | Improve this image prompt: perfume on marble. Return only the prompt. | 0 |
| boundary-7 | Draft a five-second storyboard for a product video without making the video. | 0 |
| boundary-8 | What input files would I need to make a talking photo? | 0 |
| boundary-9 | Translate “create a cinematic product photo” into French. | 0 |
| boundary-10 | Review this idea: a bottle on black marble with warm lighting. Suggest improvements, but do not generate it. | 0 |

## Review findings and remaining gates

- **Six remaining outputSchema warnings:** `fetch_image_download`, `fetch_audio_download`, `fetch_video_download`, `video_projects_delete`, `image_projects_delete`, `audio_projects_delete`. Add an outputSchema so models can use these tools' results more reliably: [MCP tool specification](https://modelcontextprotocol.io/specification/draft/server/tools#tool). Fetch helpers currently return binary content, and deletes return empty HTTP 204 responses; declaring object schemas without corresponding structured output would be inaccurate. A structured envelope is a separate API behavior change, not included here.
- **Sensitive media:** face-detection/swap, head/body/face editing, talking-photo, lip-sync, and voice cloning process faces or voices. These inputs are functional requirements; use consenting or synthetic fixtures and verify privacy disclosures and reviewer expectations. No credential/MFA/payment/SSN fields were found in the exposed input schemas.
- **Input copy:** some inherited `*_file_path` descriptions say “video file” even for images/audio. The surrounding field and tool descriptions identify the medium, but this upstream copy should be corrected before final review.
- **CSP:** production resource metadata allows the canonical MCP origin, one concrete deployment asset origin, and the Magic Hour media origin; no wildcard was observed. Source explains the deployment asset origin. The portal's CSP/domain checks and actual ChatGPT widget rendering were not run, so this is not a CSP approval claim.
- **Production mismatch:** none of these source fixes is deployed. Production still lacks annotations. After deployment, scan tools again and compare actual descriptors with the import file.
- **Publisher access:** the hidden Platform portal exposes only Personal and EmbodyAI organizations. Magic Hour's publisher organization, verified identity, Apps Management write permission, prior submission state, and domain verification remain unverified. Do not create a Magic Hour submission under an unrelated publisher.
- **Execution blocker:** the fresh Codex client rejected the generation because approval policy is never. It was not bypassed or worked around. The remaining real/repeated tests need a correctly authorized execution surface.

## Validation

The JSON validates against the freshly downloaded official schema: 43 exact production names, 5 positive cases, 3 negative cases, explicit boolean hints, and nonempty justifications. It contains no API keys, private asset URLs, internal account IDs, or test execution logs.

26 focused Python tests pass, including annotations, unknown-route blocking, wait schemas for complete/error/canceled/timeout, primary workflow schemas, and submission/descriptor agreement. Full suite initially ran 58 tests with 1 failure and 2 errors; the unchanged baseline reproduces those same three issues across 54 tests: missing frontend build artifacts for two discovery/widget tests and a stale OAuth autocomplete assertion (`off` versus source `new-password`). The added submission consistency test brings the changed suite to 59 tests. No full green-suite claim. No heavy frontend build was run on this machine. `git diff --check` passes.

## Exact remaining submission steps

1. Finish review, merge through a feature-branch PR, deploy the MCP fixes, and re-read production tools/resources.
2. Open the [plugin portal](https://platform.openai.com/plugins) using the authorized Magic Hour publisher organization. Confirm verified identity and Apps Management write permission; inspect existing drafts first.
3. Create or update a **With MCP** draft, set **Universal** URL to `https://mcp.magichour.ai/`, configure OAuth/reviewer demo access, complete domain verification, and select **Scan Tools**.
4. Import `chatgpt-app-submission.json` if the current form offers import; otherwise populate its App Info, per-tool justifications, and Testing fields. Verify all server-provided hints match the file.
5. Supply logo, public website/support/privacy/terms links, verified identity, authorized availability, stable test attachments, and reviewer credentials. Finish actual widget/CSP checks.
6. Run all five positive cases, three official negatives, and the required repeated fresh-conversation workflows. Record job count, initial/final credits_charged, terminal status, downloadable output, and visual inspection. Fix failures and rerun without changing expected behavior to fit failures.
7. Review accurate policy attestations and release notes, then select **Submit for Review**. Claim submitted only after portal confirmation; after approval, publication is a separate portal action.
