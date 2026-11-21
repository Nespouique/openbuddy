"""Loopback web administration console and virtual pet viewer for the OpenBuddy bridge."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
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
      max-width: 1140px;
      margin: 0 auto;
      padding: 22px 20px 48px;
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
    .btn-danger {
      background: rgba(255, 71, 87, 0.14);
      border-color: rgba(255, 71, 87, 0.45);
      color: #ffb3ba;
    }
    .btn-danger:hover {
      background: rgba(255, 71, 87, 0.24);
      border-color: rgba(255, 71, 87, 0.7);
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
      flex-wrap: wrap;
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
    .card h3 {
      margin: 0 0 10px;
      font-size: 13px;
      font-weight: 700;
      color: #cfd9ee;
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

    /* Inputs & buttons */
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
    input[type="text"], input[type="url"], input[type="number"], select {
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

    /* Pet collection & animation mapping */
    .import-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: flex-end;
      padding: 14px;
      border-radius: 14px;
      background: #0d1422;
      border: 1px solid var(--border-soft);
      margin-bottom: 16px;
    }
    .import-bar .field { flex: 1 1 320px; }
    .collection-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .pet-card {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 12px;
      border-radius: 14px;
      background: #0e1524;
      border: 1px solid var(--border);
      transition: border-color 0.15s, background 0.15s;
    }
    .pet-card.active {
      border-color: rgba(var(--accent-rgb), 0.75);
      box-shadow: 0 0 0 1px rgba(var(--accent-rgb), 0.35);
    }
    .pet-card.editing {
      background: #131d32;
      border-color: #60a5fa;
    }
    .pet-card-top {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .pet-thumb {
      width: 56px;
      height: 60px;
      border-radius: 10px;
      background: #080c14;
      border: 1px solid var(--border-soft);
      image-rendering: pixelated;
      flex-shrink: 0;
    }
    .pet-card-info {
      min-width: 0;
      flex: 1;
    }
    .pet-card-info strong {
      display: block;
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .pet-card-info small {
      display: block;
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .pet-card-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .pet-card-actions .btn { flex: 1 1 auto; padding: 6px 8px; font-size: 12px; }

    .mapping-panel {
      padding: 16px;
      border-radius: 14px;
      background: #0d1422;
      border: 1px solid var(--border-soft);
    }
    .mapping-board {
      display: grid;
      grid-template-columns: minmax(270px, 0.92fr) minmax(360px, 1.38fr);
      gap: 16px;
      align-items: start;
    }
    @media (max-width: 860px) {
      .mapping-board { grid-template-columns: 1fr; }
    }
    .mapping-col {
      padding: 14px;
      border-radius: 14px;
      background: #0a101c;
      border: 1px solid var(--border-soft);
    }
    .mapping-source-col {
      position: sticky;
      top: 16px;
    }
    @media (max-width: 860px) {
      .mapping-source-col { position: static; }
    }
    .mapping-col-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 10px;
    }
    .mapping-col-head h4 {
      margin: 0;
      font-size: 13px;
      font-weight: 700;
      color: var(--text);
    }
    .mapping-col-head span {
      font-size: 11px;
      color: var(--muted);
    }
    .clip-gallery {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(92px, 1fr));
      gap: 8px;
    }
    .clip-tile {
      position: relative;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      padding: 8px 6px 6px;
      border-radius: 11px;
      background: #101829;
      border: 1px solid var(--border);
      text-align: center;
      cursor: grab;
      user-select: none;
      transition: transform 0.12s ease, border-color 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease;
    }
    .clip-tile:hover {
      border-color: #5b7cff;
      background: #141f35;
      transform: translateY(-1px);
    }
    .clip-tile:active { cursor: grabbing; }
    .clip-tile.dragging {
      opacity: 0.45;
      transform: scale(0.96);
      border-style: dashed;
      border-color: #60a5fa;
    }
    .clip-tile.selected {
      border-color: #60a5fa;
      background: #16233d;
      box-shadow: 0 0 0 2px rgba(96, 165, 250, 0.32);
    }
    .clip-grip {
      position: absolute;
      top: 6px;
      left: 7px;
      font-size: 11px;
      line-height: 1;
      color: #64748b;
      letter-spacing: -1px;
    }
    .clip-use-badge {
      position: absolute;
      top: 5px;
      right: 6px;
      min-width: 18px;
      padding: 1px 5px;
      border-radius: 999px;
      background: rgba(70, 116, 255, 0.22);
      border: 1px solid rgba(96, 165, 250, 0.45);
      color: #bfdbfe;
      font-size: 10px;
      font-weight: 700;
    }
    .clip-tile canvas {
      width: 62px;
      height: 67px;
      image-rendering: pixelated;
      display: block;
      pointer-events: none;
    }
    .clip-tile strong {
      font-size: 11px;
      color: var(--text);
      line-height: 1.25;
      pointer-events: none;
    }
    .clip-tile small {
      font-size: 10px;
      color: var(--muted);
      pointer-events: none;
    }

    .mapping-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    @media (max-width: 640px) {
      .mapping-grid { grid-template-columns: 1fr; }
    }
    .state-drop-zone {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-radius: 12px;
      background: #101829;
      border: 1.5px solid var(--border);
      transition: border-color 0.15s ease, background 0.15s ease, transform 0.12s ease, box-shadow 0.15s ease;
    }
    .state-drop-zone.ready-target {
      border-style: dashed;
      border-color: rgba(147, 197, 253, 0.55);
      cursor: pointer;
    }
    .state-drop-zone.drag-over {
      border-style: dashed;
      border-color: var(--state-color);
      background: rgba(255, 255, 255, 0.06);
      box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.06), 0 0 18px rgba(var(--accent-rgb), 0.25);
      transform: scale(1.015);
    }
    .state-drop-zone.dropped {
      animation: dropFlash 0.45s ease;
    }
    @keyframes dropFlash {
      0% { box-shadow: 0 0 0 3px var(--state-color); background: rgba(50, 230, 120, 0.14); }
      100% { box-shadow: none; background: #101829; }
    }
    .map-state {
      min-width: 0;
      flex: 1;
    }
    .map-state strong {
      display: flex;
      align-items: center;
      gap: 7px;
      font-size: 13px;
    }
    .map-state small {
      display: block;
      color: var(--muted);
      font-size: 11px;
      padding-left: 17px;
    }
    .drop-hint {
      display: inline-block;
      margin-top: 5px;
      margin-left: 17px;
      padding: 2px 7px;
      border-radius: 6px;
      background: #0b111e;
      border: 1px solid var(--border-soft);
      color: #cbd5f5;
      font-size: 11px;
      font-weight: 600;
      max-width: 100%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .state-drop-zone.drag-over .drop-hint {
      background: rgba(96, 165, 250, 0.2);
      border-color: var(--state-color);
      color: #ffffff;
    }
    .map-slot {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 56px;
      height: 60px;
      border-radius: 10px;
      background: #080c14;
      border: 1px dashed var(--border);
      flex-shrink: 0;
      cursor: grab;
    }
    .state-drop-zone.drag-over .map-slot {
      border-color: var(--state-color);
    }
    .map-preview {
      width: 50px;
      height: 54px;
      border-radius: 7px;
      background: #080c14;
      image-rendering: pixelated;
      display: block;
      pointer-events: none;
    }

    .toast {
      position: fixed;
      right: 20px;
      bottom: 20px;
      max-width: 400px;
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
          <small>Administration du pont OpenCode, collection de pets &amp; compagnon flottant</small>
        </div>
      </div>
      <div class="header-actions">
        <span class="pill" id="pillSources"><span class="dot"></span><span id="pillSourcesText">OpenCode : 0</span></span>
        <span class="pill" id="pillDevice"><span class="dot"></span><span id="pillDeviceText">Matériel…</span></span>
        <span class="pill hidden" id="pillQuota"><span id="pillQuotaText"></span></span>
        <button class="btn btn-primary btn-clippy" id="clippyBtn" title="Afficher le pet dans une fenêtre flottante toujours visible au-dessus des autres applications (survol ou clic droit pour le quota)">
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

            <div class="toggle-row">
              <div>
                <strong>Hors ligne sans terminal OpenCode</strong>
                <span>Passe en OFFLINE quand aucun terminal OpenCode n'est ouvert (ignore le service seul)</span>
              </div>
              <div class="toggle-controls">
                <label class="switch"><input type="checkbox" id="cfgRequireClient"><span class="slider"></span></label>
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

    <section class="card">
      <div class="card-head">
        <div>
          <h2>Collection de pets &amp; mapping des animations</h2>
          <span class="muted">Importez n'importe quel pet depuis <strong>codex-pets.net</strong> et associez ses animations aux états OpenCode</span>
        </div>
      </div>

      <form id="importPetForm" class="import-bar">
        <div class="field">
          <label for="petUrlInput">URL Codex Pets (ex. https://codex-pets.net/#/pets/ferris-rust-crab ou monthly-salary-cat)</label>
          <input id="petUrlInput" type="text" maxlength="256" placeholder="https://codex-pets.net/#/pets/ferris-rust-crab" required>
        </div>
        <button type="submit" class="btn btn-primary" id="importPetBtn">Ajouter à la collection</button>
      </form>

      <h3>Mes pets</h3>
      <div class="collection-grid" id="petCollectionGrid"></div>

      <div id="mappingPanel" class="mapping-panel hidden">
        <div class="card-head" style="margin-bottom:14px;">
          <div>
            <h3 id="mappingTitle" style="margin:0;">Mapping des animations</h3>
            <span class="muted" id="mappingSubtitle">Glissez une animation de gauche vers un état OpenCode à droite</span>
          </div>
          <div class="inline-row" style="gap:8px;flex:0 0 auto;">
            <span class="pill warn hidden" id="mappingDirtyPill"><span class="dot"></span><span>Non enregistré</span></span>
            <button type="button" class="btn btn-subtle" id="resetMappingBtn">Réinitialiser</button>
            <button type="button" class="btn btn-primary" id="saveMappingBtn">Enregistrer le mapping</button>
          </div>
        </div>

        <div class="mapping-board">
          <div class="mapping-col mapping-source-col">
            <div class="mapping-col-head">
              <h4>Animations disponibles</h4>
              <span id="clipCountHint">Glisser-déposer →</span>
            </div>
            <div class="clip-gallery" id="clipGallery"></div>
          </div>
          <div class="mapping-col mapping-target-col">
            <div class="mapping-col-head">
              <h4>États OpenCode</h4>
              <span>Déposez une animation sur un état</span>
            </div>
            <div class="mapping-grid" id="mappingGrid"></div>
          </div>
        </div>
      </div>
    </section>
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
      pairing:    { title: 'PAIRING',    fr: 'Appairage',    rgb: [170, 90, 255] },
    };
    const TEST_STATES = ['idle', 'thinking', 'working', 'permission', 'question', 'success', 'error', 'retry'];
    const MAPPING_STATES = ['idle', 'thinking', 'working', 'permission', 'question', 'success', 'error', 'retry', 'offline', 'pairing'];

    const app = {
      dashboard: null,
      viewMode: 'screen',
      formDirty: false,
      ports: [],
      clipCache: new Map(), // "petId:clip" -> { width, height, frames, frameMs, decoded: [] }
      clipLoading: new Set(),
      anim: { clipKey: null, frame: 0, nextAt: 0 },
      pipWindow: null,
      pipCanvas: null,
      pipCtx: null,
      pipHover: false,
      pipPinQuota: false,
      canvasHover: false,
      canvasPinQuota: false,
      editingPetId: null,
      mappingDirty: false,
      mappingDraft: {},
      draggingClip: null,
      selectedClip: null,
      collectionKey: '',
      mappingKey: '',
      miniCanvases: [], // { canvas, petId, clipName }
    };

    const canvas = document.getElementById('buddyCanvas');
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;

    canvas.addEventListener('mouseenter', () => { app.canvasHover = true; });
    canvas.addEventListener('mouseleave', () => { app.canvasHover = false; });
    canvas.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      app.canvasPinQuota = !app.canvasPinQuota;
      const q = currentQuotaLabel();
      showToast(q);
    });

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
      toastTimer = setTimeout(() => el.classList.remove('show'), 3400);
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

    function escapeHtml(text) {
      return String(text).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    }

    function currentQuotaLabel() {
      const data = app.dashboard;
      if (!data) return 'Quota : …';
      const b = data.bridge || {};
      const cfg = data.config || {};
      const eff = b.effective_state;
      const val = (eff && eff.info) || b.quota || '';
      if (val) return `Quota : ${val}`;
      if (!cfg.quota_enabled) return 'Quota : désactivé';
      if (b.quota_error) return 'Quota : erreur';
      return 'Quota : non configuré';
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
          : `${item.name || item.id}${item.author ? ' — par ' + item.author : ''}`;
        sel.appendChild(opt);
      }
      sel.value = prev;
    }

    function syncFormFromConfig(cfg, petInfo) {
      if (app.formDirty) return;
      document.getElementById('cfgTransport').value = cfg.transport || 'usb';
      populatePorts(cfg.serial_port || 'auto');
      populatePetSelect(petInfo, cfg.pet || 'auto');
      document.getElementById('cfgSounds').checked = !!cfg.sounds_enabled;
      document.getElementById('cfgNotifs').checked = !!cfg.notifications_enabled;
      document.getElementById('cfgQuota').checked = !!cfg.quota_enabled;
      document.getElementById('cfgRequireClient').checked = cfg.require_opencode_client !== false;
      updateTransportRows();
    }

    function loadClip(pet, clipName) {
      if (!pet || pet.builtin || !clipName) return;
      const animMeta = pet.animations && pet.animations[clipName];
      if (!animMeta) return;
      const key = `${pet.id}:${clipName}`;
      if (app.clipCache.has(key) || app.clipLoading.has(key)) return;
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

    function ensureActiveClipLoaded() {
      const data = app.dashboard;
      if (!data || !data.pet || data.pet.builtin) return;
      const pet = data.pet;
      const state = data.bridge.effective_state ? data.bridge.effective_state.state : 'offline';
      const clipName = (pet.states && (pet.states[state] || pet.states.idle)) || 'idle';
      loadClip(pet, clipName);
    }

    function findAvailablePet(petId) {
      const list = (app.dashboard && app.dashboard.pet && app.dashboard.pet.available) || [];
      return list.find(item => item.id === petId) || null;
    }

    function renderCollectionAndMapping(petInfo) {
      const available = petInfo.available || [];
      const activeId = petInfo.active_id || 'builtin';
      const nonBuiltin = available.filter(item => !item.builtin);

      if (!app.editingPetId || !nonBuiltin.some(p => p.id === app.editingPetId)) {
        const activePack = nonBuiltin.find(p => p.id === activeId);
        app.editingPetId = activePack ? activePack.id : (nonBuiltin[0] ? nonBuiltin[0].id : null);
        app.mappingDirty = false;
      }

      const colKey = JSON.stringify({
        activeId,
        editing: app.editingPetId,
        list: available.map(p => [p.id, p.name, p.author, Object.keys(p.animations || {}).length, p.deletable]),
      });

      const editPet = app.editingPetId ? findAvailablePet(app.editingPetId) : null;
      const mapKey = editPet ? JSON.stringify({
        id: editPet.id,
        states: editPet.states,
        anims: Object.keys(editPet.animations || {}),
      }) : 'none';

      const rebuildCollection = colKey !== app.collectionKey;
      const rebuildMapping = !app.mappingDirty && mapKey !== app.mappingKey;
      if (!rebuildCollection && !rebuildMapping) return;

      if (rebuildCollection) {
        app.collectionKey = colKey;
        const grid = document.getElementById('petCollectionGrid');
        grid.innerHTML = '';
        for (const item of available) {
          const isActive = item.id === activeId;
          const isEditing = item.id === app.editingPetId;
          const card = document.createElement('div');
          card.className = `pet-card${isActive ? ' active' : ''}${isEditing ? ' editing' : ''}`;
          const animCount = Object.keys(item.animations || {}).length;
          const metaLine = item.builtin
            ? 'Intégré · sans pack'
            : `${item.author ? '@' + item.author + ' · ' : ''}${animCount} anims`;
          card.innerHTML = `
            <div class="pet-card-top">
              <canvas class="pet-thumb" width="96" height="104" data-pet-thumb="${escapeHtml(item.id)}"></canvas>
              <div class="pet-card-info">
                <strong title="${escapeHtml(item.name || item.id)}">${escapeHtml(item.name || item.id)}</strong>
                <small>${escapeHtml(metaLine)}</small>
                <small>${isActive ? '★ Actif' : escapeHtml(item.id)}</small>
              </div>
            </div>
            <div class="pet-card-actions">
              <button type="button" class="btn btn-subtle" data-action="use" data-pet="${escapeHtml(item.id)}">${isActive ? 'Actif' : 'Utiliser'}</button>
              ${item.builtin ? '' : `<button type="button" class="btn btn-subtle" data-action="map" data-pet="${escapeHtml(item.id)}">Mapper</button>`}
              ${item.deletable ? `<button type="button" class="btn btn-danger btn-subtle" data-action="delete" data-pet="${escapeHtml(item.id)}">Suppr.</button>` : ''}
            </div>
          `;
          grid.appendChild(card);
          if (!item.builtin) {
            const idleClip = (item.states && item.states.idle) || Object.keys(item.animations || {})[0];
            if (idleClip) loadClip(item, idleClip);
          }
        }
      }

      const panel = document.getElementById('mappingPanel');
      if (!editPet || editPet.builtin) {
        panel.classList.add('hidden');
        app.mappingKey = 'none';
        app.mappingDraft = {};
        app.selectedClip = null;
        setMappingDirty(false);
      } else if (rebuildMapping) {
        app.mappingKey = mapKey;
        app.mappingDraft = Object.assign({}, editPet.states || {});
        app.selectedClip = null;
        setMappingDirty(false);
        panel.classList.remove('hidden');
        const animNames = Object.keys(editPet.animations || {});
        document.getElementById('mappingTitle').textContent = `Mapping des animations — ${editPet.name || editPet.id}`;
        document.getElementById('mappingSubtitle').textContent =
          `${animNames.length} animations disponibles · glissez-déposez (ou cliquez une animation puis un état)`;
        document.getElementById('clipCountHint').textContent = `${animNames.length} dispo · glisser →`;

        // Left zone: draggable available animations
        const gallery = document.getElementById('clipGallery');
        gallery.innerHTML = '';
        for (const clipName of animNames) {
          loadClip(editPet, clipName);
          const tile = document.createElement('div');
          tile.className = 'clip-tile';
          tile.draggable = true;
          tile.dataset.clipName = clipName;
          tile.title = `Glisser « ${clipName} » vers un état OpenCode, ou cliquer pour sélectionner`;
          tile.innerHTML = `
            <span class="clip-grip" aria-hidden="true">⋮⋮</span>
            <span class="clip-use-badge hidden" data-clip-count="${escapeHtml(clipName)}">0</span>
            <canvas width="96" height="104" data-gallery-pet="${escapeHtml(editPet.id)}" data-gallery-clip="${escapeHtml(clipName)}"></canvas>
            <strong>${escapeHtml(clipName)}</strong>
          `;
          tile.addEventListener('dragstart', (event) => {
            app.draggingClip = clipName;
            app.selectedClip = null;
            tile.classList.add('dragging');
            updateDragTargetHighlights();
            if (event.dataTransfer) {
              event.dataTransfer.effectAllowed = 'copy';
              event.dataTransfer.setData('text/plain', clipName);
            }
          });
          tile.addEventListener('dragend', () => {
            app.draggingClip = null;
            tile.classList.remove('dragging');
            clearDragOverStates();
            updateDragTargetHighlights();
          });
          tile.addEventListener('click', () => {
            app.selectedClip = (app.selectedClip === clipName) ? null : clipName;
            updateDragTargetHighlights();
          });
          gallery.appendChild(tile);
        }

        // Right zone: OpenCode states drop zones
        const mapGrid = document.getElementById('mappingGrid');
        mapGrid.innerHTML = '';
        for (const st of MAPPING_STATES) {
          const meta = STATE_META[st] || { title: st.toUpperCase(), fr: st, rgb: [120, 140, 160] };
          const color = `rgb(${meta.rgb.join(',')})`;
          const assigned = app.mappingDraft[st] || animNames[0] || 'idle';
          app.mappingDraft[st] = assigned;
          const zone = document.createElement('div');
          zone.className = 'state-drop-zone';
          zone.dataset.dropState = st;
          zone.style.setProperty('--state-color', color);
          zone.innerHTML = `
            <div class="map-state">
              <strong><span class="state-swatch" style="--state-color:${color}"></span>${escapeHtml(meta.fr)}</strong>
              <small>${escapeHtml(meta.title)}</small>
              <span class="drop-hint" data-assigned-label="${escapeHtml(st)}"></span>
            </div>
            <div class="map-slot" draggable="true" title="Glisser cette animation vers un autre état">
              <canvas class="map-preview" width="96" height="104" data-map-state="${escapeHtml(st)}"></canvas>
            </div>
          `;
          const slot = zone.querySelector('.map-slot');
          slot.addEventListener('dragstart', (event) => {
            const cur = app.mappingDraft[st];
            if (!cur) return;
            app.draggingClip = cur;
            app.selectedClip = null;
            updateDragTargetHighlights();
            if (event.dataTransfer) {
              event.dataTransfer.effectAllowed = 'copy';
              event.dataTransfer.setData('text/plain', cur);
            }
          });
          slot.addEventListener('dragend', () => {
            app.draggingClip = null;
            clearDragOverStates();
            updateDragTargetHighlights();
          });
          zone.addEventListener('dragover', (event) => {
            if (!app.draggingClip) return;
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
            zone.classList.add('drag-over');
          });
          zone.addEventListener('dragenter', (event) => {
            if (!app.draggingClip) return;
            event.preventDefault();
            zone.classList.add('drag-over');
          });
          zone.addEventListener('dragleave', (event) => {
            if (!zone.contains(event.relatedTarget)) {
              zone.classList.remove('drag-over');
            }
          });
          zone.addEventListener('drop', (event) => {
            event.preventDefault();
            zone.classList.remove('drag-over');
            const droppedClip = (event.dataTransfer && event.dataTransfer.getData('text/plain')) || app.draggingClip;
            if (droppedClip && editPet.animations && editPet.animations[droppedClip]) {
              assignClipToState(st, droppedClip, zone);
            }
            app.draggingClip = null;
            updateDragTargetHighlights();
          });
          zone.addEventListener('click', () => {
            if (app.selectedClip && editPet.animations && editPet.animations[app.selectedClip]) {
              assignClipToState(st, app.selectedClip, zone);
              app.selectedClip = null;
              updateDragTargetHighlights();
            }
          });
          mapGrid.appendChild(zone);
        }
        refreshMappingLabelsAndBadges();
      }

      // Refresh registry of mini canvases to animate in renderCanvas loop
      app.miniCanvases = [];
      document.querySelectorAll('canvas[data-pet-thumb]').forEach(el => {
        const pid = el.dataset.petThumb;
        const p = findAvailablePet(pid);
        const clip = p && !p.builtin ? ((p.states && p.states.idle) || Object.keys(p.animations || {})[0]) : null;
        app.miniCanvases.push({ canvas: el, petId: pid, clipName: clip, builtin: !p || p.builtin });
      });
      document.querySelectorAll('canvas[data-gallery-clip]').forEach(el => {
        app.miniCanvases.push({
          canvas: el,
          petId: el.dataset.galleryPet,
          clipName: el.dataset.galleryClip,
          builtin: false,
        });
      });
      document.querySelectorAll('canvas[data-map-state]').forEach(el => {
        const st = el.dataset.mapState;
        app.miniCanvases.push({
          canvas: el,
          petId: app.editingPetId,
          mapState: st,
          builtin: false,
        });
      });
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
      const daemonOnly = b.sources > 0 && cfg.require_opencode_client && b.opencode_client_active === false && !eff;
      srcPill.className = 'pill ' + (b.sources > 0 && !daemonOnly ? 'online' : '');
      document.getElementById('pillSourcesText').textContent = daemonOnly
        ? 'OpenCode : service seul (OFFLINE)'
        : `OpenCode : ${b.sources} source${b.sources > 1 ? 's' : ''}`;

      const devPill = document.getElementById('pillDevice');
      if (cfg.transport === 'none') {
        devPill.className = 'pill virtual';
        document.getElementById('pillDeviceText').textContent = 'Mode virtuel (sans ESP32)';
      } else if (b.usb_connected) {
        devPill.className = 'pill online';
        document.getElementById('pillDeviceText').textContent = `ESP32 USB (${b.serial_port_active || 'connecté'})`;
      } else {
        devPill.className = 'pill warn';
        document.getElementById('pillDeviceText').textContent = 'ESP32 USB en attente…';
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
      const petName = data.pet.builtin ? 'Robot intégré' : (data.pet.name || data.pet.id);
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
        ['Transport actif', cfg.transport === 'none' ? 'Virtuel (aucun)' : 'USB Série'],
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
      renderCollectionAndMapping(data.pet);
      ensureActiveClipLoaded();
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

    function drawQuotaOverlay(targetCtx, width, height, accent, text) {
      targetCtx.save();
      targetCtx.font = 'bold 11px "Segoe UI", sans-serif';
      const tw = targetCtx.measureText(text).width;
      const pillW = Math.min(width - 16, Math.max(96, tw + 20));
      const px = Math.round((width - pillW) / 2);
      const py = height - 30;
      targetCtx.fillStyle = 'rgba(17, 24, 39, 0.94)';
      targetCtx.fillRect(px, py, pillW, 22);
      targetCtx.strokeStyle = accent;
      targetCtx.lineWidth = 1.5;
      targetCtx.strokeRect(px, py, pillW, 22);
      targetCtx.fillStyle = '#edf2ff';
      targetCtx.textAlign = 'center';
      targetCtx.fillText(text, Math.round(width / 2), py + 15);
      targetCtx.restore();
    }

    function renderMiniCanvases(nowMs, accent, robotFrame) {
      for (const item of app.miniCanvases) {
        const el = item.canvas;
        if (!el || !el.isConnected) continue;
        const mctx = el.getContext('2d');
        mctx.imageSmoothingEnabled = false;
        mctx.fillStyle = '#080c14';
        mctx.fillRect(0, 0, el.width, el.height);
        if (item.builtin) {
          drawRobot(mctx, 0, 8, accent, robotFrame, 'idle', 0.95);
          continue;
        }
        let clipName = item.clipName;
        if (item.mapState) {
          clipName = app.mappingDraft[item.mapState] || clipName;
        }
        if (!clipName) continue;
        const clip = app.clipCache.get(`${item.petId}:${clipName}`);
        if (clip && clip.decoded.length) {
          const idx = Math.floor(nowMs / Math.max(100, clip.frameMs)) % clip.decoded.length;
          mctx.drawImage(clip.decoded[idx], 0, 0, el.width, el.height);
        }
      }
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
        if (app.canvasHover || app.canvasPinQuota) {
          drawQuotaOverlay(ctx, 240, 220, accent, currentQuotaLabel());
        } else {
          ctx.fillStyle = accent;
          ctx.font = 'bold 12px "Segoe UI", sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText(meta.title, 120, 206);
          ctx.textAlign = 'left';
        }
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

      // Render collection thumbnails and mapping editor previews
      renderMiniCanvases(nowMs, accent, robotFrame);

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
        if (app.pipHover || app.pipPinQuota) {
          drawQuotaOverlay(pctx, pw, ph, accent, currentQuotaLabel());
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
      c.addEventListener('mouseenter', () => { app.pipHover = true; });
      c.addEventListener('mouseleave', () => { app.pipHover = false; });
      c.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        app.pipPinQuota = !app.pipPinQuota;
      });
      pip.document.body.appendChild(c);
      app.pipWindow = pip;
      app.pipCanvas = c;
      app.pipCtx = c.getContext('2d');
      app.pipHover = false;
      app.pipPinQuota = false;
      pip.addEventListener('pagehide', () => {
        app.pipWindow = null;
        app.pipCanvas = null;
        app.pipCtx = null;
        app.pipHover = false;
        app.pipPinQuota = false;
        if (app.dashboard) renderDashboard(app.dashboard);
      });
      if (app.dashboard) renderDashboard(app.dashboard);
      return true;
    }

    document.getElementById('clippyBtn').addEventListener('click', async () => {
      const btn = document.getElementById('clippyBtn');
      btn.disabled = true;
      try {
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
        if (ov.desktop_isolated && !ov.visible) {
          if (await openBrowserPip()) {
            showToast('Pet flottant ouvert (mode Picture-in-Picture). Survolez ou faites clic droit pour le quota.');
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
          showToast(data.overlay.visible ? 'Pet flottant affiché (survol / clic droit pour le quota).' : 'Pet flottant masqué.');
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
    ['cfgSerialPort', 'cfgPet', 'cfgSounds', 'cfgNotifs', 'cfgQuota', 'cfgRequireClient'].forEach(id => {
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
          pet: document.getElementById('cfgPet').value,
          sounds_enabled: document.getElementById('cfgSounds').checked,
          notifications_enabled: document.getElementById('cfgNotifs').checked,
          quota_enabled: document.getElementById('cfgQuota').checked,
          require_opencode_client: document.getElementById('cfgRequireClient').checked,
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

    // Browser-side WebP fallback converter when Pillow is not installed on the PC
    async function decodeAndUploadStagedPet(info) {
      const resp = await fetch(info.sheet_url);
      if (!resp.ok) throw new Error('Impossible de charger la spritesheet distante');
      const blob = await resp.blob();
      const bitmap = await createImageBitmap(blob);
      try {
        const w = bitmap.width;
        const h = bitmap.height;
        if (w !== 1536 || (h !== 1872 && h !== 2288)) {
          throw new Error(`Atlas Codex Pets inattendu (${w}×${h}, attendu 1536×1872 ou 1536×2288)`);
        }
        const hasLook = h >= 11 * 208;
        const work = document.createElement('canvas');
        work.width = 96;
        work.height = 104;
        const wctx = work.getContext('2d', { willReadFrequently: true });
        wctx.imageSmoothingEnabled = false;
        for (const [clipName, segments] of Object.entries(info.clips || {})) {
          if (clipName === 'look' && !hasLook) continue;
          let totalFrames = 0;
          for (const [, count] of segments) totalFrames += count;
          const out = new Uint8Array(totalFrames * 96 * 104 * 2);
          let offset = 0;
          for (const [row, count] of segments) {
            for (let f = 0; f < count; f++) {
              wctx.fillStyle = 'rgb(8, 12, 20)';
              wctx.fillRect(0, 0, 96, 104);
              wctx.drawImage(bitmap, f * 192, row * 208, 192, 208, 0, 0, 96, 104);
              const rgba = wctx.getImageData(0, 0, 96, 104).data;
              for (let i = 0; i < rgba.length; i += 4) {
                const c = ((rgba[i] & 0xF8) << 8) | ((rgba[i + 1] & 0xFC) << 3) | (rgba[i + 2] >> 3);
                out[offset++] = c >> 8;
                out[offset++] = c & 255;
              }
            }
          }
          const up = await fetch(`/api/pets/staged/${encodeURIComponent(info.pet_id)}/clip/${encodeURIComponent(clipName)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/octet-stream' },
            body: out,
          });
          if (!up.ok) {
            const err = await up.json().catch(() => ({}));
            throw new Error(err.error || `Échec d'envoi du clip ${clipName}`);
          }
        }
      } finally {
        bitmap.close();
      }
      return apiPost(`/api/pets/staged/${encodeURIComponent(info.pet_id)}/commit`, {});
    }

    document.getElementById('importPetForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const input = document.getElementById('petUrlInput');
      const btn = document.getElementById('importPetBtn');
      const url = input.value.trim();
      if (!url) return;
      btn.disabled = true;
      btn.textContent = 'Téléchargement…';
      try {
        let res = await apiPost('/api/pets/import', { url, activate: true });
        if (res.status === 'needs_client_decode') {
          btn.textContent = 'Conversion navigateur…';
          res = await decodeAndUploadStagedPet(res);
        }
        // Clear cached clips for this pet_id in case it was re-imported
        for (const key of Array.from(app.clipCache.keys())) {
          if (key.startsWith(res.pet_id + ':')) app.clipCache.delete(key);
        }
        app.editingPetId = res.pet_id;
        setMappingDirty(false);
        app.collectionKey = '';
        app.mappingKey = '';
        app.formDirty = false;
        input.value = '';
        if (res.dashboard) renderDashboard(res.dashboard);
        showToast(`Pet « ${res.pet_id} » ajouté à la collection et activé.`);
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = 'Ajouter à la collection';
      }
    });

    document.getElementById('petCollectionGrid').addEventListener('click', async (event) => {
      const btn = event.target.closest('button[data-action]');
      if (!btn) return;
      const action = btn.dataset.action;
      const petId = btn.dataset.pet;
      if (!petId) return;
      try {
        if (action === 'use') {
          const data = await apiPost('/api/config', { pet: petId });
          app.formDirty = false;
          if (petId !== 'builtin') {
            app.editingPetId = petId;
            setMappingDirty(false);
          }
          app.collectionKey = '';
          app.mappingKey = '';
          renderDashboard(data);
          showToast(`Pet actif : ${petId}`);
        } else if (action === 'map') {
          app.editingPetId = petId;
          setMappingDirty(false);
          app.collectionKey = '';
          app.mappingKey = '';
          if (app.dashboard) renderCollectionAndMapping(app.dashboard.pet);
          document.getElementById('mappingPanel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else if (action === 'delete') {
          if (!confirm(`Supprimer le pet « ${petId} » de la collection locale ?`)) return;
          const data = await apiPost(`/api/pets/${encodeURIComponent(petId)}/delete`, {});
          for (const key of Array.from(app.clipCache.keys())) {
            if (key.startsWith(petId + ':')) app.clipCache.delete(key);
          }
          if (app.editingPetId === petId) app.editingPetId = null;
          setMappingDirty(false);
          app.collectionKey = '';
          app.mappingKey = '';
          app.formDirty = false;
          renderDashboard(data);
          showToast(`Pet « ${petId} » supprimé.`);
        }
      } catch (err) {
        showToast(err.message, 'error');
      }
    });

    function setMappingDirty(dirty) {
      app.mappingDirty = !!dirty;
      const pill = document.getElementById('mappingDirtyPill');
      if (pill) pill.classList.toggle('hidden', !app.mappingDirty);
    }

    function clearDragOverStates() {
      document.querySelectorAll('#mappingGrid .state-drop-zone.drag-over').forEach(z => z.classList.remove('drag-over'));
    }

    function updateDragTargetHighlights() {
      const active = !!(app.draggingClip || app.selectedClip);
      document.querySelectorAll('#clipGallery .clip-tile').forEach(tile => {
        tile.classList.toggle('selected', !!app.selectedClip && tile.dataset.clipName === app.selectedClip);
      });
      document.querySelectorAll('#mappingGrid .state-drop-zone').forEach(zone => {
        zone.classList.toggle('ready-target', active);
      });
    }

    function refreshMappingLabelsAndBadges() {
      const counts = {};
      for (const st of MAPPING_STATES) {
        const clip = app.mappingDraft[st];
        if (clip) counts[clip] = (counts[clip] || 0) + 1;
        const labelEl = document.querySelector(`[data-assigned-label="${st}"]`);
        if (labelEl) {
          labelEl.textContent = clip || '—';
        }
      }
      document.querySelectorAll('[data-clip-count]').forEach(badge => {
        const name = badge.dataset.clipCount;
        const n = counts[name] || 0;
        badge.textContent = `×${n}`;
        badge.classList.toggle('hidden', n === 0);
      });
    }

    function assignClipToState(state, clipName, zoneEl) {
      app.mappingDraft[state] = clipName;
      setMappingDirty(true);
      refreshMappingLabelsAndBadges();
      if (zoneEl) {
        zoneEl.classList.remove('dropped');
        void zoneEl.offsetWidth;
        zoneEl.classList.add('dropped');
      }
    }

    document.getElementById('saveMappingBtn').addEventListener('click', async () => {
      if (!app.editingPetId) return;
      const btn = document.getElementById('saveMappingBtn');
      btn.disabled = true;
      try {
        const states = Object.assign({}, app.mappingDraft);
        const data = await apiPost(`/api/pets/${encodeURIComponent(app.editingPetId)}/mapping`, { states });
        setMappingDirty(false);
        app.mappingKey = '';
        renderDashboard(data);
        showToast('Mapping des animations enregistré et appliqué.');
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });

    document.getElementById('resetMappingBtn').addEventListener('click', async () => {
      if (!app.editingPetId) return;
      try {
        const data = await apiPost(`/api/pets/${encodeURIComponent(app.editingPetId)}/mapping`, { reset: true });
        setMappingDirty(false);
        app.mappingKey = '';
        renderDashboard(data);
        showToast('Mapping par défaut restauré.');
      } catch (err) {
        showToast(err.message, 'error');
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

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


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
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _read_json(self, max_bytes=8192):
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

    def _read_binary(self, max_bytes=1024 * 1024):
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            return None, (400, {"error": "Taille de requête invalide"})
        if not 0 < length <= max_bytes:
            return None, (400, {"error": "Taille binaire invalide"})
        return self.rfile.read(length), None

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
        elif path.startswith("/api/pets/staged/"):
            parts = path.split("/")
            # Expected: ['', 'api', 'pets', 'staged', '<pet_id>', 'spritesheet']
            if len(parts) != 6 or parts[5] != "spritesheet" or not safe_name(parts[4]):
                self._send(404, {"error": "Spritesheet introuvable"})
                return
            try:
                raw = self.server.controller.get_staged_spritesheet(parts[4])
            except (KeyError, ValueError, OSError):
                self._send(404, {"error": "Spritesheet introuvable"})
                return
            self._send(200, raw, "image/webp", cache="no-store")
        else:
            self._send(404, {"error": "Introuvable"})

    def do_POST(self):
        if not self._allowed_origin():
            self._send(403, {"error": "Accès local uniquement"})
            return
        path = self.path.split("?", 1)[0]
        ctrl = self.server.controller

        # Binary upload path for browser-decoded RGB565 clips
        if path.startswith("/api/pets/staged/"):
            parts = path.split("/")
            if len(parts) == 7 and parts[5] == "clip" and safe_name(parts[4]) and safe_name(parts[6]):
                raw, err = self._read_binary(max_bytes=512 * 1024)
                if err is not None:
                    self._send(*err)
                    return
                try:
                    self._send(200, ctrl.store_staged_clip(parts[4], parts[6], raw))
                except (KeyError, ValueError, TypeError) as error:
                    self._send(400, {"error": str(error)})
                return

        payload, err = self._read_json()
        if err is not None:
            self._send(*err)
            return
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
            elif path == "/api/pets/import":
                if set(payload) - {"url", "activate", "states"}:
                    raise ValueError("Paramètres d'import invalides")
                url = payload.get("url", "")
                activate = payload.get("activate", True)
                if type(activate) is not bool:
                    raise ValueError("activate doit être un booléen")
                states = payload.get("states")
                self._send(200, ctrl.import_pet(url, activate=activate, states=states))
            elif path.startswith("/api/pets/staged/"):
                parts = path.split("/")
                # Expected: ['', 'api', 'pets', 'staged', '<pet_id>', 'commit']
                if len(parts) != 6 or parts[5] != "commit" or not safe_name(parts[4]):
                    self._send(404, {"error": "Introuvable"})
                    return
                if set(payload) - {"states", "activate"}:
                    raise ValueError("Paramètres de finalisation invalides")
                self._send(200, ctrl.commit_staged_import(
                    parts[4],
                    states=payload.get("states"),
                    activate=payload.get("activate"),
                ))
            elif path.startswith("/api/pets/"):
                parts = path.split("/")
                # Expected: ['', 'api', 'pets', '<pet_id>', 'mapping'|'delete']
                if len(parts) != 5 or not safe_name(parts[3]):
                    self._send(404, {"error": "Introuvable"})
                    return
                pet_id, action = parts[3], parts[4]
                if action == "mapping":
                    if set(payload) - {"states", "reset"}:
                        raise ValueError("Paramètres de mapping invalides")
                    reset = bool(payload.get("reset", False))
                    self._send(200, ctrl.update_pet_mapping(pet_id, states=payload.get("states"), reset=reset))
                elif action == "delete":
                    self._send(200, ctrl.delete_pet(pet_id))
                else:
                    self._send(404, {"error": "Introuvable"})
            else:
                self._send(404, {"error": "Introuvable"})
        except (ValueError, TypeError, KeyError) as error:
            self._send(400, {"error": str(error)})
        except (OSError, TimeoutError, RuntimeError) as error:
            self._send(503, {"error": str(error)})
        except Exception as error:
            self._send(500, {"error": "%s: %s" % (type(error).__name__, error)})


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

    def get_staged_spritesheet(self, pet_id):
        return self.bridge.get_staged_spritesheet(pet_id)

    def store_staged_clip(self, pet_id, clip_name, raw_bytes):
        return self.bridge.store_staged_clip(pet_id, clip_name, raw_bytes)

    def import_pet(self, url, activate=True, states=None):
        return self._run_async(
            self.bridge.import_pet_from_url(url, activate=activate, states=states),
            timeout=60.0,
        )

    def commit_staged_import(self, pet_id, states=None, activate=None):
        return self._run_async(
            self.bridge.commit_staged_import(pet_id, states=states, activate=activate),
            timeout=20.0,
        )

    def update_pet_mapping(self, pet_id, states=None, reset=False):
        return self._run_async(
            self.bridge.update_pet_mapping(pet_id, states=states, reset=reset),
            timeout=10.0,
        )

    def delete_pet(self, pet_id):
        return self._run_async(self.bridge.delete_pet(pet_id), timeout=10.0)

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
