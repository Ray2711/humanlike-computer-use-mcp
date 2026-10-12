// Run with: node --test integrations/opencode/keep-last-images.test.mjs  (Node >= 22.18 strips the TS types)
import assert from "node:assert/strict"
import { test } from "node:test"

const PLACEHOLDER = "[older screenshot removed to stay under the provider's image limit]"

// ---- opencode 2.x plugin (default export { id, setup }) ----

const mod = await import("./plugins/keep-last-images.ts")
const plugin = mod.default

// Stand-in for opencode's immutable LLM.Message schema class.
class Msg {
  constructor(fields) {
    Object.assign(this, fields)
    Object.freeze(this.content)
    Object.freeze(this)
  }
}

const shot = (n) => ({ type: "file", mime: "image/png", name: `shot${n}.png`, uri: `data:image/png;base64,${n}` })
const toolMsg = (id, value) =>
  new Msg({
    id,
    role: "tool",
    content: [{ type: "tool-result", id: `call-${id}`, name: "screenshot", result: { type: "content", value } }],
  })
const userMsg = (id, mediaType = "image/jpeg") =>
  new Msg({
    id,
    role: "user",
    content: [
      { type: "text", text: "look" },
      { type: "media", media: { mediaType, source: { type: "base64", data: "x" } }, filename: "a.jpg" },
    ],
  })
const images = (messages) =>
  messages.flatMap((m) =>
    m.content.flatMap((p) =>
      p.type === "media" && p.media.mediaType.startsWith("image/")
        ? [`media:${m.id}`]
        : p.type === "tool-result" && p.result.type === "content"
          ? p.result.value.filter((v) => v.type === "file" && v.mime.startsWith("image/")).map((v) => v.uri)
          : [],
    ),
  )

async function load(options) {
  const hooks = new Map()
  const api = {
    options,
    session: {
      hook: async (name, fn) => {
        hooks.set(name, fn)
        return { dispose() {} }
      },
    },
  }
  const result = await plugin.setup(api)
  return { hooks, result }
}

async function run(messages, options) {
  const { hooks } = await load(options)
  const event = { sessionID: "s", messages, system: [], options: {} }
  await hooks.get("context")(event)
  return event
}

test("default export is an opencode 2.x plugin definition and nothing else is exported", () => {
  assert.deepEqual(Object.keys(mod), ["default"])
  assert.equal(typeof plugin.id, "string")
  assert.equal(typeof plugin.setup, "function")
  assert.equal("effect" in plugin, false)
})

test("setup registers the request hooks and returns no cleanup", async () => {
  const { hooks, result } = await load()
  assert.deepEqual([...hooks.keys()], ["context", "compaction", "generate"])
  assert.equal(result, undefined)
})

test("keeps the newest N tool-result images and replaces older ones with text", async () => {
  delete process.env.OPENCODE_KEEP_LAST_IMAGES
  const messages = [1, 2, 3, 4, 5].map((i) => toolMsg(`m${i}`, [{ type: "text", text: `shot ${i}` }, shot(i)]))
  const original = [...messages]
  const event = await run(messages)
  assert.equal(event.messages, messages)
  assert.deepEqual(images(messages), [3, 4, 5].map((i) => shot(i).uri))
  assert.deepEqual(messages[0].content[0].result.value, [
    { type: "text", text: "shot 1" },
    { type: "text", text: PLACEHOLDER },
  ])
  assert.ok(messages[0] instanceof Msg)
  assert.notEqual(messages[0], original[0])
  assert.equal(messages[4], original[4])
  assert.equal(original[0].content[0].result.value[1].type, "file")
})

test("counts user media images and keeps text and non-image media", async () => {
  const pdf = new Msg({
    id: "p",
    role: "user",
    content: [{ type: "media", media: { mediaType: "application/pdf", source: { type: "base64", data: "p" } } }],
  })
  const messages = [userMsg("u1"), pdf, toolMsg("m2", [shot(2)]), toolMsg("m3", [shot(3)])]
  await run(messages, { keep: 2 })
  assert.deepEqual(messages[0].content, [
    { type: "text", text: "look" },
    { type: "text", text: PLACEHOLDER },
  ])
  assert.equal(messages[1], pdf)
  assert.equal(images(messages).length, 2)
})

test("several images in one result are trimmed oldest first", async () => {
  const messages = [toolMsg("m1", [shot("a"), shot("b"), shot("c")])]
  await run(messages, { keep: 2 })
  assert.deepEqual(images(messages), [shot("b").uri, shot("c").uri])
})

test("ignores string content, non-content tool results and missing messages", async () => {
  const messages = [
    new Msg({ id: "s", role: "user", content: "plain text" }),
    new Msg({ id: "j", role: "tool", content: [{ type: "tool-result", result: { type: "json", value: {} } }] }),
    toolMsg("m1", [shot(1)]),
  ]
  const before = [...messages]
  await run(messages, { keep: 1 })
  assert.deepEqual(messages, before)
  const { hooks } = await load()
  await hooks.get("context")({})
})

test("keep option, environment variable and fallback", async () => {
  const fresh = () => [toolMsg("m1", [shot(1)]), userMsg("u2"), toolMsg("m3", [shot(3)])]
  let messages = fresh()
  await run(messages, { keep: 0 })
  assert.equal(images(messages).length, 0)

  process.env.OPENCODE_KEEP_LAST_IMAGES = "1"
  messages = fresh()
  await run(messages)
  assert.deepEqual(images(messages), [shot(3).uri])

  messages = fresh()
  await run(messages, { keep: 2 })
  assert.equal(images(messages).length, 2)

  process.env.OPENCODE_KEEP_LAST_IMAGES = "x"
  messages = [1, 2, 3, 4].map((i) => toolMsg(`m${i}`, [shot(i)]))
  await run(messages, { keep: "5" })
  assert.equal(images(messages).length, 3)
  delete process.env.OPENCODE_KEEP_LAST_IMAGES
})

test("compaction and generate hooks prune too", async () => {
  const { hooks } = await load({ keep: 1 })
  for (const name of ["compaction", "generate"]) {
    const messages = [toolMsg("a", [shot(1)]), toolMsg("b", [shot(2)])]
    await hooks.get(name)({ messages })
    assert.deepEqual(images(messages), [shot(2).uri], name)
  }
})

// ---- opencode 1.x legacy plugin (experimental.chat.messages.transform) ----

const legacy = await import("./legacy-1.x/keep-last-images.ts")
const { KeepLastImages } = legacy
const { pruneImages, keepFromEnv } = KeepLastImages

const png = (n) => ({ mime: "image/png", url: `data:image/png;base64,${n}` })
const legacyTool = (id, attachments, extra = {}) => ({
  info: { id, role: "assistant" },
  parts: [
    {
      id: `${id}-p`,
      type: "tool",
      tool: "humanlike-computer-use_screenshot",
      state: { status: "completed", output: `shot ${id}`, attachments, time: { start: 1, end: 2, ...extra } },
    },
  ],
})
const legacyUser = (id) => ({
  info: { id, role: "user" },
  parts: [
    { id: `${id}-t`, sessionID: "s", messageID: id, type: "text", text: "look" },
    { id: `${id}-f`, sessionID: "s", messageID: id, type: "file", mime: "image/jpeg", url: "data:image/jpeg;base64,x" },
  ],
})
const legacyImages = (messages) =>
  messages.flatMap((m) =>
    m.parts.flatMap((p) =>
      p.type === "file" && p.mime.startsWith("image/")
        ? [p.url]
        : p.type === "tool" && !p.state.time?.compacted
          ? (p.state.attachments ?? []).filter((a) => a.mime.startsWith("image/")).map((a) => a.url)
          : [],
    ),
  )

test("legacy: module exports only plugin functions", () => {
  for (const [name, value] of Object.entries(legacy)) assert.equal(typeof value, "function", name)
  assert.equal(KeepLastImages.PLACEHOLDER, PLACEHOLDER)
})

test("legacy: keeps the newest N images and marks older tool results", () => {
  const messages = [1, 2, 3, 4, 5].map((i) => legacyTool(`m${i}`, [png(i)]))
  assert.equal(pruneImages(messages, 3), 2)
  assert.deepEqual(legacyImages(messages), [3, 4, 5].map((i) => png(i).url))
  assert.equal(messages[0].parts[0].state.output, `shot m1\n${PLACEHOLDER}`)
  assert.equal(messages[4].parts[0].state.output, "shot m5")
})

test("legacy: user images, non-image attachments and compacted parts", () => {
  const pdf = { mime: "application/pdf", url: "data:application/pdf;base64,p" }
  const messages = [legacyUser("u1"), legacyTool("m1", [pdf, png(1)]), legacyTool("m2", [png(2)], { compacted: 5 })]
  assert.equal(pruneImages(messages, 0), 2)
  assert.equal(messages[0].parts[1].text, PLACEHOLDER)
  assert.deepEqual(messages[1].parts[0].state.attachments, [pdf])
  assert.deepEqual(messages[2].parts[0].state.attachments, [png(2)])
  assert.equal(keepFromEnv(undefined), 3)
  assert.equal(keepFromEnv("-2"), 3)
  assert.equal(keepFromEnv("0"), 0)
})

test("legacy: hook mutates output.messages in place", async () => {
  delete process.env.OPENCODE_KEEP_LAST_IMAGES
  const hooks = await KeepLastImages({})
  const messages = [1, 2, 3, 4, 5, 6].map((i) => legacyTool(`m${i}`, [png(i)]))
  const output = { messages }
  await hooks["experimental.chat.messages.transform"]({}, output)
  assert.equal(output.messages, messages)
  assert.equal(legacyImages(messages).length, 3)
})
