import { test } from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, dirname } from "node:path"
import { discoveryPath, publish } from "../src/transport.ts"

test("authenticated state publication is direct loopback HTTP even with a proxy configured", async () => {
  const root = await mkdtemp(join(tmpdir(), "openbuddy-transport-"))
  const old = { LOCALAPPDATA: process.env.LOCALAPPDATA, XDG_CACHE_HOME: process.env.XDG_CACHE_HOME, HTTP_PROXY: process.env.HTTP_PROXY }
  process.env.LOCALAPPDATA = root
  process.env.XDG_CACHE_HOME = root
  process.env.HTTP_PROXY = "http://127.0.0.1:1"
  let received: any
  const token = "a".repeat(64)
  const server = createServer((request, response) => {
    assert.equal(request.headers.authorization, `Bearer ${token}`)
    assert.equal(request.headers["transfer-encoding"], undefined)
    let body = ""
    request.setEncoding("utf8")
    request.on("data", chunk => { body += chunk })
    request.on("end", () => {
      received = JSON.parse(body)
      response.end('{"accepted":true}')
    })
  })
  try {
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve))
    const address = server.address()
    assert.ok(address && typeof address !== "string")
    const discovery = discoveryPath()
    await mkdir(dirname(discovery), { recursive: true })
    await writeFile(discovery, JSON.stringify({ v: 1, product: "openbuddy", port: address.port, token }))
    await publish("test-source", 1, { state: "question", label: "Response pending",
      pending_permissions: 0, pending_forms: 1, active_sessions: 1 }, new AbortController().signal)
    assert.equal(received.state, "question")
    assert.equal(received.sequence, 1)
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()))
    for (const [name, value] of Object.entries(old)) {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
    await rm(root, { recursive: true, force: true })
  }
})
