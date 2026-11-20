# test_solid.py - минимальный тест ST7735: сплошные цвета, SPI 1 МГц, без повторной настройки
import time
from machine import Pin, SPI

SCK, MOSI, DC, CS, RST = 18, 19, 16, 17, 20
W, H = 128, 160

spi = SPI(0, baudrate=1000000, polarity=0, phase=0, sck=Pin(SCK), mosi=Pin(MOSI))
dc = Pin(DC, Pin.OUT, value=0)
cs = Pin(CS, Pin.OUT, value=1)
rst = Pin(RST, Pin.OUT, value=1)


def cmd(c, data=b""):
    cs(0)
    dc(0)
    spi.write(bytes([c]))
    if data:
        dc(1)
        spi.write(data)
    cs(1)


# аппаратный сброс
rst(1); time.sleep_ms(50)
rst(0); time.sleep_ms(100)
rst(1); time.sleep_ms(150)

# минимальная инициализация
cmd(0x01); time.sleep_ms(150)          # программный сброс
cmd(0x11); time.sleep_ms(255)          # выход из сна
cmd(0x3A, b"\x05")                     # 16 бит на пиксель (RGB565)
cmd(0x36, b"\xC8")                     # ориентация портрет, порядок BGR
cmd(0x29); time.sleep_ms(100)          # включить дисплей


def fill(color):
    cmd(0x2A, bytes([0, 0, 0, W - 1]))
    cmd(0x2B, bytes([0, 0, 0, H - 1]))
    line = bytes([color >> 8, color & 255]) * W
    cs(0)
    dc(0)
    spi.write(b"\x2C")
    dc(1)
    for _ in range(H):
        spi.write(line)
    cs(1)


while True:
    for name, c in (("КРАСНЫЙ", 0xF800), ("ЗЕЛЁНЫЙ", 0x07E0), ("СИНИЙ", 0x001F),
                    ("БЕЛЫЙ", 0xFFFF), ("ЧЁРНЫЙ", 0x0000)):
        print(name)
        fill(c)
        time.sleep(2)