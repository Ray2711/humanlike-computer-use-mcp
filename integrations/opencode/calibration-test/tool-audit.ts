// Test-only opencode 2.x plugin for the calibration experiments. When the file named by CALIB_AUDIT_FLAG
// contains "on", the next model request is written to CALIB_AUDIT_OUT (tool names and descriptions, system
// prompt) and aborted before it reaches the provider, so no credits are used; the flag is then set to "off".

import { readFileSync, writeFileSync } from "node:fs"

type Api = { session: { hook(name: string, fn: (event: Record<string, unknown>) => Promise<void>): Promise<unknown> } }

function armed(flag: string): boolean {
  try {
    return readFileSync(flag, "utf8").trim() === "on"
  } catch {
    return false
  }
}

async function setup(api: Api): Promise<void> {
  const flag = process.env.CALIB_AUDIT_FLAG
  const out = process.env.CALIB_AUDIT_OUT
  if (!flag || !out) return
  await api.session.hook("context", async (event) => {
    if (!armed(flag)) return
    writeFileSync(flag, "off")
    const tools = (event.tools ?? {}) as Record<string, { description?: string }>
    const system = (event.system ?? []) as Array<{ text?: string }>
    const dump = {
      tools: Object.fromEntries(Object.entries(tools).map(([name, def]) => [name, def?.description ?? null])),
      system: system.map((s) => (typeof s === "string" ? s : (s?.text ?? ""))),
    }
    writeFileSync(out, JSON.stringify(dump, null, 2))
    throw new Error("calibration tool audit: request stopped before reaching the provider")
  })
}

export default { id: "calib-tool-audit", setup }
