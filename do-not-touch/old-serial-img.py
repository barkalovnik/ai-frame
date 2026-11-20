


# Приём картинки по USB (последовательный порт) и вывод на TFT 1.8" 128x160 (ST7735).
# Работает вместе со скриптом send_image.py на компьютере.
#
# Загрузи на Pico как main.py и перезапусти плату. На экране появится "Waiting for image".
# Потом закрой Thonny (он занимает порт) и запусти на компьютере:  python send_image.py
#
# Подключение экрана (подписи на модуле -> Pico):
#   VCC, LED -> 3V3 (пин 36)   GND -> GND (пин 23)   CS -> GP17   RESET -> GP20
#   A0 -> GP16   SDA -> GP19   SCK -> GP18
#
# Протокол (текстовый, поэтому Ctrl+C и кнопка Stop в Thonny продолжают работать):
#   компьютер -> Pico:  "IMG1 <ширина> <высота> <crc32>"  затем строки base64  затем "END"
#   Pico -> компьютер:  "OK" или "ERR <причина>"
import sys, time, struct, binascii
from machine import Pin, SPI
import framebuf

# ---------------- ПИНЫ И НАСТРОЙКИ ЭКРАНА ----------------
SCK_PIN, MOSI_PIN = 18, 19
DC_PIN, CS_PIN, RST_PIN = 16, 17, 20
BL_PIN = None                    # None: LED подключён к 3V3
ROTATION = 0                     # 0 - портрет 128x160, 1/3 - ландшафт 160x128, 2 - портрет вверх ногами
BGR = True                       # красный и синий перепутаны - поставь False
INVERT = False                   # цвета как "негатив" - поставь True
COL_OFFSET = 0                   # полоса "мусора" по краю или сдвиг - попробуй 2 и 1
ROW_OFFSET = 0
SPI_BAUD = 20000000

_MADCTL_ROT = (0xC0, 0xA0, 0x00, 0x60)


class ST7735:
    def __init__(self, spi, dc, cs, rst, bl=None, rotation=0):
        self.spi = spi
        self.dc = Pin(dc, Pin.OUT, value=0)
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.rst = Pin(rst, Pin.OUT, value=1)
        self.bl = Pin(bl, Pin.OUT, value=1) if bl is not None else None
        self.rotation = rotation % 4
        if self.rotation % 2:
            self.width, self.height = 160, 128
        else:
            self.width, self.height = 128, 160
        self.buf = bytearray(self.width * self.height * 2)
        self.fb = framebuf.FrameBuffer(self.buf, self.width, self.height, framebuf.RGB565)
        self._init()

    def cmd(self, c, data=None):
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes([c]))
        if data:
            self.dc(1)
            self.spi.write(data)
        self.cs(1)

    def _init(self):
        self.rst(1)
        time.sleep_ms(50)
        self.rst(0)
        time.sleep_ms(50)
        self.rst(1)
        time.sleep_ms(150)
        self.cmd(0x01)
        time.sleep_ms(150)
        self.cmd(0x11)
        time.sleep_ms(255)
        self.cmd(0xB1, b"\x01\x2C\x2D")
        self.cmd(0xB2, b"\x01\x2C\x2D")
        self.cmd(0xB3, b"\x01\x2C\x2D\x01\x2C\x2D")
        self.cmd(0xB4, b"\x07")
        self.cmd(0xC0, b"\xA2\x02\x84")
        self.cmd(0xC1, b"\xC5")
        self.cmd(0xC2, b"\x0A\x00")
        self.cmd(0xC3, b"\x8A\x2A")
        self.cmd(0xC4, b"\x8A\xEE")
        self.cmd(0xC5, b"\x0E")
        self.cmd(0x21 if INVERT else 0x20)
        madctl = _MADCTL_ROT[self.rotation] | (0x08 if BGR else 0x00)
        self.cmd(0x36, bytes([madctl]))
        self.cmd(0x3A, b"\x05")
        self.cmd(0xE0, b"\x02\x1c\x07\x12\x37\x32\x29\x2d\x29\x25\x2B\x39\x00\x01\x03\x10")
        self.cmd(0xE1, b"\x03\x1d\x07\x06\x2E\x2C\x29\x2D\x2E\x2E\x37\x3F\x00\x00\x02\x10")
        self.cmd(0x13)
        time.sleep_ms(10)
        self.cmd(0x29)
        time.sleep_ms(100)

    def _window(self, x0, y0, x1, y1):
        if self.rotation % 2:
            xo, yo = ROW_OFFSET, COL_OFFSET
        else:
            xo, yo = COL_OFFSET, ROW_OFFSET
        self.cmd(0x2A, struct.pack(">HH", x0 + xo, x1 + xo))
        self.cmd(0x2B, struct.pack(">HH", y0 + yo, y1 + yo))

    def show(self):
        self._window(0, 0, self.width - 1, self.height - 1)
        self.cs(0)
        self.dc(0)
        self.spi.write(b"\x2C")
        self.dc(1)
        self.spi.write(self.buf)
        self.cs(1)


spi = SPI(0, baudrate=SPI_BAUD, polarity=0, phase=0, sck=Pin(SCK_PIN), mosi=Pin(MOSI_PIN))
d = ST7735(spi, DC_PIN, CS_PIN, RST_PIN, BL_PIN, ROTATION)
W, H = d.width, d.height
FRAME_BYTES = W * H * 2
frame = bytearray(FRAME_BYTES)           # сюда складываются принятые данные (экран не портится при ошибках)


def status(*lines):
    """Текст на чёрном экране (только латиница)."""
    d.fb.fill(0)
    y = H // 2 - len(lines) * 6
    for i, s in enumerate(lines):
        s = str(s)[:W // 8]
        d.fb.text(s, (W - len(s) * 8) // 2, y + i * 12, 0xFFFF if i else 0x60FC)
    d.show()


def parse_header(line):
    """'IMG1 128 160 1a2b3c4d' -> (128, 160, 0x1a2b3c4d) или None."""
    try:
        parts = line.split()
        return int(parts[1]), int(parts[2]), int(parts[3], 16)
    except (ValueError, IndexError):
        return None


def main():
    status("Waiting", "for image", "%dx%d" % (W, H))
    print("READY %dx%d" % (W, H))
    header = None            # заголовок текущей передачи
    pos = 0                  # сколько байт кадра уже принято
    while True:
        line = sys.stdin.readline().strip()
        if not line:
            continue

        if line.startswith("IMG1"):                      # начало новой передачи (прерывает старую)
            header = parse_header(line)
            pos = 0
            if header is None:
                print("ERR header")
            elif (header[0], header[1]) != (W, H):
                print("ERR size %dx%d" % (W, H))
                header = None
            continue

        if header is None:                               # данные вне передачи - игнорируем
            continue

        if line == "END":
            if pos != FRAME_BYTES:
                print("ERR length %d" % pos)
            elif (binascii.crc32(frame) & 0xFFFFFFFF) != header[2]:
                print("ERR crc")
            else:
                d.buf[:] = frame
                d.show()
                print("OK")
            header = None
            continue

        try:
            chunk = binascii.a2b_base64(line)
        except Exception:
            print("ERR data")
            header = None
            continue
        n = len(chunk)
        if pos + n > FRAME_BYTES:
            print("ERR too long")
            header = None
            continue
        frame[pos:pos + n] = chunk
        pos += n


main()