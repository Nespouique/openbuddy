from machine import Pin
import time
import board_config as cfg
from st7789 import LCD, rgb, ORDER

lcd = LCD(cfg.DEFAULT_ORIENTATION)
btn = Pin(cfg.BOOT_BUTTON, Pin.IN, Pin.PULL_UP)  # pressed = low

BLACK = rgb(0, 0, 0)
RED, GREEN, BLUE, YELLOW, WHITE = (rgb(255, 0, 0), rgb(0, 255, 0), rgb(0, 0, 255),
                                   rgb(255, 255, 0), rgb(255, 255, 255))
ORANGE, CYAN, LIME = rgb(255, 160, 0), rgb(0, 200, 255), rgb(0, 255, 120)


def draw():
    w, h = lcd.w, lcd.h
    lcd.fill_rect(0, 0, w, 3, RED)          # top    = red
    lcd.fill_rect(0, h - 3, w, 3, GREEN)    # bottom = green
    lcd.fill_rect(0, 0, 3, h, BLUE)         # left   = blue
    lcd.fill_rect(w - 3, 0, 3, h, YELLOW)   # right  = yellow
    lcd.text("TOP-LEFT", 8, 8, WHITE, BLACK, 1)
    lcd.text("BOTTOM-RIGHT", w - 8 - 12 * 8, h - 18, WHITE, BLACK, 1)
    lcd.text(lcd.orientation, 10, 40, ORANGE, BLACK, 1)
    lcd.text("%dx%d" % (w, h), 10, 60, CYAN, BLACK, 2)
    lcd.text("Hello openbuddy!", 10, 90, LIME, BLACK, 1)
    lcd.text("BOOT = next view", 10, 105, WHITE, BLACK, 1)


idx = ORDER.index(lcd.orientation)
draw()
print("orientation", ORDER[idx])
while True:
    if btn.value() == 0:
        time.sleep_ms(30)  # debounce
        if btn.value() == 0:
            idx = (idx + 1) % len(ORDER)
            lcd.set_orientation(ORDER[idx])
            draw()
            print("orientation", ORDER[idx])
            while btn.value() == 0:
                time.sleep_ms(10)
    time.sleep_ms(20)
