import { readFile } from "node:fs/promises"
import { request } from "node:http"
import { homedir } from "node:os"
import { join } from "node:path"
import type { View } from "./state.ts"

export function discoveryPath(): string {
  const root = process.platform === "win32"
    ? process.env.LOCALAPPDATA ?? join(homedir(), "AppData", "Local")
    : process.env.XDG_CACHE_HOME ?? join(homedir(), ".cache")
  return join(root, "openbuddy", "bridge.json")
}

export async function publish(source: string, sequence: number, view: View, signal: AbortSignal) {
  const text = await readFile(discoveryPath(), "utf8")
  if (text.length > 1024) throw new Error("Invalid bridge discovery")
  const target = JSON.parse(text)
  if (target.product !== "openbuddy" || target.v !== 1 || !Number.isInteger(target.port)
      || target.port < 1024 || target.port > 65535 || !/^[a-f0-9]{64}$/.test(target.token)) {
    throw new Error("Invalid bridge discovery")
  }
  const body = JSON.stringify({ v: 1, source, sequence, ...view, label: view.label.slice(0, 24) })
  // Native loopback HTTP avoids environment-proxy handling by fetch implementations.
  await new Promise<void>((resolve, reject) => {
    const outgoing = request({
      hostname: "127.0.0.1", port: target.port, path: "/state", method: "POST", agent: false,
      headers: { "Authorization": `Bearer ${target.token}`, "Content-Type": "application/json",
                 "Content-Length": Buffer.byteLength(body) },
      signal: AbortSignal.any([signal, AbortSignal.timeout(1500)]),
    }, response => {
      response.on("error", reject)
      response.on("end", () => {
        if (response.statusCode === 200) resolve()
        else reject(new Error("Bridge rejected state"))
      })
      response.resume()
    })
    outgoing.on("error", reject)
    outgoing.end(body)
  })
}
