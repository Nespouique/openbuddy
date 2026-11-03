import { test } from "node:test"
import assert from "node:assert/strict"
import { Tracker } from "../src/state.ts"

function send(tracker: Tracker, type: string, data: Record<string, any> = {}, now = 1000) {
  tracker.apply({ type, data: { sessionID: "s1", ...data } }, now)
}

test("permission requests are tracked independently and dominate activity", () => {
  const tracker = new Tracker()
  send(tracker, "session.execution.started")
  send(tracker, "permission.asked", { id: "p1" })
  send(tracker, "permission.asked", { id: "p2" })
  send(tracker, "permission.replied", { requestID: "p1" })
  assert.equal(tracker.view().pending_permissions, 1)
  assert.equal(tracker.view().state, "permission")
  send(tracker, "permission.replied", { requestID: "p2" })
  assert.equal(tracker.view().state, "thinking")
})

test("forms use the actual v2 nested creation payload", () => {
  const tracker = new Tracker()
  send(tracker, "form.created", { form: { id: "f1", sessionID: "s1" } })
  send(tracker, "form.created", { form: { id: "f2", sessionID: "s1" } })
  send(tracker, "form.cancelled", { id: "f1" })
  assert.equal(tracker.view().state, "question")
  assert.equal(tracker.view().pending_forms, 1)
  send(tracker, "form.replied", { id: "f2" })
  assert.equal(tracker.view().state, "idle")
})

test("completion of one session does not hide another active session", () => {
  const tracker = new Tracker()
  send(tracker, "session.execution.started")
  send(tracker, "session.execution.started", { sessionID: "s2" })
  send(tracker, "session.execution.succeeded")
  assert.equal(tracker.view(1100).state, "thinking")
  assert.equal(tracker.view(1100).active_sessions, 1)
})

test("parallel tools do not finish when only one tool completes", () => {
  const tracker = new Tracker()
  send(tracker, "session.tool.input.started", { id: "t1" })
  send(tracker, "session.tool.called", { id: "t2" })
  send(tracker, "session.tool.success", { id: "t1" })
  assert.equal(tracker.view().state, "working")
  send(tracker, "session.tool.failed", { id: "t2" })
  assert.equal(tracker.view().state, "thinking")
})

test("only execution failure produces a terminal error, not a failed tool", () => {
  const tracker = new Tracker()
  send(tracker, "session.execution.started")
  send(tracker, "session.tool.failed", { id: "t1" })
  assert.notEqual(tracker.view().state, "error")
  send(tracker, "session.execution.failed")
  assert.equal(tracker.view(1100).state, "error")
  assert.equal(tracker.view(9100).state, "idle")
})

test("snapshot hydration restores busy sessions and preexisting requests", () => {
  const tracker = new Tracker()
  tracker.seed({ sessions: [{ id: "s1", busy: true }],
    permissions: [{ id: "p1", sessionID: "s1" }], forms: [{ id: "f1", sessionID: "s1" }] })
  assert.equal(tracker.view().state, "permission")
  send(tracker, "permission.replied", { requestID: "p1" })
  assert.equal(tracker.view().state, "question")
})

test("deleted sessions remove only their own waiting requests", () => {
  const tracker = new Tracker()
  send(tracker, "permission.asked", { id: "p1" })
  send(tracker, "permission.asked", { id: "p2", sessionID: "s2" })
  send(tracker, "session.deleted")
  assert.equal(tracker.view().pending_permissions, 1)
})

test("retry expires at the scheduled timestamp and resumes on new activity", () => {
  const tracker = new Tracker()
  send(tracker, "session.retry.scheduled", { at: 4000 })
  assert.equal(tracker.view(2000).state, "retry")
  assert.equal(tracker.view(5000).state, "thinking")
  send(tracker, "session.text.started", { ordinal: 1 })
  assert.equal(tracker.view(2000).state, "working")
})

test("success is transient and interruption is not success", () => {
  const tracker = new Tracker()
  send(tracker, "session.execution.succeeded")
  assert.equal(tracker.view(2000).state, "success")
  assert.equal(tracker.view(10000).state, "idle")
  send(tracker, "session.execution.started")
  send(tracker, "session.execution.interrupted")
  assert.equal(tracker.view().state, "idle")
})

test("view cannot leak tool inputs, responses, question content or errors", () => {
  const tracker = new Tracker()
  send(tracker, "session.tool.called", { id: "t1", input: { secret: "sensitive" } })
  send(tracker, "session.tool.success", { id: "t1", content: [{ text: "sensitive" }] })
  send(tracker, "form.created", { form: { id: "f1", sessionID: "s1", title: "sensitive" } })
  assert.equal(JSON.stringify(tracker.view()).includes("sensitive"), false)
})
