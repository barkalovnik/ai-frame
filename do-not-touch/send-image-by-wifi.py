#!/usr/bin/env python3
"""
Показ картинок из папки generated_images на Raspberry Pi Pico W по Wi-Fi (слайд-шоу).
Работает вместе с main.py на Pico (он показывает свой IP на экране).

Подготовка:  pip install pillow

Запуск:
    python send_images_wifi.py --host 192.168.1.57        # первый раз: IP с экрана Pico
    python send_images_wifi.py                            # дальше IP берётся из last_host.txt
    python send_images_wifi.py --interval 5 --shuffle     # менять картинку каждые 5 с, вперемешку
    python send_images_wifi.py --once                     # показать каждую один раз и выйти
    python send_images_wifi.py другая_папка --fit contain
Остановить: Ctrl+C.
"""
import argparse
import os
import random
import socket
import sys
import time
import zlib

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Нужна библиотека Pillow:  pip install pillow")

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
DEFAULT_FOLDER = "generated_images"
DEFAULT_PORT = 5050
HOST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_host.txt")


def list_images(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, n) for n in os.listdir(folder) if n.lower().endswith(IMAGE_EXT))


def to_rgb565(img, w, h, fit):
    """Картинка -> байты RGB565 (старший байт первым), как нужно дисплею ST7735."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    if fit == "stretch":
        img = img.resize((w, h), Image.LANCZOS)
    elif fit == "contain":
        img = ImageOps.contain(img, (w, h), Image.LANCZOS)
        canvas = Image.new("RGB", (w, h), (0, 0, 0))
        canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
        img = canvas
    else:
        img = ImageOps.fit(img, (w, h), Image.LANCZOS)
    px = img.tobytes()
    out = bytearray(w * h * 2)
    j = 0
    for i in range(0, len(px), 3):
        c = ((px[i] & 0xF8) << 8) | ((px[i + 1] & 0xFC) << 3) | (px[i + 2] >> 3)
        out[j] = c >> 8
        out[j + 1] = c & 0xFF
        j += 2
    return bytes(out)


def read_line(sock):
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("плата закрыла соединение")
        buf += chunk
    return buf.decode("utf-8", "replace").strip()


def send_one(host, port, path, fit, cache):
    """Одно соединение = одна картинка. Возвращает ответ платы ("OK" или "ERR ...")."""
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.settimeout(20)
        parts = read_line(sock).split()
        if len(parts) != 3 or parts[0] != "READY":
            raise ConnectionError("неожиданный ответ платы: %s" % " ".join(parts))
        w, h = int(parts[1]), int(parts[2])
        key = (path, w, h, fit)
        if key not in cache:
            with Image.open(path) as img:
                img.load()
                cache[key] = to_rgb565(img, w, h, fit)
        payload = cache[key]
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        sock.sendall(("IMG1 %d %d %08x\n" % (w, h, crc)).encode() + payload)
        return read_line(sock)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Показ картинок из папки на Pico W по Wi-Fi")
    parser.add_argument("folder", nargs="?", default=DEFAULT_FOLDER, help="папка с картинками (по умолчанию generated_images)")
    parser.add_argument("--host", help="IP-адрес Pico (виден на её экране); запоминается в last_host.txt")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interval", type=float, default=10, help="секунд на каждую картинку (по умолчанию 10)")
    parser.add_argument("--fit", choices=("cover", "contain", "stretch"), default="cover",
                        help="cover - заполнить экран с обрезкой, contain - вписать целиком, stretch - растянуть")
    parser.add_argument("--shuffle", action="store_true", help="показывать в случайном порядке")
    parser.add_argument("--once", action="store_true", help="показать каждую картинку один раз и выйти")
    args = parser.parse_args(argv)

    images = list_images(args.folder)
    if not images:
        print("В папке %s нет картинок (%s)." % (args.folder, ", ".join(IMAGE_EXT)))
        return 1

    host = args.host
    if not host and os.path.isfile(HOST_FILE):
        host = open(HOST_FILE, encoding="utf-8").read().strip()
    if not host:
        host = input("IP-адрес Pico (написан на её экране): ").strip()
    if not host:
        return 1

    print("Картинок: %d, плата: %s:%d, интервал %g с. Остановить: Ctrl+C" % (len(images), host, args.port, args.interval))
    cache = {}
    saved = False
    try:
        while True:
            order = images[:]
            if args.shuffle:
                random.shuffle(order)
            for n, path in enumerate(order):
                reply = None
                for attempt in range(1, 4):
                    try:
                        reply = send_one(host, args.port, path, args.fit, cache)
                    except (OSError, ConnectionError) as e:
                        print("  связи с платой нет (%s), попытка %d из 3" % (e, attempt))
                        time.sleep(3)
                        continue
                    if reply == "OK":
                        break
                    print("  плата ответила: %s, повтор" % reply)
                if reply != "OK":
                    print("Не удалось отправить %s. Проверь IP, Wi-Fi и что плата включена." % os.path.basename(path))
                    return 1
                if not saved:
                    with open(HOST_FILE, "w", encoding="utf-8") as f:
                        f.write(host)
                    saved = True
                print("[%d/%d] %s" % (n + 1, len(order), os.path.basename(path)))
                if not (args.once and n == len(order) - 1):
                    time.sleep(args.interval)
            if args.once:
                return 0
    except KeyboardInterrupt:
        print("\nОстановлено.")
        return 0


if __name__ == "__main__":
    sys.exit(main())