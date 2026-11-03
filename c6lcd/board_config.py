"""Hardware configuration for the Waveshare ESP32-C6-LCD-1.47 (non-touch).

Single source of truth for pins and panel geometry. Imported by st7789.py and
main.py, so a pin or offset change happens here only.
"""

# --- Display (ST7789, SPI, write-only) -------------------------------------
LCD_SPI_ID = 1
LCD_SPI_BAUD = 40_000_000   # 40 MHz is the practical ceiling on this board
LCD_MOSI = 6                # shared with the TF card slot
LCD_SCLK = 7                # shared with the TF card slot
LCD_CS = 14
LCD_DC = 15
LCD_RST = 21
LCD_BL = 22                 # backlight, drive with PWM (see BL_DUTY_PERCENT)

# Waveshare warns that >50 % backlight duty for long periods can leave
# permanent dark shadows on the panel. Never drive GPIO22 as a plain output.
BL_PWM_FREQ = 1000
BL_DUTY_PERCENT = 50

# --- Other onboard peripherals ----------------------------------------------
RGB_LED = 8                 # one WS2812 (GRB order), VERY bright: keep values < 40
BOOT_BUTTON = 9             # pressed = low (RESET is not a GPIO)
SD_CS = 4                   # TF card, same SPI bus as the LCD
SD_MISO = 5
SD_SPI_BAUD = 8_000_000      # conservative shared-bus clock; benchmark on the card
SD_MOUNT = "/sd"
PET_DIRECTORY = "/sd/openbuddy/pets"
PET_SELECTION = "/sd/openbuddy/active.json"

# --- Panel geometry ----------------------------------------------------------
# The 172px glass is centred in the controller's 240px RAM axis:
# (240 - 172) / 2 = 34. Without the offset the picture is shifted and wraps.
PANEL_SHORT = 172
PANEL_LONG = 320
RAM_SHORT_OFFSET = 34

# name: (MADCTL, width, height, x_offset, y_offset)
# MV (0x20) swaps the axes, so the 34px offset moves from X to Y in landscape.
ORIENTATIONS = {
    "portrait":      (0x00, 172, 320, 34, 0),   # USB-C at the bottom
    "landscape":     (0xA0, 320, 172, 0, 34),   # USB-C on the left
    "portrait_inv":  (0xC0, 172, 320, 34, 0),   # USB-C on top
    "landscape_inv": (0x60, 320, 172, 0, 34),   # USB-C on the right
}
ORDER = ("portrait", "landscape", "portrait_inv", "landscape_inv")
DEFAULT_ORIENTATION = "landscape"
