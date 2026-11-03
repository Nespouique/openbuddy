"""Minimal ST7789 driver for Waveshare ESP32-C6-LCD-1.47 (172x320 panel, SPI).

Pins, panel offset and orientations live in board_config.py.
Portrait is native (USB-C at the bottom). Landscape uses MADCTL MV, which
swaps the axes, so the 34px offset moves from X to Y.
"""
from machine import Pin, SPI, PWM
import framebuf
import time

import board_config as cfg
from board_config import ORIENTATIONS, ORDER
from shared_spi import SharedSPI


def rgb(r, g, b):
    """RGB565. Callers send the high byte first (color >> 8, then & 255)."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


class LCD:
    def __init__(self, orientation=cfg.DEFAULT_ORIENTATION, bus=None):
        self.bus = bus if bus is not None else SharedSPI()
        self.spi = self.bus.spi
        self.cs = self.bus.lcd_cs
        self.dc = Pin(cfg.LCD_DC, Pin.OUT, value=0)
        self.rst = Pin(cfg.LCD_RST, Pin.OUT, value=1)
        self.bl = PWM(Pin(cfg.LCD_BL), freq=cfg.BL_PWM_FREQ, duty_u16=0)
        self._init()
        self.set_orientation(orientation)
        self.set_backlight(cfg.BL_DUTY_PERCENT)

    def set_backlight(self, percent):
        """Backlight in %, clamped to BL_DUTY_PERCENT to protect the panel."""
        percent = max(0, min(percent, cfg.BL_DUTY_PERCENT))
        self.bl.duty_u16(percent * 65535 // 100)

    def set_orientation(self, name):
        madctl, self.w, self.h, self.xo, self.yo = ORIENTATIONS[name]
        self.orientation = name
        self._mv = bool(madctl & 0x20)
        self._cmd(0x36, bytes([madctl]))
        self.clear_all(0)

    def _cmd(self, c, data=None):
        self.bus.for_lcd()
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes([c]))
        if data is not None:
            self.dc(1)
            self.spi.write(data)
        self.cs(1)

    def _init(self):
        self.rst(0); time.sleep_ms(20); self.rst(1); time.sleep_ms(120)
        self._cmd(0x01); time.sleep_ms(150)       # SWRESET
        self._cmd(0x11); time.sleep_ms(120)       # SLPOUT
        self._cmd(0x3A, b'\x55')                  # 16-bit color
        self._cmd(0x36, b'\x00')                  # MADCTL: portrait, no flip
        self._cmd(0x21)                           # INVON (IPS panel)
        self._cmd(0x13)                           # NORON
        self._cmd(0x29); time.sleep_ms(50)        # DISPON

    def _raw_window(self, x0, y0, x1, y1):
        self._cmd(0x2A, bytes([x0 >> 8, x0 & 255, x1 >> 8, x1 & 255]))
        self._cmd(0x2B, bytes([y0 >> 8, y0 & 255, y1 >> 8, y1 & 255]))
        self.cs(0); self.dc(0); self.spi.write(b'\x2C'); self.dc(1)

    def window(self, x0, y0, x1, y1):
        self._raw_window(x0 + self.xo, y0 + self.yo, x1 + self.xo, y1 + self.yo)

    def clear_all(self, color=0):
        """Fill the entire controller RAM (240x320), visible or not."""
        hi, lo = color >> 8, color & 255
        line = bytearray(480)  # 240 px
        for i in range(0, 480, 2):
            line[i] = hi; line[i + 1] = lo
        # With MV set, CASET spans the 320 axis and RASET the 240 axis.
        if self._mv:
            self._raw_window(0, 0, 319, 239)
        else:
            self._raw_window(0, 0, 239, 319)
        for _ in range(320):  # 320 * 240 px
            self.spi.write(line)
        self.cs(1)

    def blit(self, buf, x, y, w, h):
        self.window(x, y, x + w - 1, y + h - 1)
        self.spi.write(buf)
        self.cs(1)

    def fill_rect(self, x, y, w, h, color):
        line = bytearray(w * 2)
        hi, lo = color >> 8, color & 255
        for i in range(w):
            line[2 * i] = hi; line[2 * i + 1] = lo
        self.window(x, y, x + w - 1, y + h - 1)
        for _ in range(h):
            self.spi.write(line)
        self.cs(1)

    def text(self, s, x, y, fg, bg, scale=1):
        """Draw text with the built-in 8x8 font at an integer scale.

        Width = len(s) * 8 * scale px; keep it <= self.w.
        """
        if not s:
            return
        n = len(s)
        w, h = 8 * n * scale, 8 * scale
        small = bytearray(n * 8)  # MONO_HLSB: n*8 px wide, 8 rows -> n bytes/row
        fb = framebuf.FrameBuffer(small, n * 8, 8, framebuf.MONO_HLSB)
        fb.fill(0)
        fb.text(s, 0, 0, 1)
        out = bytearray(w * h * 2)
        hi_f, lo_f = fg >> 8, fg & 255
        hi_b, lo_b = bg >> 8, bg & 255
        for py in range(8):
            for px in range(n * 8):
                hi, lo = (hi_f, lo_f) if fb.pixel(px, py) else (hi_b, lo_b)
                for sy in range(scale):
                    row = (py * scale + sy) * w
                    for sx in range(scale):
                        i = (row + px * scale + sx) * 2
                        out[i] = hi; out[i + 1] = lo
        self.blit(out, x, y, w, h)
