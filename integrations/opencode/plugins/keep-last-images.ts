// opencode 2.x plugin: keep only the newest N images in the request sent to the model.
//
// Providers such as abliteration.ai reject requests with more than 4 images, and opencode re-sends every
// earlier screenshot from the conversation. opencode rebuilds the model messages from the stored session for
// every request and passes them through the "context" / "compaction" / "generate" session hooks; this plugin
// replaces older images in that per-request copy with a short text note. The stored session is not changed.
//
// Install: copy this file to ~/.config/opencode/plugins/ (global) or <project>/.opencode/plugins/, then
// restart opencode. N defaults to 3; override with the environment variable OPENCODE_KEEP_LAST_IMAGES or a
// numeric `keep` plugin option.
//
// Requires opencode 2.x (checked against @opencode/cli 2.0.27): the loader only accepts a default export
// `{ id, setup }` or `{ id, effect }`. opencode 1.x needs integrations/opencode/legacy-1.x/keep-last-images.ts.
// Only erasable TypeScript syntax is used so the file also runs under plain `node` (see the test file).

const PLACEHOLDER = "[older screenshot removed to stay under the provider's image limit]"
const DEFAULT_KEEP = 3
const HOOKS = ["context", "compaction", "generate"]

type Part = {
  type?: string
  media?: { mediaType?: string }
  mime?: string
  result?: { type?: string; value?: unknown }
  [key: string]: unknown
}
type Message = { content?: unknown; [key: string]: unknown }
type RequestEvent = { messages?: Message[] }
type SetupApi = {
  options?: Record<string, unknown>
  session: { hook(name: string, fn: (event: RequestEvent) => void): Promise<unknown> }
}

const isImage = (mime: unknown): boolean => typeof mime === "string" && mime.toLowerCase().startsWith("image/")
const note = (): Part => ({ type: "text", text: PLACEHOLDER })

function keepFrom(option: unknown, env: string | undefined): number {
  if (typeof option === "number" && Number.isInteger(option) && option >= 0) return option
  const n = Number.parseInt(env ?? "", 10)
  return Number.isFinite(n) && n >= 0 ? n : DEFAULT_KEEP
}

// Messages are immutable LLM.Message class instances; opencode itself swaps them via new Message({...m, content}).
function withContent(message: Message, content: Part[]): Message {
  const Ctor = (message as { constructor?: unknown }).constructor
  if (typeof Ctor === "function" && Ctor !== Object) {
    return new (Ctor as new (fields: Message) => Message)({ ...message, content })
  }
  return { ...message, content }
}

function pruneImages(messages: Message[], keep: number): number {
  let seen = 0
  let removed = 0
  const over = (): boolean => {
    seen++
    if (seen <= keep) return false
    removed++
    return true
  }
  for (let m = messages.length - 1; m >= 0; m--) {
    const content = messages[m]?.content
    if (!Array.isArray(content)) continue
    let next: Part[] | undefined
    for (let p = content.length - 1; p >= 0; p--) {
      const part = content[p] as Part
      if (part?.type === "media" && isImage(part.media?.mediaType)) {
        if (over()) (next ??= [...content])[p] = note()
        continue
      }
      const result = part?.type === "tool-result" ? part.result : undefined
      if (result?.type !== "content" || !Array.isArray(result.value)) continue
      const value = result.value as Part[]
      let nextValue: Part[] | undefined
      for (let v = value.length - 1; v >= 0; v--) {
        if (value[v]?.type === "file" && isImage(value[v].mime) && over()) (nextValue ??= [...value])[v] = note()
      }
      if (nextValue) (next ??= [...content])[p] = { ...part, result: { ...result, value: nextValue } }
    }
    if (next) messages[m] = withContent(messages[m], next)
  }
  return removed
}

async function setup(api: SetupApi): Promise<void> {
  const keep = keepFrom(api.options?.keep, process.env.OPENCODE_KEEP_LAST_IMAGES)
  const prune = (event: RequestEvent): void => {
    if (Array.isArray(event?.messages)) pruneImages(event.messages, keep)
  }
  for (const name of HOOKS) await api.session.hook(name, prune)
}

export default { id: "keep-last-images", setup }
