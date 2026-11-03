# OpenBuddy development guide

Read `docs/hardware.md`, `docs/opencode-events.md` and `docs/animations.md` before
changing firmware or integration code.

- Replies and documentation are in French. Code, comments, identifiers and commit
  messages are in English.
- Firmware is MicroPython; do not introduce a C toolchain requirement.
- Pins and panel geometry belong in `c6lcd/board_config.py`.
- Backlight GPIO22 must use PWM at no more than 50%. WS2812 GPIO8 values stay below 40.
- LCD and SD share one SPI controller. Never initialize native `machine.SDCard`
  concurrently with the LCD; it is reserved for the USB installer.
- BLE bonding keys and bridge discovery tokens are private. Do not commit them.
- The OpenCode integration targets v2 events and the local managed service, not
  legacy hooks. Buttons and notifications do not approve permissions.
- Build the standalone plugin with `npm --prefix integration/openbuddy run build`,
  then install it with `python -m pc.install_plugin --replace`. Avoid loading both
  global and project copies.
- Keep hardware-specific tests mocked in CI. Do not flash a device or play desktop
  notifications from unit tests.
- Never include pet artwork, runtime profiles, caches, virtual environments or
  `node_modules` in source publication or transfer kits.

Checks:

```text
python -m unittest discover -s tests -v
npm --prefix integration/openbuddy test
npm --prefix integration/openbuddy run typecheck
```
