"""Floating always-on-top desktop pet window ('Clippy' mode) using stdlib tkinter.

Runs as a dedicated child process so Tcl/Tk always owns its main thread cleanly.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import sys
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser

from pc.pet_store import (BUILTIN_PET_ID, CHROMA_KEY_HEX, CHROMA_KEY_RGB,
                          read_clip_bytes, resolve_pet, rgb565_frame_to_ppm)

STATE_COLORS = {
    "offline": "#48546c",
    "idle": "#4884b4",
    "thinking": "#4674ff",
    "working": "#20dcd2",
    "permission": "#ff7818",
    "question": "#ffd228",
    "success": "#32e678",
    "error": "#ff373c",
    "retry": "#dc8c20",
    "pairing": "#aa5aff",
}

STATE_LABELS = {
    "offline": "OFFLINE",
    "idle": "READY",
    "thinking": "THINKING",
    "working": "WORKING",
    "permission": "PERMISSION",
    "question": "QUESTION",
    "success": "DONE",
    "error": "ERROR",
    "retry": "RETRY",
    "pairing": "PAIRING",
}

IDE_EXECUTABLES = frozenset({
    "code.exe",
    "code - insiders.exe",
    "cursor.exe",
    "windsurf.exe",
})

TERMINAL_EXECUTABLES = frozenset({
    "windowsterminal.exe",
    "wt.exe",
    "pwsh.exe",
    "powershell.exe",
    "cmd.exe",
    "conhost.exe",
    "wezterm-gui.exe",
    "alacritty.exe",
    "mintty.exe",
    "hyper.exe",
})

DRAG_THRESHOLD_PX = 4


def windows_desktop_name():
    """Return the current Win32 desktop name (usually 'Default' for interactive sessions)."""
    if sys.platform != "win32":
        return None
    try:
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        handle = u32.GetThreadDesktop(k32.GetCurrentThreadId())
        if not handle:
            return None
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if u32.GetUserObjectInformationW(handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
            return buf.value
    except Exception:
        return None
    return None


def windows_work_area():
    """Return (left, top, right, bottom) of the primary monitor work area (excluding taskbar)."""
    if sys.platform != "win32":
        return None
    try:
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None
    return None


def _ensure_topmost_windows(root):
    if sys.platform != "win32":
        return
    try:
        hwnd = root.winfo_id()
        # HWND_TOPMOST = -1, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE|SWP_SHOWWINDOW
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
    except Exception:
        pass


def _snapshot_windows_processes():
    """Return {pid: (parent_pid, exe_name)} via Win32 Toolhelp32."""
    if sys.platform != "win32":
        return {}

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    procs = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                procs[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        k32.CloseHandle(snap)
    return procs


def _enum_visible_windows():
    """Return visible top-level windows in Z-order as [(hwnd, pid, title)]."""
    if sys.platform != "win32":
        return []
    u32 = ctypes.windll.user32
    results = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not u32.IsWindowVisible(hwnd):
            return True
        length = u32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        u32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append((hwnd, pid.value, title))
        return True

    u32.EnumWindows(proc_type(callback), 0)
    return results


def select_opencode_window(windows, processes, self_pid=None):
    """Pick the best window (hwnd) hosting OpenCode, or fallback to VS Code / Terminal.

    `windows` is ordered top-to-bottom by Z-order: [(hwnd, pid, title)].
    `processes` maps pid -> (parent_pid, exe_name).
    """
    # Collect all ancestor PIDs of any running opencode* process.
    opencode_ancestors = set()
    for pid, (_ppid, exe) in processes.items():
        if "opencode" in (exe or "").lower():
            cur = pid
            for _ in range(16):
                if not cur or cur in opencode_ancestors:
                    break
                opencode_ancestors.add(cur)
                parent_info = processes.get(cur)
                if parent_info is None:
                    break
                cur = parent_info[0]

    best_hwnd = None
    best_score = -1

    for index, (hwnd, pid, title) in enumerate(windows):
        if self_pid is not None and pid == self_pid:
            continue
        title_lower = (title or "").lower()
        if "openbuddy" in title_lower and "visual studio code" not in title_lower:
            continue
        exe_lower = (processes.get(pid, (0, ""))[1] or "").lower()

        score = -1
        if pid in opencode_ancestors:
            score = 100
        elif "opencode" in title_lower:
            score = 85
        elif exe_lower in IDE_EXECUTABLES or "visual studio code" in title_lower:
            score = 75
        elif exe_lower in TERMINAL_EXECUTABLES:
            score = 70

        if score < 0:
            continue
        # Higher score wins; within the same tier, earlier Z-order (smaller index) wins.
        rank = (score, -index)
        if best_hwnd is None or rank > best_score:
            best_hwnd = hwnd
            best_score = rank

    return best_hwnd


def _activate_window_windows(hwnd):
    """Restore and bring a Win32 window reliably to the foreground."""
    if sys.platform != "win32" or not hwnd:
        return False
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    if u32.IsIconic(hwnd):
        u32.ShowWindow(hwnd, 9)  # SW_RESTORE
    else:
        u32.ShowWindow(hwnd, 5)  # SW_SHOW

    fg_hwnd = u32.GetForegroundWindow()
    cur_tid = k32.GetCurrentThreadId()
    fg_tid = u32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
    target_tid = u32.GetWindowThreadProcessId(hwnd, None)

    attached_fg = False
    attached_target = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached_fg = bool(u32.AttachThreadInput(cur_tid, fg_tid, True))
        if target_tid and target_tid != cur_tid and target_tid != fg_tid:
            attached_target = bool(u32.AttachThreadInput(cur_tid, target_tid, True))
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False
    finally:
        if attached_target:
            u32.AttachThreadInput(cur_tid, target_tid, False)
        if attached_fg:
            u32.AttachThreadInput(cur_tid, fg_tid, False)


def focus_opencode_window():
    """Bring the VS Code or terminal window hosting OpenCode to the foreground."""
    if sys.platform != "win32":
        return False
    try:
        procs = _snapshot_windows_processes()
        windows = _enum_visible_windows()
        target = select_opencode_window(windows, procs, self_pid=os.getpid())
        if target is None:
            return False
        return _activate_window_windows(target)
    except Exception:
        return False


class OverlayController:
    """Subprocess-backed controller for the floating Clippy pet window."""

    def __init__(self, get_snapshot=None, on_visibility_change=None, on_scale_change=None,
                 web_url_provider=None, pet_roots=None, desktop_checker=windows_desktop_name):
        self.get_snapshot = get_snapshot
        self.on_visibility_change = on_visibility_change
        self.on_scale_change = on_scale_change
        self.web_url_provider = web_url_provider
        self.pet_roots = pet_roots
        self.desktop_checker = desktop_checker
        self._lock = threading.Lock()
        self._proc = None
        self._error = None

    def _poll_proc_locked(self):
        if self._proc is not None and self._proc.poll() is not None:
            self._proc = None
            return False
        return self._proc is not None

    @property
    def visible(self):
        with self._lock:
            return self._poll_proc_locked()

    @property
    def error(self):
        with self._lock:
            return self._error

    def _desktop_isolated(self):
        desktop = self.desktop_checker() if self.desktop_checker is not None else None
        return bool(desktop and desktop.lower() != "default")

    def status(self):
        with self._lock:
            visible = self._poll_proc_locked()
            return {
                "visible": visible,
                "error": self._error,
                "desktop_isolated": self._desktop_isolated(),
            }

    def show(self):
        desktop = self.desktop_checker() if self.desktop_checker is not None else None
        if desktop and desktop.lower() != "default":
            with self._lock:
                self._error = (
                    "Le pont tourne sur un bureau isolé (%s) : lancez `python -m pc.opencode` "
                    "depuis votre terminal PowerShell pour afficher la fenêtre flottante." % desktop
                )
            return False

        web_url = self.web_url_provider() if self.web_url_provider is not None else None
        if not web_url:
            with self._lock:
                self._error = "Console web locale requise pour le pet flottant."
            return False

        with self._lock:
            if self._poll_proc_locked():
                return True
            self._error = None
            cmd = [sys.executable, "-m", "pc.pet_overlay", "--web-url", web_url.rstrip("/")]
            if self.pet_roots:
                for root in self.pet_roots:
                    cmd.extend(["--pet-root", str(root)])
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=creationflags,
                )
            except OSError as error:
                self._error = str(error)
                return False
            self._proc = proc

        # Briefly check that the Tk child did not exit immediately with an error.
        time.sleep(0.35)
        with self._lock:
            if self._proc is proc and proc.poll() is not None:
                err_text = ""
                try:
                    err_text = (proc.stderr.read() or b"").decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
                self._proc = None
                self._error = err_text.splitlines()[-1] if err_text else "Impossible d'ouvrir la fenêtre flottante."
                return False
        if self.on_visibility_change is not None:
            try:
                self.on_visibility_change(True)
            except Exception:
                pass
        return True

    def hide(self):
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self.on_visibility_change is not None:
            try:
                self.on_visibility_change(False)
            except Exception:
                pass
        return True

    def set_visible(self, visible):
        return self.show() if visible else self.hide()

    def close(self):
        self.hide()


def _http_client():
    return build_opener(ProxyHandler({}))


def _fetch_dashboard(opener, base_url):
    with opener.open(base_url + "/api/dashboard", timeout=1.5) as resp:
        return json.loads(resp.read())


def _post_json(opener, url, payload):
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=2.0) as resp:
        return json.loads(resp.read())


def run_overlay_app(web_url, pet_roots=None):
    import tkinter as tk

    base_url = web_url.rstrip("/")
    opener = _http_client()
    shared = {
        "snapshot": {
            "state": "idle",
            "pet": "auto",
            "overlay_scale": 1,
        },
        "stop": False,
        "bridge_lost": 0,
    }
    lock = threading.Lock()

    def poll_bridge():
        while not shared["stop"]:
            try:
                data = _fetch_dashboard(opener, base_url)
                bridge = data.get("bridge") or {}
                cfg = data.get("config") or {}
                eff = bridge.get("effective_state")
                state = eff.get("state") if isinstance(eff, dict) and eff.get("state") else "offline"
                snap = {
                    "state": state,
                    "pet": cfg.get("pet") or "auto",
                    "overlay_scale": 2 if cfg.get("overlay_scale") == 2 else 1,
                }
                with lock:
                    shared["snapshot"] = snap
                    shared["bridge_lost"] = 0
            except Exception:
                with lock:
                    shared["bridge_lost"] += 1
                    if shared["bridge_lost"] >= 15:
                        shared["stop"] = True
            time.sleep(0.35)

    poller = threading.Thread(target=poll_bridge, name="openbuddy-overlay-poll", daemon=True)
    poller.start()

    root = tk.Tk()
    root.title("OpenBuddy Pet")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    transparent = sys.platform == "win32"
    bg = CHROMA_KEY_HEX if transparent else "#0c121e"
    root.configure(bg=bg)
    if transparent:
        root.wm_attributes("-transparentcolor", CHROMA_KEY_HEX)

    canvas = tk.Canvas(root, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
    canvas.pack(fill="both", expand=True)

    drag = {"start_rx": 0, "start_ry": 0, "win_x": 0, "win_y": 0, "moved": False}
    cache = {
        "pet_selection": None,
        "pack": None,
        "scale": None,
        "clips": {},
        "clip": None,
        "frame_index": 0,
        "next_frame_at": 0.0,
        "layout_key": None,
        "window_w": 0,
        "window_h": 0,
        "position": None,
        "photo_ref": None,
        "topmost_tick": 0,
    }

    def close_app():
        shared["stop"] = True
        cache["clips"].clear()
        cache["photo_ref"] = None
        root.destroy()

    def trigger_focus():
        focus_opencode_window()
        root.after(40, lambda: _ensure_topmost_windows(root))

    def on_press(event):
        drag["start_rx"] = event.x_root
        drag["start_ry"] = event.y_root
        drag["win_x"] = root.winfo_x()
        drag["win_y"] = root.winfo_y()
        drag["moved"] = False

    def on_motion(event):
        dx = event.x_root - drag["start_rx"]
        dy = event.y_root - drag["start_ry"]
        if not drag["moved"] and abs(dx) < DRAG_THRESHOLD_PX and abs(dy) < DRAG_THRESHOLD_PX:
            return
        drag["moved"] = True
        nx = drag["win_x"] + dx
        ny = drag["win_y"] + dy
        root.geometry("+%d+%d" % (nx, ny))
        cache["position"] = (nx, ny)

    def on_release(_event):
        if not drag["moved"]:
            trigger_focus()

    def change_scale(new_scale):
        def worker():
            try:
                _post_json(opener, base_url + "/api/config", {"overlay_scale": new_scale})
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    menu = tk.Menu(root, tearoff=0, bg="#161f30", fg="#edf2ff",
                   activebackground="#3b5bdb", activeforeground="#ffffff")
    menu.add_command(label="Afficher OpenCode (VS Code / Terminal)", command=trigger_focus)
    menu.add_command(label="Ouvrir le panneau OpenBuddy", command=lambda: webbrowser.open(base_url + "/"))
    menu.add_separator()
    menu.add_command(label="Taille normale (1x)", command=lambda: change_scale(1))
    menu.add_command(label="Grande taille (2x)", command=lambda: change_scale(2))
    menu.add_separator()
    menu.add_command(label="Fermer", command=close_app)

    def show_menu(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_motion)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<Button-3>", show_menu)

    def ensure_pack(selection, scale):
        if selection == cache["pet_selection"] and scale == cache["scale"]:
            return
        _, pack, _ = resolve_pet(selection, pet_roots)
        cache["pet_selection"] = selection
        cache["pack"] = pack
        cache["scale"] = scale
        cache["clip"] = None
        cache["frame_index"] = 0
        cache["next_frame_at"] = 0.0

    def get_clip_frames(pack, clip_name, scale):
        key = (pack.pet_id, clip_name, scale)
        frames = cache["clips"].get(key)
        if frames is not None:
            return frames
        raw = read_clip_bytes(pack, clip_name)
        count = pack.animations[clip_name]["frames"]
        stride = pack.frame_bytes
        bg565 = pack.background_rgb565 if transparent else None
        images = []
        for idx in range(count):
            chunk = raw[idx * stride:(idx + 1) * stride]
            ppm = rgb565_frame_to_ppm(
                chunk, pack.width, pack.height,
                transparent_rgb565=bg565, chroma_key=CHROMA_KEY_RGB,
            )
            photo = tk.PhotoImage(data=ppm)
            if scale > 1:
                photo = photo.zoom(scale, scale)
            images.append(photo)
        cache["clips"][key] = images
        return images

    def draw_builtin_robot(cx, cy, accent, frame, state, scale):
        s = scale
        bob = (3 * s) if (frame % 2 and state in ("thinking", "working")) else 0
        x = cx - 33 * s
        y = cy - 28 * s + bob
        canvas.create_rectangle(x + 28 * s, y - 10 * s, x + 33 * s, y, fill=accent, outline="", tags="pet")
        canvas.create_rectangle(x + 5 * s, y, x + 61 * s, y + 48 * s, fill=accent, outline="", tags="pet")
        canvas.create_rectangle(x, y + 10 * s, x + 66 * s, y + 40 * s, fill=accent, outline="", tags="pet")
        canvas.create_rectangle(x + 10 * s, y + 8 * s, x + 56 * s, y + 36 * s, fill="#0c121e", outline="", tags="pet")
        eye_h = (2 if frame % 8 == 0 else 8) * s
        canvas.create_rectangle(x + 18 * s, y + 17 * s, x + 25 * s, y + 17 * s + eye_h, fill="#edf2ff", outline="", tags="pet")
        canvas.create_rectangle(x + 41 * s, y + 17 * s, x + 48 * s, y + 17 * s + eye_h, fill="#edf2ff", outline="", tags="pet")
        canvas.create_rectangle(x + 24 * s, y + 43 * s, x + 42 * s, y + 46 * s, fill="#0c121e", outline="", tags="pet")
        canvas.create_rectangle(x + 10 * s, y + 48 * s, x + 22 * s, y + 56 * s, fill=accent, outline="", tags="pet")
        canvas.create_rectangle(x + 44 * s, y + 48 * s, x + 56 * s, y + 56 * s, fill=accent, outline="", tags="pet")

    def draw_bubble(win_w, bubble_h, accent, state_text):
        canvas.delete("bubble")
        # Compact centered state-only speech pill, without close button.
        pill_w = min(win_w - 12, max(92, len(state_text) * 8 + 34))
        mid = win_w // 2
        bx1 = mid - pill_w // 2
        bx2 = mid + pill_w // 2
        by1 = 4
        by2 = bubble_h - 8
        canvas.create_rectangle(bx1, by1, bx2, by2, fill="#111827", outline=accent, width=2, tags="bubble")
        canvas.create_polygon(
            mid - 6, by2 - 1, mid + 6, by2 - 1, mid, by2 + 6,
            fill="#111827", outline=accent, width=2, tags="bubble",
        )
        canvas.create_line(mid - 5, by2 - 1, mid + 5, by2 - 1, fill="#111827", width=3, tags="bubble")
        cy = (by1 + by2) // 2
        canvas.create_oval(bx1 + 10, cy - 4, bx1 + 18, cy + 4, fill=accent, outline="", tags="bubble")
        canvas.create_text(
            bx1 + 24, cy, anchor="w", text=state_text,
            fill=accent, font=("Segoe UI", 9, "bold"), tags="bubble",
        )

    def initial_position(win_w, win_h):
        work = windows_work_area()
        if work is not None:
            left, top, right, bottom = work
            return (max(left + 16, right - win_w - 28), max(top + 16, bottom - win_h - 20))
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        return (max(20, sw - win_w - 28), max(20, sh - win_h - 72))

    def tick():
        if shared["stop"]:
            close_app()
            return
        now = time.monotonic()
        with lock:
            snap = dict(shared["snapshot"])
        state = snap.get("state") or "offline"
        if state not in STATE_COLORS:
            state = "offline"
        pet_sel = snap.get("pet") or "auto"
        scale = 2 if snap.get("overlay_scale") == 2 else 1

        ensure_pack(pet_sel, scale)
        pack = cache["pack"]
        pet_w = (pack.width if pack is not None else 96) * scale
        pet_h = (pack.height if pack is not None else 96) * scale
        bubble_h = 36
        win_w = max(136, pet_w + 16)
        win_h = bubble_h + pet_h + 4

        if win_w != cache["window_w"] or win_h != cache["window_h"]:
            cache["window_w"] = win_w
            cache["window_h"] = win_h
            canvas.configure(width=win_w, height=win_h)
            if cache["position"] is None:
                cache["position"] = initial_position(win_w, win_h)
            px, py = cache["position"]
            root.geometry("%dx%d+%d+%d" % (win_w, win_h, px, py))
            root.update_idletasks()
            root.lift()
            root.attributes("-topmost", True)
            _ensure_topmost_windows(root)
            cache["layout_key"] = None

        cache["topmost_tick"] += 1
        if cache["topmost_tick"] % 25 == 0:
            root.attributes("-topmost", True)
            _ensure_topmost_windows(root)

        accent = STATE_COLORS[state]
        state_text = STATE_LABELS[state]
        layout_key = (state, win_w, bubble_h)
        if layout_key != cache["layout_key"]:
            cache["layout_key"] = layout_key
            draw_bubble(win_w, bubble_h, accent, state_text)

        pet_cx = win_w // 2
        pet_cy = bubble_h + pet_h // 2

        if pack is not None:
            clip = pack.states.get(state, pack.states["idle"])
            if clip != cache["clip"]:
                cache["clip"] = clip
                cache["frame_index"] = 0
                cache["next_frame_at"] = 0.0
            if now >= cache["next_frame_at"]:
                try:
                    frames = get_clip_frames(pack, clip, scale)
                except (OSError, ValueError, KeyError):
                    frames = []
                if frames:
                    idx = cache["frame_index"] % len(frames)
                    photo = frames[idx]
                    cache["photo_ref"] = photo
                    canvas.delete("pet")
                    canvas.create_image(pet_cx, pet_cy, image=photo, tags="pet")
                    cache["frame_index"] = (idx + 1) % len(frames)
                    delay_ms = pack.animations[clip]["frame_ms"]
                    cache["next_frame_at"] = now + delay_ms / 1000.0
        else:
            frame_id = int(now * 1000) // 350
            if frame_id != cache["frame_index"] or cache["clip"] != ("builtin", state, scale):
                cache["clip"] = ("builtin", state, scale)
                cache["frame_index"] = frame_id
                canvas.delete("pet")
                draw_builtin_robot(pet_cx, pet_cy, accent, frame_id, state, scale)

        root.after(60, tick)

    tick()
    try:
        root.mainloop()
    finally:
        shared["stop"] = True
        cache["clips"].clear()
        cache["photo_ref"] = None


def main():
    parser = argparse.ArgumentParser(description="OpenBuddy floating desktop pet overlay")
    parser.add_argument("--web-url", required=True, help="Loopback URL of the OpenBuddy web console")
    parser.add_argument("--pet-root", action="append", help="Extra pet pack root directory")
    args = parser.parse_args()
    run_overlay_app(args.web_url, pet_roots=args.pet_root)


if __name__ == "__main__":
    main()
