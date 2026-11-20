# Приём картинок по Wi-Fi и вывод на TFT 1.8" 128x160 (ST7735). Raspberry Pi Pico W.
# Работает вместе со скриптом send_images_wifi.py на компьютере.
import time, struct, binascii, socket, gc
import network
from machine import Pin, SPI
import framebuf

# ---------------- Wi-Fi (только сеть 2,4 ГГц) ----------------
WIFI_SSID = "A26 пользователя н"
WIFI_PASSWORD = "12345678"
PORT = 5050

# ---------------- ПИНЫ И НАСТРОЙКИ ЭКРАНА ----------------
SCK_PIN, MOSI_PIN = 18, 19
DC_PIN, CS_PIN, RST_PIN = 16, 17, 20     # подставь те DC и CS, с которыми заработал тест
ROTATION = 0
BGR = True
INVERT = False
COL_OFFSET = 0
ROW_OFFSET = 0
SPI_BAUD = 20000000                      # если на экране снова шум - поставь 8000000

_MADCTL_ROT = (0xC0, 0xA0, 0x00, 0x60)


class ST7735:
    def __init__(self, spi, dc, cs, rst, rotation=0):
        self.spi = spi
        self.dc = Pin(dc, Pin.OUT, value=0)
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.rst = Pin(rst, Pin.OUT, value=1)
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
        self.rst(1); time.sleep_ms(50)
        self.rst(0); time.sleep_ms(50)
        self.rst(1); time.sleep_ms(150)
        self.cmd(0x01); time.sleep_ms(150)
        self.cmd(0x11); time.sleep_ms(255)
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
        self.cmd(0x36, bytes([_MADCTL_ROT[self.rotation] | (0x08 if BGR else 0x00)]))
        self.cmd(0x3A, b"\x05")
        self.cmd(0xE0, b"\x02\x1c\x07\x12\x37\x32\x29\x2d\x29\x25\x2B\x39\x00\x01\x03\x10")
        self.cmd(0xE1, b"\x03\x1d\x07\x06\x2E\x2C\x29\x2D\x2E\x2E\x37\x3F\x00\x00\x02\x10")
        self.cmd(0x13); time.sleep_ms(10)
        self.cmd(0x29); time.sleep_ms(100)

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
d = ST7735(spi, DC_PIN, CS_PIN, RST_PIN, ROTATION)
W, H = d.width, d.height
FRAME_BYTES = W * H * 2
frame = bytearray(FRAME_BYTES)            # сюда принимается кадр (экран не портится при ошибках)


def status(*lines):
    """Текст на чёрном экране (только латиница)."""
    d.fb.fill(0)
    y = H // 2 - len(lines) * 6
    for i, s in enumerate(lines):
        s = str(s)[:W // 8]
        d.fb.text(s, (W - len(s) * 8) // 2, y + i * 12, 0xFFFF if i else 0x60FC)
    d.show()


def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.config(pm=0xa11140)              # отключить энергосбережение Wi-Fi (меньше задержек)
    while not wlan.isconnected():
        status("Wi-Fi", "connecting...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        t = time.ticks_ms()
        while not wlan.isconnected() and time.ticks_diff(time.ticks_ms(), t) < 20000:
            time.sleep_ms(200)
        if not wlan.isconnected():
            status("Wi-Fi", "FAILED", "retry...")
            time.sleep(3)
    return wlan


def parse_header(line):
    """'IMG1 128 160 1a2b3c4d' -> (128, 160, 0x1a2b3c4d) или None."""
    try:
        parts = line.split()
        if parts[0] != "IMG1":
            return None
        return int(parts[1]), int(parts[2]), int(parts[3], 16)
    except (ValueError, IndexError):
        return None


def serve(conn):
    """Одно соединение = одна картинка."""
    conn.settimeout(10)
    conn.write(("READY %d %d\n" % (W, H)).encode())
    line = conn.readline()
    if not line:
        return
    header = parse_header(line.decode().strip())
    if header is None:
        conn.write(b"ERR header\n")
        return
    if (header[0], header[1]) != (W, H):
        conn.write(("ERR size %dx%d\n" % (W, H)).encode())
        return
    mv = memoryview(frame)
    pos = 0
    while pos < FRAME_BYTES:
        n = conn.readinto(mv[pos:])
        if not n:
            return                        # PC оборвал передачу
        pos += n
    if (binascii.crc32(frame) & 0xFFFFFFFF) != header[2]:
        conn.write(b"ERR crc\n")
        return
    d.buf[:] = frame
    d.show()
    conn.write(b"OK\n")


def main():
    wlan = wifi_connect()
    ip = wlan.ifconfig()[0]
    print("IP %s port %d" % (ip, PORT))
    status("Waiting", "IP:", ip, "port %d" % PORT)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(1)
    srv.settimeout(5)
    while True:
        try:
            conn, addr = srv.accept()
        except OSError:                   # просто истёк таймаут ожидания
            if not wlan.isconnected():
                wlan = wifi_connect()
                ip = wlan.ifconfig()[0]
                print("IP %s port %d" % (ip, PORT))
            continue
        try:
            serve(conn)
        except OSError as e:
            print("conn error:", e)
        finally:
            conn.close()
            gc.collect()


main()
