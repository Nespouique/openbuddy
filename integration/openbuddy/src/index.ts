import type { Plugin } from "@opencode/plugin"
import { OpenCode } from "@opencode/client"
import { Service } from "@opencode/client/service"
import { randomUUID } from "node:crypto"
import { normalize } from "node:path"
import { Tracker, type Event, type Snapshot } from "./state.ts"
import { publish } from "./transport.ts"

const EVENTS = [
  "session.created", "session.deleted", "session.execution.started", "session.execution.succeeded",
  "session.execution.failed", "session.execution.interrupted", "session.step.started",
  "session.tool.input.started", "session.tool.called", "session.tool.success", "session.tool.failed",
  "session.reasoning.started", "session.reasoning.ended", "session.text.started", "session.text.ended",
  "session.compaction.started", "session.compaction.ended", "session.compaction.failed",
  "session.retry.scheduled", "permission.asked", "permission.replied",
  "form.created", "form.replied", "form.cancelled",
] as const

function directory(value: string) {
  const result = normalize(value).replace(/[\\/]+$/, "")
  return process.platform === "win32" ? result.toLowerCase() : result
}

export default {
  id: "openbuddy.events",
  setup(ctx) {
    const controller = new AbortController()
    const signal = controller.signal
    const source = randomUUID()
    const home = directory(ctx.location.directory)
    let tracker = new Tracker()
    let synchronized = false
    let replay: { event: Event; at: number }[] | undefined
    let replayOverflow = false
    let synchronizing = false
    let sending = false
    let lastSent = 0
    let lastView = ""
    let lastWarning = 0
    let sequence = 0

    async function synchronize() {
      if (synchronizing || signal.aborted) return
      synchronizing = true
      replay = []
      replayOverflow = false
      try {
        // Never bootstrap from a different server when running standalone/remotely.
        const endpoint = await Service.discover({ version: ctx.app.version })
        if (!endpoint) throw new Error("OpenCode service is unavailable")
        const client = OpenCode.make({ baseUrl: endpoint.url, headers: Service.headers(endpoint) })
        const request = { signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]) }
        const info = await client.server.info(request)
        if (info.pid !== process.pid) throw new Error("OpenBuddy requires the local managed OpenCode service")
        const sessions: Snapshot["sessions"] = []
        let cursor: string | undefined
        for (let pageIndex = 0; pageIndex < 20; pageIndex++) {
          const page = await ctx.session.list(cursor ? { cursor } : {
            directory: ctx.location.directory, limit: 100, archived: false,
          }, { signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]) })
          for (const session of page.data) {
            sessions.push({ id: session.id, busy: (session.time.busy ?? 0) > (session.time.idle ?? 0) })
          }
          cursor = page.cursor.next ?? undefined
          if (!cursor) break
        }
        if (cursor) throw new Error("Session snapshot exceeds the safety limit")
        const location = { directory: ctx.location.directory }
        const permissions = await client.permission.request.list({ location }, request)
        const forms = await client.form.list({ location }, request)
        if (!Array.isArray(permissions.data) || !Array.isArray(forms.data)) throw new Error("Invalid pending-request snapshot")
        const next = new Tracker()
        next.seed({ sessions, permissions: permissions.data, forms: forms.data })
        for (const [id, session] of next.sessions) {
          const current = tracker.sessions.get(id)
          if (session.busy && current?.busy) {
            session.tools = new Set(current.tools)
            session.reasoning = new Set(current.reasoning)
            session.text = new Set(current.text)
            session.compacting = current.compacting
            session.retryAt = current.retryAt
          }
        }
        for (const item of replay ?? []) next.apply(item.event, item.at)
        if (replayOverflow) throw new Error("Too many events during synchronization")
        // Preserve the short terminal display across periodic snapshots.
        next.terminal ??= tracker.terminal
        tracker = next
        synchronized = true
      } catch {
        if (!signal.aborted && Date.now() - lastWarning > 60_000) {
          console.warn("OpenBuddy: state synchronization unavailable; retrying (no session content logged)")
          lastWarning = Date.now()
        }
      } finally {
        replay = undefined
        synchronizing = false
      }
    }

    const events = (async () => {
      while (!signal.aborted) {
        try {
          for await (const event of ctx.event.subscribe({ types: [...EVENTS], signal })) {
            const data = event.data as Record<string, any>
            const location = event.location?.directory ?? data.location?.directory
            const sessionID = data.sessionID ?? data.form?.sessionID
            if (location ? directory(location) !== home : !tracker.sessions.has(sessionID)) continue
            // Project only identifiers and counters, never prompt/tool/form payloads.
            const minimal: Event = { type: event.type, data: {
              sessionID: data.sessionID, id: data.id, requestID: data.requestID,
              assistantMessageID: data.assistantMessageID, ordinal: data.ordinal, at: data.at,
              form: data.form ? { id: data.form.id, sessionID: data.form.sessionID } : undefined,
            } }
            const at = Date.now()
            tracker.apply(minimal, at)
            if (replay) {
              if (replay.length < 2048) replay.push({ event: minimal, at })
              else replayOverflow = true
            }
          }
        } catch { /* Re-subscribe after a live-only stream failure. */ }
        if (!signal.aborted) {
          synchronized = false
          void synchronize()
          await new Promise(resolve => setTimeout(resolve, 1000))
        }
      }
    })()

    const sender = setInterval(() => {
      if (sending || !synchronized || signal.aborted) return
      const view = tracker.view()
      const serialized = JSON.stringify(view)
      if (serialized === lastView && Date.now() - lastSent < 3000) return
      sending = true
      void publish(source, ++sequence, view, signal).then(() => {
        lastSent = Date.now(); lastView = serialized
      }).catch(() => {
        // The companion must never break OpenCode when Python/Bluetooth is absent.
        lastSent = Date.now(); lastView = serialized
      }).finally(() => { sending = false })
    }, 250)
    const resync = setInterval(() => { void synchronize() }, 15_000)
    void synchronize()
    return async () => {
      controller.abort()
      clearInterval(sender)
      clearInterval(resync)
      await events
    }
  },
} satisfies Plugin.Plugin
