"""Loopback web administration console and virtual pet viewer for the OpenBuddy bridge."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from c6lcd.buddy_protocol import STATES, VERSION, validate_command
from pc.pet_store import read_clip_bytes, safe_name

DEFAULT_WEB_PORT = 8765


PAGE = r"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenBuddy — Console & Pet</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090d16;
      --bg-elev: #111827;
      --bg-card: #141c2d;
      --bg-input: #0b101b;
      --border: #253147;
      --border-soft: #1d273b;
      --text: #eef3ff;
      --muted: #93a3bf;
      --accent: #4674ff;
      --accent-rgb: 70, 116, 255;
      --ok: #32e678;
      --warn: #ff7818;
      --err: #ff4757;
      font: 14px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(900px 420px at 15% -10%, rgba(var(--accent-rgb), 0.16), transparent 65%),
        radial-gradient(700px 360px at 85% 0%, rgba(32, 220, 210, 0.08), transparent 60%),
        var(--bg);
      color: var(--text);
      transition: background 0.35s ease;
    }
    .shell {
      max-width: 1120px;
      margin: 0 auto;
      padding: 22px 20px 42px;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 16px 20px;
      background: rgba(20, 28, 45, 0.85);
      border: 1px solid var(--border);
      border-radius: 18px;
      backdrop-filter: blur(10px);
      margin-bottom: 20px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-dot {
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 14px rgba(var(--accent-rgb), 0.9);
      flex-shrink: 0;
    }
    .brand h1 {
      margin: 0;
      font-size: 19px;
      font-weight: 700;
      letter-spacing: 0.01em;
    }
    .brand small {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .header-actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 6px 11px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--bg-input);
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    .pill .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #64748b;
    }
    .pill.online { color: #d8f8e6; border-color: rgba(50, 230, 120, 0.4); }
    .pill.online .dot { background: var(--ok); box-shadow: 0 0 8px var(--ok); }
    .pill.virtual { color: #d7e4ff; border-color: rgba(70, 116, 255, 0.45); }
    .pill.virtual .dot { background: #60a5fa; }
    .pill.warn { color: #ffe2cc; border-color: rgba(255, 120, 24, 0.45); }
    .pill.warn .dot { background: var(--warn); }

    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border: 1px solid var(--border);
      border-radius: 11px;
      padding: 9px 14px;
      background: #1b253b;
      color: var(--text);
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.08s ease, background 0.15s ease, border-color 0.15s ease, box-shadow 0.2s ease;
    }
    .btn:hover { background: #24314d; border-color: #3a4c70; }
    .btn:active { transform: translateY(1px); }
    .btn:disabled { opacity: 0.5; cursor: wait; }
    .btn-primary {
      background: linear-gradient(135deg, #4f6dff, #3b55e6);
      border-color: rgba(126, 152, 255, 0.55);
      color: #fff;
      box-shadow: 0 6px 18px rgba(70, 116, 255, 0.28);
    }
    .btn-primary:hover { filter: brightness(1.08); background: linear-gradient(135deg, #5975ff, #425cf0); }
    .btn-clippy.active {
      background: linear-gradient(135deg, #16a34a, #0f766e);
      border-color: rgba(110, 231, 183, 0.65);
      box-shadow: 0 0 22px rgba(50, 230, 120, 0.35);
    }
    .btn-subtle {
      padding: 6px 11px;
      font-size: 12px;
      border-radius: 9px;
      background: var(--bg-input);
    }
    .btn-warn {
      background: rgba(255, 120, 24, 0.16);
      border-color: rgba(255, 120, 24, 0.5);
      color: #ffd3b3;
    }

    .grid {
      display: grid;
      grid-template-columns: 1.25fr 0.95fr;
      gap: 20px;
      align-items: start;
    }
    @media (max-width: 920px) {
      .grid { grid-template-columns: 1fr; }
    }
    .card {
      background: rgba(20, 28, 45, 0.92);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 20px;
      margin-bottom: 20px;
    }
    .card-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 14px;
    }
    .card h2 {
      margin: 0;
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0.01em;
    }
    .muted { color: var(--muted); font-size: 13px; }

    /* Virtual ESP32 screen & Pet stage */
    .stage-wrap {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 14px;
      padding: 16px 10px;
      border-radius: 16px;
      background: #070b12;
      border: 1px solid var(--border-soft);
      position: relative;
      overflow: hidden;
    }
    .bezel {
      position: relative;
      padding: 12px;
      border-radius: 18px;
      background: linear-gradient(160deg, #1b2232, #0d121c);
      border: 2px solid rgba(var(--accent-rgb), 0.65);
      box-shadow:
        0 0 38px rgba(var(--accent-rgb), 0.32),
        inset 0 0 18px rgba(var(--accent-rgb), 0.14);
      transition: border-color 0.3s ease, box-shadow 0.3s ease;
      max-width: 100%;
    }
    .bezel.breathing {
      animation: haloPulse 2.4s ease-in-out infinite;
    }
    @keyframes haloPulse {
      0%, 100% { box-shadow: 0 0 24px rgba(var(--accent-rgb), 0.25), inset 0 0 12px rgba(var(--accent-rgb), 0.12); }
      50% { box-shadow: 0 0 46px rgba(var(--accent-rgb), 0.52), inset 0 0 20px rgba(var(--accent-rgb), 0.2); }
    }
    #buddyCanvas {
      display: block;
      width: 100%;
      max-width: 544px;
      height: auto;
      border-radius: 10px;
      image-rendering: pixelated;
      background: #080c14;
    }
    .stage-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      gap: 10px;
      padding: 0 6px;
    }
    .seg {
      display: inline-flex;
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 3px;
      gap: 3px;
    }
    .seg button {
      border: 0;
      background: transparent;
      color: var(--muted);
      padding: 5px 10px;
      border-radius: 7px;
      font: inherit;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
    }
    .seg button.active {
      background: #22304d;
      color: var(--text);
    }

    /* Manual state test grid */
    .inputs-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-bottom: 12px;
    }
    @media (max-width: 560px) {
      .inputs-row { grid-template-columns: 1fr; }
    }
    .field label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 5px;
      font-weight: 600;
    }
    input[type="text"], input[type="number"], select {
      width: 100%;
      padding: 9px 11px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: var(--bg-input);
      color: var(--text);
      font: inherit;
      outline: none;
    }
    input:focus, select:focus {
      border-color: #5b7cff;
      box-shadow: 0 0 0 3px rgba(91, 124, 255, 0.18);
    }
    .states-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    @media (max-width: 560px) {
      .states-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    .state-btn {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 10px;
      border-radius: 11px;
      border: 1px solid var(--border);
      background: #101726;
      color: var(--text);
      font: inherit;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: border-color 0.15s, background 0.15s, transform 0.08s;
    }
    .state-btn:hover { background: #192338; border-color: var(--state-color); }
    .state-btn.active {
      border-color: var(--state-color);
      background: rgba(255, 255, 255, 0.05);
      box-shadow: inset 0 0 0 1px var(--state-color);
    }
    .state-swatch {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--state-color);
      box-shadow: 0 0 8px var(--state-color);
      flex-shrink: 0;
    }

    /* Admin configuration form */
    .form-stack {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .toggle-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 11px 13px;
      border-radius: 12px;
      border: 1px solid var(--border-soft);
      background: #0e1523;
    }
    .toggle-row strong { display: block; font-size: 13px; }
    .toggle-row span { display: block; font-size: 12px; color: var(--muted); }
    .toggle-controls {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
    }
    .switch {
      position: relative;
      display: inline-block;
      width: 44px;
      height: 24px;
    }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider {
      position: absolute;
      inset: 0;
      border-radius: 999px;
      background: #27334d;
      cursor: pointer;
      transition: background 0.2s;
    }
    .slider::before {
      content: "";
      position: absolute;
      width: 18px;
      height: 18px;
      left: 3px;
      top: 3px;
      border-radius: 50%;
      background: #eef3ff;
      transition: transform 0.2s;
    }
    .switch input:checked + .slider { background: #3b63ff; }
    .switch input:checked + .slider::before { transform: translateX(20px); }

    .inline-row {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .inline-row > *:first-child { flex: 1; }
    .kv-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .kv {
      padding: 9px 11px;
      border-radius: 10px;
      background: var(--bg-input);
      border: 1px solid var(--border-soft);
    }
    .kv small { display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
    .kv strong { font-size: 13px; font-weight: 600; word-break: break-word; }

    .toast {
      position: fixed;
      right: 20px;
      bottom: 20px;
      max-width: 380px;
      padding: 11px 15px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: #131c2e;
      color: var(--text);
      font-size: 13px;
      font-weight: 600;
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.45);
      opacity: 0;
      transform: translateY(8px);
      pointer-events: none;
      transition: opacity 0.2s, transform 0.2s;
      z-index: 99;
    }
    .toast.show { opacity: 1; transform: translateY(0); }
    .toast.ok { border-color: rgba(50, 230, 120, 0.55); color: #bdf7d2; }
    .toast.error { border-color: rgba(255, 71, 87, 0.6); color: #ffb3ba; }
    .hidden { display: none !important; }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand">
        <div class="brand-dot" id="brandDot"></div>
        <div>
          <h1>OpenBuddy Studio</h1>
          <small>Administration du pont OpenCode, aperçu du pet &amp; compagnon flottant</small>
        </div>
      </div>
      <div class="header-actions">
        <span class="pill" id="pillSources"><span class="dot"></span><span id="pillSourcesText">OpenCode : 0</span></span>
        <span class="pill" id="pillDevice"><span class="dot"></span><span id="pillDeviceText">Matériel…</span></span>
        <span class="pill hidden" id="pillQuota"><span id="pillQuotaText"></span></span>
        <button class="btn btn-primary btn-clippy" id="clippyBtn" title="Afficher le pet dans une fenêtre flottante toujours visible au-dessus des autres applications">
          <span id="clippyBtnLabel">Keep the pet visible</span>
        </button>
      </div>
    </header>

    <div class="grid">
      <div>
        <section class="card">
          <div class="card-head">
            <div>
              <h2>Aperçu du compagnon</h2>
              <span class="muted" id="petSubtitle">Synchronisé en temps réel avec le pont</span>
            </div>
            <div class="inline-row" style="gap:6px;">
              <span class="pill" id="modeBadge"><span class="dot"></span><span id="modeBadgeText">LIVE</span></span>
              <button class="btn btn-warn btn-subtle hidden" id="clearManualBtn">Revenir en mode Live</button>
            </div>
          </div>

          <div class="stage-wrap">
            <div class="bezel breathing" id="bezel">
              <canvas id="buddyCanvas" width="320" height="172"></canvas>
            </div>
            <div class="stage-bar">
              <div class="seg" role="group" aria-label="Mode d'aperçu">
                <button id="viewScreenBtn" class="active" type="button">Écran 320×172</button>
                <button id="viewPetBtn" type="button">Pet seul</button>
              </div>
              <div class="inline-row" style="gap:6px;">
                <span class="muted" style="font-size:12px;">Pet flottant :</span>
                <div class="seg" role="group" aria-label="Taille du pet flottant">
                  <button id="scale1Btn" type="button">1×</button>
                  <button id="scale2Btn" type="button">2×</button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-head">
            <div>
              <h2>Tester un état manuellement</h2>
              <span class="muted">Envoie l'état à l'ESP32 (si branché), à l'aperçu web et au pet flottant</span>
            </div>
          </div>
          <div class="inputs-row">
            <div class="field">
              <label for="manualLabel">Libellé facultatif (24 car. max)</label>
              <input id="manualLabel" type="text" maxlength="24" placeholder="Test manuel">
            </div>
            <div class="field">
              <label for="manualInfo">Ligne info / quota facultative (24 car. max)</label>
              <input id="manualInfo" type="text" maxlength="24" placeholder="Ex. 12 / 200">
            </div>
          </div>
          <div class="states-grid" id="statesGrid"></div>
        </section>
      </div>

      <div>
        <section class="card">
          <div class="card-head">
            <div>
              <h2>Configuration du bridge</h2>
              <span class="muted">Appliquée à chaud et enregistrée pour les prochains lancements</span>
            </div>
          </div>
          <form id="configForm" class="form-stack">
            <div class="field">
              <label for="cfgTransport">Mode de sortie matériel</label>
              <select id="cfgTransport">
                <option value="usb">USB Série (ESP32 branché en USB-C)</option>
                <option value="ble">Bluetooth LE (ESP32 appairé en BLE)</option>
                <option value="none">Sans ESP32 (pet virtuel &amp; notifications PC uniquement)</option>
              </select>
            </div>

            <div class="field" id="usbPortRow">
              <label for="cfgSerialPort">Port série USB</label>
              <div class="inline-row">
                <select id="cfgSerialPort">
                  <option value="auto">auto (détection Espressif)</option>
                </select>
                <button type="button" class="btn btn-subtle" id="refreshPortsBtn">Scanner</button>
              </div>
            </div>

            <div class="field hidden" id="bleAddressRow">
              <label for="cfgBleAddress">Adresse ou nom Bluetooth (facultatif)</label>
              <input id="cfgBleAddress" type="text" maxlength="64" placeholder="Laisser vide pour recherche auto">
            </div>

            <div class="field">
              <label for="cfgPet">Pet affiché (web &amp; fenêtre flottante)</label>
              <select id="cfgPet"></select>
            </div>

            <div class="toggle-row">
              <div>
                <strong>Sons système Windows</strong>
                <span>Alertes sonores sur permission, question, succès et erreur</span>
              </div>
              <div class="toggle-controls">
                <button type="button" class="btn btn-subtle" id="testSoundBtn">Tester</button>
                <label class="switch"><input type="checkbox" id="cfgSounds"><span class="slider"></span></label>
              </div>
            </div>

            <div class="toggle-row">
              <div>
                <strong>Notifications Windows (toasts)</strong>
                <span>Bannières natives sans contenu privé</span>
              </div>
              <div class="toggle-controls">
                <button type="button" class="btn btn-subtle" id="testNotifBtn">Tester</button>
                <label class="switch"><input type="checkbox" id="cfgNotifs"><span class="slider"></span></label>
              </div>
            </div>

            <div class="toggle-row">
              <div>
                <strong>Quota fournisseur</strong>
                <span id="quotaDetail">Lit %APPDATA%\openbuddy\quota.json</span>
              </div>
              <div class="toggle-controls">
                <button type="button" class="btn btn-subtle" id="refreshQuotaBtn">Actualiser</button>
                <label class="switch"><input type="checkbox" id="cfgQuota"><span class="slider"></span></label>
              </div>
            </div>

            <div class="inline-row" style="justify-content:flex-end;">
              <button type="submit" class="btn btn-primary" id="saveConfigBtn">Enregistrer et appliquer</button>
            </div>
          </form>
        </section>

        <section class="card">
          <div class="card-head">
            <h2>Diagnostic matériel &amp; pont</h2>
            <button type="button" class="btn btn-subtle" id="refreshDeviceBtn">Ping ESP32</button>
          </div>
          <div class="kv-grid" id="diagGrid"></div>
        </section>
      </div>
    </div>
  </div>

  <div class="toast" id="toast" role="status" aria-live="polite"></div>

  <script>
    const STATE_META = {
      offline:    { title: 'OFFLINE',    fr: 'Hors ligne',   rgb: [72, 84, 108] },
      idle:       { title: 'READY',      fr: 'Prêt',         rgb: [72, 132, 180] },
      thinking:   { title: 'THINKING',   fr: 'Réflexion',    rgb: [70, 116, 255] },
      working:    { title: 'WORKING',    fr: 'En cours',     rgb: [32, 220, 210] },
      permission: { title: 'PERMISSION', fr: 'Autorisation', rgb: [255, 120, 24] },
      question:   { title: 'QUESTION',   fr: 'Question',     rgb: [255, 210, 40] },
      success:    { title: 'DONE',       fr: 'Succès',       rgb: [50, 230, 120] },
      error:      { title: 'ERROR',      fr: 'Erreur',       rgb: [255, 55, 60] },
      retry:      { title: 'RETRY',      fr: 'Nouvel essai', rgb: [220, 140, 32] },
    };
    const TEST_STATES = ['idle', 'thinking', 'working', 'permission', 'question', 'success', 'error', 'retry'];

    const app = {
      dashboard: null,
      viewMode: 'screen',
      formDirty: false,
      ports: [],
      clipCache: new Map(), // "petId:clip" -> { bytes, width, height, frames, frameMs, bg565, decoded: [] }
      clipLoading: new Set(),
      anim: { clipKey: null, frame: 0, nextAt: 0 },
      pipWindow: null,
      pipCanvas: null,
      pipCtx: null,
    };

    const canvas = document.getElementById('buddyCanvas');
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;

    const statesGrid = document.getElementById('statesGrid');
    for (const st of TEST_STATES) {
      const meta = STATE_META[st];
      const color = `rgb(${meta.rgb.join(',')})`;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'state-btn';
      btn.dataset.state = st;
      btn.style.setProperty('--state-color', color);
      btn.innerHTML = `<span class="state-swatch"></span><span>${meta.fr}</span>`;
      btn.addEventListener('click', () => sendManualState(st));
      statesGrid.appendChild(btn);
    }

    let toastTimer = null;
    function showToast(text, kind = 'ok') {
      const el = document.getElementById('toast');
      el.textContent = text;
      el.className = `toast show ${kind}`;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
    }

    async function api(url, options = {}) {
      const resp = await fetch(url, options);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || `Erreur HTTP ${resp.status}`);
      return data;
    }

    async function apiPost(url, body = {}) {
      return api(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    }

    function applyThemeColor(state) {
      const meta = STATE_META[state] || STATE_META.offline;
      const rgbStr = meta.rgb.join(', ');
      document.documentElement.style.setProperty('--accent', `rgb(${rgbStr})`);
      document.documentElement.style.setProperty('--accent-rgb', rgbStr);
    }

    function updateTransportRows() {
      const mode = document.getElementById('cfgTransport').value;
      document.getElementById('usbPortRow').classList.toggle('hidden', mode !== 'usb');
      document.getElementById('bleAddressRow').classList.toggle('hidden', mode !== 'ble');
    }

    function populatePorts(selectedPort) {
      const sel = document.getElementById('cfgSerialPort');
      const current = selectedPort || sel.value || 'auto';
      sel.innerHTML = '';
      const autoOpt = document.createElement('option');
      autoOpt.value = 'auto';
      autoOpt.textContent = 'auto (détection Espressif)';
      sel.appendChild(autoOpt);
      const seen = new Set(['auto']);
      for (const p of app.ports) {
        const opt = document.createElement('option');
        opt.value = p.device;
        opt.textContent = `${p.device} — ${p.description}${p.espressif ? ' ★ Espressif' : ''}`;
        sel.appendChild(opt);
        seen.add(p.device);
      }
      if (current && !seen.has(current)) {
        const opt = document.createElement('option');
        opt.value = current;
        opt.textContent = `${current} (configuré)`;
        sel.appendChild(opt);
      }
      sel.value = current || 'auto';
    }

    function populatePetSelect(petInfo, selectedValue) {
      const sel = document.getElementById('cfgPet');
      const prev = selectedValue || sel.value || 'auto';
      sel.innerHTML = '';
      const autoOpt = document.createElement('option');
      autoOpt.value = 'auto';
      autoOpt.textContent = 'Automatique (premier pack local, sinon robot)';
      sel.appendChild(autoOpt);
      for (const item of (petInfo.available || [])) {
        const opt = document.createElement('option');
        opt.value = item.id;
        opt.textContent = item.builtin
          ? 'Robot intégré (sans pack)'
          : `${item.id}${item.author ? ' — par ' + item.author : ''}`;
        sel.appendChild(opt);
      }
      sel.value = prev;
    }

    function syncFormFromConfig(cfg, petInfo) {
      if (app.formDirty) return;
      document.getElementById('cfgTransport').value = cfg.transport || 'usb';
      populatePorts(cfg.serial_port || 'auto');
      document.getElementById('cfgBleAddress').value = cfg.ble_address || '';
      populatePetSelect(petInfo, cfg.pet || 'auto');
      document.getElementById('cfgSounds').checked = !!cfg.sounds_enabled;
      document.getElementById('cfgNotifs').checked = !!cfg.notifications_enabled;
      document.getElementById('cfgQuota').checked = !!cfg.quota_enabled;
      updateTransportRows();
    }

    function renderDashboard(data) {
      app.dashboard = data;
      const b = data.bridge;
      const cfg = data.config;
      const ov = data.overlay || {};
      const eff = b.effective_state;
      const activeState = eff ? eff.state : 'offline';

      applyThemeColor(activeState);

      // Header pills
      const srcPill = document.getElementById('pillSources');
      srcPill.className = 'pill ' + (b.sources > 0 ? 'online' : '');
      document.getElementById('pillSourcesText').textContent = `OpenCode : ${b.sources} source${b.sources > 1 ? 's' : ''}`;

      const devPill = document.getElementById('pillDevice');
      if (cfg.transport === 'none') {
        devPill.className = 'pill virtual';
        document.getElementById('pillDeviceText').textContent = 'Mode virtuel (sans ESP32)';
      } else if (b.usb_connected) {
        devPill.className = 'pill online';
        document.getElementById('pillDeviceText').textContent = `ESP32 USB (${b.serial_port_active || 'connecté'})`;
      } else if (b.ble_connected) {
        devPill.className = 'pill online';
        document.getElementById('pillDeviceText').textContent = 'ESP32 Bluetooth connecté';
      } else {
        devPill.className = 'pill warn';
        document.getElementById('pillDeviceText').textContent = cfg.transport === 'usb' ? 'ESP32 USB en attente…' : 'ESP32 BLE en attente…';
      }

      const qPill = document.getElementById('pillQuota');
      if (b.quota) {
        qPill.classList.remove('hidden');
        document.getElementById('pillQuotaText').textContent = `Quota : ${b.quota}`;
      } else {
        qPill.classList.add('hidden');
      }

      // Clippy button & scale
      const pipOpen = !!(app.pipWindow && !app.pipWindow.closed);
      const anyFloating = !!(ov.visible || pipOpen);
      const clippyBtn = document.getElementById('clippyBtn');
      clippyBtn.classList.toggle('active', anyFloating);
      document.getElementById('clippyBtnLabel').textContent = anyFloating
        ? 'Pet flottant visible (masquer)'
        : 'Keep the pet visible';
      document.getElementById('scale1Btn').classList.toggle('active', cfg.overlay_scale !== 2);
      document.getElementById('scale2Btn').classList.toggle('active', cfg.overlay_scale === 2);

      // Live vs Manual badge
      const modeBadge = document.getElementById('modeBadge');
      const clearBtn = document.getElementById('clearManualBtn');
      if (b.manual_mode) {
        modeBadge.className = 'pill warn';
        document.getElementById('modeBadgeText').textContent = 'TEST MANUEL';
        clearBtn.classList.remove('hidden');
      } else {
        modeBadge.className = 'pill ' + (b.sources > 0 ? 'online' : '');
        document.getElementById('modeBadgeText').textContent = 'LIVE OPENCODE';
        clearBtn.classList.add('hidden');
      }

      // Active pet subtitle
      const petName = data.pet.builtin ? 'Robot intégré' : data.pet.id;
      document.getElementById('petSubtitle').textContent = `Pet actif : ${petName} · État : ${(STATE_META[activeState] || STATE_META.offline).title}`;

      // Highlight state buttons
      document.querySelectorAll('.state-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.state === activeState);
      });

      // Quota detail text
      const qDetail = document.getElementById('quotaDetail');
      if (!cfg.quota_enabled) {
        qDetail.textContent = 'Désactivé dans la configuration';
      } else if (b.quota_error) {
        qDetail.textContent = `Erreur : ${b.quota_error}`;
      } else if (b.quota) {
        qDetail.textContent = `Actuel : ${b.quota}`;
      } else {
        qDetail.textContent = 'Aucun quota configuré (%APPDATA%\\openbuddy\\quota.json)';
      }

      // Diagnostics grid
      const devStatus = b.device_status || {};
      const items = [
        ['Transport actif', cfg.transport === 'none' ? 'Virtuel (aucun)' : cfg.transport.toUpperCase()],
        ['Firmware ESP32', devStatus.firmware || '—'],
        ['Carte SD / Pet ESP32', devStatus.sd === true ? (devStatus.pet || 'SD OK (robot)') : (devStatus.sd === false ? 'Absente' : '—')],
        ['Dernier état envoyé', b.last_delivered ? `${b.last_delivered.state} (${b.last_delivered.label || '—'})` : 'Aucun'],
        ['Pont OpenCode', `127.0.0.1:${b.bridge_port}`],
        ['Console Web', `127.0.0.1:${b.web_port}`],
      ];
      document.getElementById('diagGrid').innerHTML = items
        .map(([k, v]) => `<div class="kv"><small>${escapeHtml(k)}</small><strong>${escapeHtml(String(v))}</strong></div>`)
        .join('');

      syncFormFromConfig(cfg, data.pet);
      ensureActiveClipLoaded();
    }

    function escapeHtml(text) {
      return text.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    }

    function ensureActiveClipLoaded() {
      const data = app.dashboard;
      if (!data || !data.pet || data.pet.builtin) return;
      const pet = data.pet;
      const state = data.bridge.effective_state ? data.bridge.effective_state.state : 'offline';
      const clipName = (pet.states && (pet.states[state] || pet.states.idle)) || 'idle';
      const key = `${pet.id}:${clipName}`;
      if (app.clipCache.has(key) || app.clipLoading.has(key)) return;
      const animMeta = pet.animations && pet.animations[clipName];
      if (!animMeta) return;
      app.clipLoading.add(key);
      fetch(`/api/pet/${encodeURIComponent(pet.id)}/clip/${encodeURIComponent(clipName)}`)
        .then(r => { if (!r.ok) throw new Error('clip load failed'); return r.arrayBuffer(); })
        .then(buf => {
          const bytes = new Uint8Array(buf);
          const decoded = [];
          const pixels = pet.width * pet.height;
          for (let f = 0; f < animMeta.frames; f++) {
            const off = document.createElement('canvas');
            off.width = pet.width;
            off.height = pet.height;
            const octx = off.getContext('2d');
            const img = octx.createImageData(pet.width, pet.height);
            const out = img.data;
            const start = f * pixels * 2;
            for (let i = 0; i < pixels; i++) {
              const val = (bytes[start + i * 2] << 8) | bytes[start + i * 2 + 1];
              const o = i * 4;
              const r5 = val >> 11;
              const g6 = (val >> 5) & 63;
              const b5 = val & 31;
              out[o]     = (r5 << 3) | (r5 >> 2);
              out[o + 1] = (g6 << 2) | (g6 >> 4);
              out[o + 2] = (b5 << 3) | (b5 >> 2);
              out[o + 3] = 255;
            }
            octx.putImageData(img, 0, 0);
            decoded.push(off);
          }
          app.clipCache.set(key, {
            width: pet.width,
            height: pet.height,
            frames: animMeta.frames,
            frameMs: animMeta.frame_ms,
            decoded,
          });
        })
        .catch(() => {})
        .finally(() => app.clipLoading.delete(key));
    }

    function drawRobot(targetCtx, ox, oy, accent, frame, state, scale = 1) {
      const bob = (frame % 2 && (state === 'thinking' || state === 'working')) ? 3 * scale : 0;
      const x = ox + 17 * scale;
      const y = oy + 20 * scale + bob;
      const s = scale;
      targetCtx.fillStyle = accent;
      targetCtx.fillRect(x + 28 * s, y - 10 * s, 5 * s, 10 * s);
      targetCtx.fillRect(x + 5 * s, y, 56 * s, 48 * s);
      targetCtx.fillRect(x, y + 10 * s, 66 * s, 30 * s);
      targetCtx.fillStyle = '#080c14';
      targetCtx.fillRect(x + 10 * s, y + 8 * s, 46 * s, 28 * s);
      const eyeH = (frame % 8 === 0 ? 2 : 8) * s;
      targetCtx.fillStyle = '#e1e9f5';
      targetCtx.fillRect(x + 18 * s, y + 17 * s, 7 * s, eyeH);
      targetCtx.fillRect(x + 41 * s, y + 17 * s, 7 * s, eyeH);
      targetCtx.fillStyle = '#080c14';
      targetCtx.fillRect(x + 24 * s, y + 43 * s, 18 * s, 3 * s);
      targetCtx.fillStyle = accent;
      targetCtx.fillRect(x + 10 * s, y + 48 * s, 12 * s, 8 * s);
      targetCtx.fillRect(x + 44 * s, y + 48 * s, 12 * s, 8 * s);
    }

    function renderCanvas(nowMs) {
      const data = app.dashboard;
      const b = data ? data.bridge : null;
      const eff = b ? b.effective_state : null;
      const state = eff ? eff.state : 'offline';
      const meta = STATE_META[state] || STATE_META.offline;
      const accent = `rgb(${meta.rgb.join(',')})`;
      const pet = data ? data.pet : { builtin: true };

      let petFrameCanvas = null;
      if (!pet.builtin && pet.states) {
        const clipName = pet.states[state] || pet.states.idle;
        const key = `${pet.id}:${clipName}`;
        const clip = app.clipCache.get(key);
        if (clip && clip.decoded.length) {
          if (app.anim.clipKey !== key) {
            app.anim.clipKey = key;
            app.anim.frame = 0;
            app.anim.nextAt = nowMs + clip.frameMs;
          } else if (nowMs >= app.anim.nextAt) {
            app.anim.frame = (app.anim.frame + 1) % clip.frames;
            app.anim.nextAt = nowMs + clip.frameMs;
          }
          petFrameCanvas = clip.decoded[app.anim.frame % clip.decoded.length];
        }
      }
      const robotFrame = Math.floor(nowMs / 350);

      if (app.viewMode === 'pet') {
        if (canvas.width !== 240 || canvas.height !== 220) {
          canvas.width = 240;
          canvas.height = 220;
          ctx.imageSmoothingEnabled = false;
        }
        ctx.fillStyle = '#080c14';
        ctx.fillRect(0, 0, 240, 220);
        ctx.fillStyle = accent;
        ctx.fillRect(0, 0, 240, 3);
        if (petFrameCanvas) {
          const drawW = petFrameCanvas.width * 1.75;
          const drawH = petFrameCanvas.height * 1.75;
          ctx.drawImage(petFrameCanvas, (240 - drawW) / 2, (206 - drawH) / 2 + 6, drawW, drawH);
        } else {
          drawRobot(ctx, 56, 36, accent, robotFrame, state, 1.5);
        }
        ctx.fillStyle = accent;
        ctx.font = 'bold 12px "Segoe UI", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(meta.title, 120, 206);
        ctx.textAlign = 'left';
      } else {
        if (canvas.width !== 320 || canvas.height !== 172) {
          canvas.width = 320;
          canvas.height = 172;
          ctx.imageSmoothingEnabled = false;
        }
        ctx.fillStyle = '#080c14';
        ctx.fillRect(0, 0, 320, 172);
        // Top accent bar
        ctx.fillStyle = accent;
        ctx.fillRect(0, 0, 320, 3);
        // Header text
        ctx.fillStyle = '#e1e9f5';
        ctx.font = 'bold 11px Consolas, monospace';
        ctx.fillText('OPENBUDDY', 12, 22);
        const linkText = !b ? 'CONNECTING'
          : b.usb_connected ? 'USB LINK'
          : b.ble_connected ? 'BLE SECURE'
          : (data.config.transport === 'none' ? 'VIRTUAL PET' : 'WAIT ESP32');
        ctx.fillStyle = '#788a9e';
        ctx.textAlign = 'right';
        ctx.fillText(linkText, 308, 22);
        ctx.textAlign = 'left';

        // Pet stage (8, 35, 104x104)
        if (petFrameCanvas) {
          const px = 8 + Math.floor((104 - petFrameCanvas.width) / 2);
          const py = 35 + Math.floor((104 - petFrameCanvas.height) / 2);
          ctx.drawImage(petFrameCanvas, px, py);
        } else {
          drawRobot(ctx, 8, 35, accent, robotFrame, state, 1);
        }

        // Right status column
        ctx.fillStyle = accent;
        ctx.font = 'bold 20px Consolas, monospace';
        ctx.fillText(meta.title, 124, 72);

        const detail = eff ? (eff.label || 'Awaiting events') : 'No OpenCode source';
        ctx.fillStyle = '#e1e9f5';
        ctx.font = '12px Consolas, monospace';
        ctx.fillText(detail.slice(0, 24), 124, 96);

        const infoLine = (eff && eff.info) || (b && b.quota) || (pet.builtin ? 'VIRTUAL / v1' : 'PET / LOCAL');
        ctx.fillStyle = '#788a9e';
        ctx.font = '11px Consolas, monospace';
        ctx.fillText(infoLine.slice(0, 24), 124, 120);

        // Divider + footer
        ctx.fillStyle = accent;
        ctx.fillRect(12, 141, 296, 1);
        const footer = b && b.manual_mode ? 'MANUAL PREVIEW ACTIVE'
          : (data && data.config.transport === 'none' ? 'DESKTOP PET MODE' : 'OPENCODE BRIDGE ACTIVE');
        ctx.fillStyle = '#788a9e';
        ctx.font = '10px Consolas, monospace';
        ctx.fillText(footer, 12, 160);
      }

      // Mirror live animated pet into Document Picture-in-Picture window if open.
      if (app.pipWindow && !app.pipWindow.closed && app.pipCtx && app.pipCanvas) {
        const pctx = app.pipCtx;
        const pw = app.pipCanvas.width;
        const ph = app.pipCanvas.height;
        pctx.imageSmoothingEnabled = false;
        pctx.fillStyle = '#080c14';
        pctx.fillRect(0, 0, pw, ph);
        // Compact state-only speech pill
        const pillW = Math.min(pw - 24, Math.max(96, meta.title.length * 9 + 36));
        const px = Math.round((pw - pillW) / 2);
        pctx.fillStyle = '#111827';
        pctx.fillRect(px, 10, pillW, 28);
        pctx.strokeStyle = accent;
        pctx.lineWidth = 2;
        pctx.strokeRect(px, 10, pillW, 28);
        pctx.fillStyle = accent;
        pctx.beginPath();
        pctx.arc(px + 14, 24, 4, 0, Math.PI * 2);
        pctx.fill();
        pctx.font = 'bold 12px "Segoe UI", sans-serif';
        pctx.fillText(meta.title, px + 24, 28);
        // Pet character
        if (petFrameCanvas) {
          const scale = (data && data.config && data.config.overlay_scale === 2) ? 1.85 : 1.5;
          const dw = Math.round(petFrameCanvas.width * scale);
          const dh = Math.round(petFrameCanvas.height * scale);
          pctx.drawImage(petFrameCanvas, Math.round((pw - dw) / 2), 46 + Math.round((ph - 52 - dh) / 2), dw, dh);
        } else {
          drawRobot(pctx, Math.round((pw - 110) / 2), 68, accent, robotFrame, state, 1.3);
        }
      }
      requestAnimationFrame(renderCanvas);
    }

    async function loadPorts() {
      try {
        const res = await api('/api/ports');
        app.ports = res.ports || [];
        const selected = app.dashboard ? app.dashboard.config.serial_port : 'auto';
        populatePorts(app.formDirty ? document.getElementById('cfgSerialPort').value : selected);
      } catch (err) {
        showToast(err.message, 'error');
      }
    }

    async function pollDashboard() {
      try {
        const data = await api('/api/dashboard');
        renderDashboard(data);
      } catch (err) {
        // Keep polling silently while bridge restarts or is busy.
      }
    }

    async function sendManualState(state) {
      try {
        const payload = { state };
        const label = document.getElementById('manualLabel').value.trim();
        const info = document.getElementById('manualInfo').value.trim();
        if (label) payload.label = label;
        if (info) payload.info = info;
        const data = await apiPost('/api/state', payload);
        renderDashboard(data);
        showToast(`État « ${STATE_META[state].fr} » appliqué.`);
      } catch (err) {
        showToast(err.message, 'error');
      }
    }

    document.getElementById('clearManualBtn').addEventListener('click', async () => {
      try {
        const data = await apiPost('/api/state/clear');
        renderDashboard(data);
        showToast('Retour en mode Live OpenCode.');
      } catch (err) {
        showToast(err.message, 'error');
      }
    });

    async function openBrowserPip() {
      if (!('documentPictureInPicture' in window)) return false;
      const pip = await window.documentPictureInPicture.requestWindow({ width: 240, height: 240 });
      pip.document.title = 'OpenBuddy Pet';
      pip.document.body.style.cssText = 'margin:0;background:#080c14;overflow:hidden;display:flex;align-items:center;justify-content:center;width:100vw;height:100vh;';
      const c = pip.document.createElement('canvas');
      c.width = 240;
      c.height = 240;
      c.style.cssText = 'width:100%;height:100%;display:block;image-rendering:pixelated;';
      pip.document.body.appendChild(c);
      app.pipWindow = pip;
      app.pipCanvas = c;
      app.pipCtx = c.getContext('2d');
      pip.addEventListener('pagehide', () => {
        app.pipWindow = null;
        app.pipCanvas = null;
        app.pipCtx = null;
        if (app.dashboard) renderDashboard(app.dashboard);
      });
      if (app.dashboard) renderDashboard(app.dashboard);
      return true;
    }

    document.getElementById('clippyBtn').addEventListener('click', async () => {
      const btn = document.getElementById('clippyBtn');
      btn.disabled = true;
      try {
        // Close browser Picture-in-Picture if open.
        if (app.pipWindow && !app.pipWindow.closed) {
          app.pipWindow.close();
          app.pipWindow = null;
          app.pipCanvas = null;
          app.pipCtx = null;
          if (app.dashboard) renderDashboard(app.dashboard);
          showToast('Pet flottant masqué.');
          return;
        }
        const ov = (app.dashboard && app.dashboard.overlay) || {};
        // If bridge runs on an isolated desktop (e.g. background sandbox), open browser PiP immediately on click.
        if (ov.desktop_isolated && !ov.visible) {
          if (await openBrowserPip()) {
            showToast('Pet flottant ouvert (mode Picture-in-Picture). Lancez le pont dans votre PowerShell pour le mode transparent natif.');
            return;
          }
        }
        const currentlyVisible = !!ov.visible;
        const data = await apiPost('/api/overlay', { visible: !currentlyVisible });
        renderDashboard(data);
        if (data.overlay && data.overlay.error) {
          if (!currentlyVisible && await openBrowserPip()) {
            showToast('Pet flottant ouvert via Picture-in-Picture.');
          } else {
            showToast(data.overlay.error, 'error');
          }
        } else {
          showToast(data.overlay.visible ? 'Pet flottant affiché par-dessus vos fenêtres.' : 'Pet flottant masqué.');
        }
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });

    async function setOverlayScale(scale) {
      try {
        const data = await apiPost('/api/config', { overlay_scale: scale });
        app.formDirty = false;
        renderDashboard(data);
        showToast(`Taille du pet flottant : ${scale}×`);
      } catch (err) {
        showToast(err.message, 'error');
      }
    }
    document.getElementById('scale1Btn').addEventListener('click', () => setOverlayScale(1));
    document.getElementById('scale2Btn').addEventListener('click', () => setOverlayScale(2));

    document.getElementById('viewScreenBtn').addEventListener('click', () => {
      app.viewMode = 'screen';
      document.getElementById('viewScreenBtn').classList.add('active');
      document.getElementById('viewPetBtn').classList.remove('active');
    });
    document.getElementById('viewPetBtn').addEventListener('click', () => {
      app.viewMode = 'pet';
      document.getElementById('viewPetBtn').classList.add('active');
      document.getElementById('viewScreenBtn').classList.remove('active');
    });

    document.getElementById('cfgTransport').addEventListener('change', () => {
      app.formDirty = true;
      updateTransportRows();
    });
    ['cfgSerialPort', 'cfgBleAddress', 'cfgPet', 'cfgSounds', 'cfgNotifs', 'cfgQuota'].forEach(id => {
      document.getElementById(id).addEventListener('change', () => { app.formDirty = true; });
    });

    document.getElementById('refreshPortsBtn').addEventListener('click', async () => {
      await loadPorts();
      showToast(`${app.ports.length} port(s) série détecté(s).`);
    });

    document.getElementById('configForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const btn = document.getElementById('saveConfigBtn');
      btn.disabled = true;
      try {
        const payload = {
          transport: document.getElementById('cfgTransport').value,
          serial_port: document.getElementById('cfgSerialPort').value,
          ble_address: document.getElementById('cfgBleAddress').value.trim(),
          pet: document.getElementById('cfgPet').value,
          sounds_enabled: document.getElementById('cfgSounds').checked,
          notifications_enabled: document.getElementById('cfgNotifs').checked,
          quota_enabled: document.getElementById('cfgQuota').checked,
        };
        const data = await apiPost('/api/config', payload);
        app.formDirty = false;
        renderDashboard(data);
        showToast('Configuration enregistrée et appliquée.');
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });

    document.getElementById('testSoundBtn').addEventListener('click', async () => {
      try {
        const res = await apiPost('/api/test/sound', { state: 'permission' });
        showToast(res.played ? 'Son de test joué.' : 'Son désactivé ou indisponible.', res.played ? 'ok' : 'error');
      } catch (err) {
        showToast(err.message, 'error');
      }
    });

    document.getElementById('testNotifBtn').addEventListener('click', async () => {
      try {
        const res = await apiPost('/api/test/notification', {});
        showToast(res.sent ? 'Notification Windows envoyée.' : 'Notifications désactivées ou indisponibles.', res.sent ? 'ok' : 'error');
      } catch (err) {
        showToast(err.message, 'error');
      }
    });

    document.getElementById('refreshQuotaBtn').addEventListener('click', async () => {
      try {
        const data = await apiPost('/api/quota/refresh', {});
        renderDashboard(data);
        showToast(data.bridge.quota ? `Quota actualisé : ${data.bridge.quota}` : 'Rafraîchissement du quota lancé.');
      } catch (err) {
        showToast(err.message, 'error');
      }
    });

    document.getElementById('refreshDeviceBtn').addEventListener('click', async () => {
      try {
        const data = await apiPost('/api/device/status', {});
        renderDashboard(data);
        showToast('Statut ESP32 actualisé.');
      } catch (err) {
        showToast(err.message, 'error');
      }
    });

    // Initial boot
    pollDashboard().then(loadPorts);
    setInterval(pollDashboard, 500);
    requestAnimationFrame(renderCanvas);
  </script>
</body>
</html>
"""


class WebAdminServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, controller):
        self.controller = controller
        super().__init__(address, WebAdminHandler)


class WebAdminHandler(BaseHTTPRequestHandler):
    server_version = "OpenBuddyAdmin/1.0"

    def log_message(self, _format, *_args):
        pass

    def _allowed_origin(self):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        if host not in (
            "127.0.0.1:%d" % port,
            "localhost:%d" % port,
            "[::1]:%d" % port,
        ):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in (
            "http://127.0.0.1:%d" % port,
            "http://localhost:%d" % port,
            "http://[::1]:%d" % port,
        )

    def _send(self, status, value, content_type="application/json; charset=utf-8", cache="no-store"):
        if isinstance(value, bytes):
            body = value
        elif isinstance(value, str):
            body = value.encode("utf-8")
        else:
            body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self, max_bytes=4096):
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return None, (415, {"error": "JSON requis"})
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            return None, (400, {"error": "Taille de requête invalide"})
        if not 0 <= length <= max_bytes:
            return None, (400, {"error": "Taille de requête invalide"})
        if length == 0:
            return {}, None
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as error:
            return None, (400, {"error": str(error)})
        if not isinstance(payload, dict):
            return None, (400, {"error": "Objet JSON attendu"})
        return payload, None

    def do_GET(self):
        if not self._allowed_origin():
            self._send(403, {"error": "Accès local uniquement"})
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/api/dashboard":
            self._send(200, self.server.controller.get_dashboard())
        elif path == "/api/ports":
            self._send(200, {"ports": self.server.controller.list_ports()})
        elif path.startswith("/api/pet/"):
            parts = path.split("/")
            # Expected: ['', 'api', 'pet', '<pet_id>', 'clip', '<clip_name>']
            if len(parts) != 6 or parts[4] != "clip" or not safe_name(parts[3]) or not safe_name(parts[5]):
                self._send(404, {"error": "Clip introuvable"})
                return
            try:
                raw = self.server.controller.get_pet_clip(parts[3], parts[5])
            except (KeyError, ValueError, OSError):
                self._send(404, {"error": "Clip introuvable"})
                return
            self._send(200, raw, "application/octet-stream", cache="private, max-age=300")
        else:
            self._send(404, {"error": "Introuvable"})

    def do_POST(self):
        if not self._allowed_origin():
            self._send(403, {"error": "Accès local uniquement"})
            return
        path = self.path.split("?", 1)[0]
        payload, err = self._read_json()
        if err is not None:
            self._send(*err)
            return
        ctrl = self.server.controller
        try:
            if path == "/api/config":
                self._send(200, ctrl.update_config(payload))
            elif path == "/api/state":
                if set(payload) - {"state", "label", "info"}:
                    raise ValueError("Paramètres invalides")
                validate_command(dict(payload, cmd="state", id=1, v=VERSION))
                self._send(200, ctrl.set_manual_state(payload))
            elif path == "/api/state/clear":
                self._send(200, ctrl.clear_manual_state())
            elif path == "/api/overlay":
                if set(payload) - {"visible", "toggle", "scale"}:
                    raise ValueError("Paramètres overlay invalides")
                self._send(200, ctrl.set_overlay(payload))
            elif path == "/api/test/sound":
                state = payload.get("state", "permission")
                if state not in STATES:
                    raise ValueError("État sonore invalide")
                self._send(200, {"played": ctrl.test_sound(state)})
            elif path == "/api/test/notification":
                self._send(200, {"sent": ctrl.test_notification()})
            elif path == "/api/quota/refresh":
                self._send(200, ctrl.refresh_quota())
            elif path == "/api/device/status":
                self._send(200, ctrl.refresh_device_status())
            else:
                self._send(404, {"error": "Introuvable"})
        except (ValueError, TypeError) as error:
            self._send(400, {"error": str(error)})
        except (OSError, TimeoutError, RuntimeError) as error:
            self._send(503, {"error": str(error)})


class BridgeWebController:
    """Thread-safe adapter connecting WebAdminServer threads to an asyncio Bridge."""

    def __init__(self, bridge, loop, port_lister=None):
        self.bridge = bridge
        self.loop = loop
        self.port_lister = port_lister or _default_list_ports

    def _run_async(self, coro, timeout=6.0):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

    def get_dashboard(self):
        return self.bridge.dashboard_snapshot()

    def list_ports(self):
        return self.port_lister()

    def get_pet_clip(self, pet_id, clip_name):
        pack = self.bridge.get_pet_pack(pet_id)
        if pack is None:
            raise KeyError(pet_id)
        return read_clip_bytes(pack, clip_name)

    def update_config(self, payload):
        return self._run_async(self.bridge.apply_config(payload, persist=True))

    def set_manual_state(self, payload):
        return self._run_async(self.bridge.set_manual_state(payload))

    def clear_manual_state(self):
        return self._run_async(self.bridge.clear_manual_state())

    def set_overlay(self, payload):
        return self._run_async(self.bridge.set_overlay(payload))

    def test_sound(self, state):
        return self._run_async(self.bridge.test_sound(state))

    def test_notification(self):
        return self._run_async(self.bridge.test_notification())

    def refresh_quota(self):
        return self._run_async(self.bridge.refresh_quota_now(), timeout=25.0)

    def refresh_device_status(self):
        return self._run_async(self.bridge.refresh_device_status(), timeout=8.0)


def _default_list_ports():
    from serial.tools import list_ports

    result = []
    for item in list_ports.comports():
        result.append({
            "device": item.device,
            "description": item.description or item.device,
            "espressif": item.vid == 0x303A,
        })
    return result


class WebAdminRunner:
    """Starts and stops WebAdminServer cleanly in a daemon thread."""

    def __init__(self, bridge, loop, port=DEFAULT_WEB_PORT, port_lister=None):
        self.controller = BridgeWebController(bridge, loop, port_lister=port_lister)
        self.server = WebAdminServer(("127.0.0.1", port), self.controller)
        self.port = self.server.server_port
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="openbuddy-web-admin",
            daemon=True,
        )

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
