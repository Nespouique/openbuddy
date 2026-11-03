"""One SPI controller, two chip selects, serialized by the firmware main loop."""

from machine import Pin, SPI

import board_config as cfg


class SharedSPI:
    def __init__(self):
        self.lcd_cs = Pin(cfg.LCD_CS, Pin.OUT, value=1)
        self.sd_cs = Pin(cfg.SD_CS, Pin.OUT, value=1)
        self.spi = SPI(cfg.LCD_SPI_ID, baudrate=cfg.LCD_SPI_BAUD, polarity=0, phase=0,
                       sck=Pin(cfg.LCD_SCLK), mosi=Pin(cfg.LCD_MOSI), miso=Pin(cfg.SD_MISO))
        self.baudrate = cfg.LCD_SPI_BAUD

    def configure(self, baudrate):
        self.lcd_cs(1)
        self.sd_cs(1)
        if self.baudrate != baudrate:
            self.spi.init(baudrate=baudrate, polarity=0, phase=0)
            self.baudrate = baudrate

    def for_lcd(self):
        self.configure(cfg.LCD_SPI_BAUD)

    def for_sd(self):
        self.configure(cfg.SD_SPI_BAUD)
