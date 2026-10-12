// opencode 1.x plugin (legacy @opencode-ai/plugin hooks): keep only the newest N images in the request.
// For opencode 2.x use integrations/opencode/plugins/keep-last-images.ts instead; 2.x rejects this file with
// "Plugin must export a default definition with an id and an effect or setup function".
//
// Providers such as abliteration.ai reject requests with more than 4 images, and opencode re-sends every
// earlier screenshot from the conversation. This rewrites the in-memory copy of the history right before
// each model call (experimental.chat.messages.transform); the stored session is not changed.
//
// Install: copy this file to ~/.config/opencode/plugins/ (global) or <project>/.opencode/plugins/, then
// restart opencode. N defaults to 3; override with the environment variable OPENCODE_KEEP_LAST_IMAGES.
//
// opencode 1.x calls every export of a plugin file as a plugin, and a non-function export stops the whole
// file from loading, so the only export is the plugin function; helpers hang off it for the tests.
// Only erasable TypeScript syntax is used so the file also runs under plain `node` (see the test file).

const PLACEHOLDER = "[older screenshot removed to stay under the provider's image limit]"
const DEFAULT_KEEP = 3

type Attachment = { mime?: string; url?: string; [key: string]: unknown }
type AnyPart = {
  type?: string
  mime?: string
  id?: string
  sessionID?: string
  messageID?: string
  state?: {
    status?: string
    output?: string
    attachments?: Attachment[]
    time?: { compacted?: number }
  }
  [key: string]: unknown
}
type WithParts = { info?: unknown; parts?: AnyPart[] }

const isImage = (mime: unknown): boolean => typeof mime === "string" && mime.startsWith("image/")

function keepFromEnv(value: string | undefined): number {
  const n = Number.parseInt(value ?? "", 10)
  return Number.isFinite(n) && n >= 0 ? n : DEFAULT_KEEP
}

/** Remove all but the newest `keep` images from `messages` in place. Returns how many were removed. */
function pruneImages(messages: WithParts[], keep: number): number {
  let seen = 0
  let removed = 0
  for (let m = messages.length - 1; m >= 0; m--) {
    const parts = messages[m]?.parts
    if (!Array.isArray(parts)) continue
    for (let p = parts.length - 1; p >= 0; p--) {
      const part = parts[p]
      if (part?.type === "file" && isImage(part.mime)) {
        seen++
        if (seen > keep) {
          parts.splice(p, 1, {
            id: part.id,
            sessionID: part.sessionID,
            messageID: part.messageID,
            type: "text",
            text: PLACEHOLDER,
            synthetic: true,
          })
          removed++
        }
        continue
      }
      const state = part?.type === "tool" ? part.state : undefined
      if (!state || state.status !== "completed" || state.time?.compacted || !Array.isArray(state.attachments)) {
        continue
      }
      const attachments = state.attachments
      let dropped = 0
      for (let a = attachments.length - 1; a >= 0; a--) {
        if (!isImage(attachments[a]?.mime)) continue
        seen++
        if (seen > keep) {
          attachments.splice(a, 1)
          dropped++
        }
      }
      if (dropped > 0) {
        state.output = `${state.output ?? ""}\n${PLACEHOLDER}`.trimStart()
        removed += dropped
      }
    }
  }
  return removed
}

export const KeepLastImages = async () => {
  const keep = keepFromEnv(process.env.OPENCODE_KEEP_LAST_IMAGES)
  return {
    "experimental.chat.messages.transform": async (_input: unknown, output: { messages: WithParts[] }) => {
      if (Array.isArray(output?.messages)) pruneImages(output.messages, keep)
    },
  }
}

KeepLastImages.pruneImages = pruneImages
KeepLastImages.keepFromEnv = keepFromEnv
KeepLastImages.PLACEHOLDER = PLACEHOLDER
