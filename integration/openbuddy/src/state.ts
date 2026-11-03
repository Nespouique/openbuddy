export type State = "idle" | "thinking" | "working" | "permission" | "question" | "success" | "error" | "retry"
export type Event = { type: string; data: Record<string, any> }
export type Snapshot = {
  sessions: { id: string; busy: boolean }[]
  permissions: { id: string; sessionID: string }[]
  forms: { id: string; sessionID: string }[]
}
type Session = { busy: boolean; tools: Set<string>; reasoning: Set<string>; text: Set<string>; compacting: boolean; retryAt: number }
export type View = { state: State; label: string; pending_permissions: number; pending_forms: number; active_sessions: number }

export class Tracker {
  sessions = new Map<string, Session>()
  permissions = new Map<string, string>()
  forms = new Map<string, string>()
  terminal: { state: "success" | "error"; at: number } | undefined

  session(id: string): Session {
    let value = this.sessions.get(id)
    if (!value) {
      value = { busy: false, tools: new Set(), reasoning: new Set(), text: new Set(), compacting: false, retryAt: 0 }
      this.sessions.set(id, value)
    }
    return value
  }

  seed(snapshot: Snapshot) {
    for (const session of snapshot.sessions) this.session(session.id).busy = session.busy
    for (const request of snapshot.permissions) this.permissions.set(request.id, request.sessionID)
    for (const form of snapshot.forms) this.forms.set(form.id, form.sessionID)
  }

  apply(event: Event, now = Date.now()) {
    const data = event.data
    const id = typeof data.sessionID === "string" ? data.sessionID : undefined
    if (event.type === "permission.asked" && typeof data.id === "string" && id) {
      this.permissions.set(data.id, id)
      return
    }
    if (event.type === "permission.replied") { this.permissions.delete(data.requestID); return }
    if (event.type === "form.created") {
      const form = data.form
      if (form && typeof form.id === "string" && typeof form.sessionID === "string") this.forms.set(form.id, form.sessionID)
      return
    }
    if (event.type === "form.replied" || event.type === "form.cancelled") { this.forms.delete(data.id); return }
    if (!id) return
    if (event.type === "session.deleted") {
      this.sessions.delete(id)
      for (const [key, owner] of this.permissions) if (owner === id) this.permissions.delete(key)
      for (const [key, owner] of this.forms) if (owner === id) this.forms.delete(key)
      return
    }
    const session = this.session(id)
    const part = `${data.assistantMessageID ?? ""}:${data.ordinal ?? ""}`
    switch (event.type) {
      case "session.execution.started":
        session.busy = true
        this.terminal = undefined
        break
      case "session.execution.succeeded":
      case "session.execution.failed":
      case "session.execution.interrupted":
        session.busy = false
        session.tools.clear(); session.reasoning.clear(); session.text.clear()
        session.compacting = false; session.retryAt = 0
        if (event.type !== "session.execution.interrupted") {
          this.terminal = { state: event.type === "session.execution.failed" ? "error" : "success", at: now }
        }
        break
      case "session.step.started": session.busy = true; session.retryAt = 0; break
      case "session.tool.input.started":
      case "session.tool.called": session.busy = true; session.tools.add(String(data.id)); session.retryAt = 0; break
      case "session.tool.success":
      case "session.tool.failed": session.tools.delete(String(data.id)); break
      case "session.reasoning.started": session.busy = true; session.reasoning.add(part); session.retryAt = 0; break
      case "session.reasoning.ended": session.reasoning.delete(part); break
      case "session.text.started": session.busy = true; session.text.add(part); session.retryAt = 0; break
      case "session.text.ended": session.text.delete(part); break
      case "session.compaction.started": session.compacting = true; break
      case "session.compaction.ended":
      case "session.compaction.failed": session.compacting = false; break
      case "session.retry.scheduled":
        session.busy = true
        session.retryAt = typeof data.at === "number" ? data.at : now + 3000
        break
    }
  }

  view(now = Date.now()): View {
    const sessions = [...this.sessions.values()]
    const active = sessions.filter(session => session.busy || session.compacting)
    const counters = { pending_permissions: this.permissions.size, pending_forms: this.forms.size, active_sessions: active.length }
    const result = (state: State, label: string): View => ({ state, label, ...counters })
    if (this.permissions.size) return result("permission", `${this.permissions.size} approval(s) pending`)
    if (this.forms.size) return result("question", `${this.forms.size} response(s) pending`)
    if (active.some(session => session.tools.size)) return result("working", "Running tools")
    if (active.some(session => session.compacting)) return result("thinking", "Compacting context")
    if (active.some(session => session.text.size)) return result("working", "Writing response")
    if (active.some(session => session.retryAt > now)) return result("retry", "Retry scheduled")
    if (active.length) return result("thinking", active.length > 1 ? `${active.length} sessions active` : "OpenCode active")
    if (this.terminal && now - this.terminal.at < 8000) return result(this.terminal.state, this.terminal.state === "error" ? "Execution failed" : "Response ready")
    return result("idle", "OpenCode ready")
  }
}
