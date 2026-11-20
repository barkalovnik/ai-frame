#!/usr/bin/env python3
"""
Отправка картинки на Raspberry Pi Pico W по USB-кабелю (последовательный порт)
для вывода на TFT-дисплей. Работает вместе с serial_frame.py на Pico.

Подготовка (один раз):
    pip install pillow pyserial
    1. Загрузи на Pico файл serial_frame.py под именем main.py и перезапусти плату.
    2. ЗАКРОЙ Thonny: пока он подключён к плате, порт занят и скрипт не сможет его открыть.

Запуск:
    python send_image.py                        # самая свежая картинка из папки generated_images
    python send_image.py generated_images/robot.jpg
    python send_image.py cat.png --fit contain  # вписать целиком (с чёрными полями)
    python send_image.py --port COM5            # если плата не нашлась автоматически
    python send_image.py --list                 # показать доступные порты
"""
import argparse
import base64
import os
import re
import sys
import time
import zlib

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Нужна библиотека Pillow:  pip install pillow")

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
DEFAULT_FOLDER = "generated_images"
PICO_VID = 0x2E8A                 # идентификатор производителя Raspberry Pi
LINE_BYTES = 1024                 # символов base64 в одной строке


def import_serial():
    try:
        import serial
        from serial.tools import list_ports
        return serial, list_ports
    except ImportError:
        sys.exit("Нужна библиотека pyserial:  pip install pyserial")


def latest_image():
    """Самая свежая картинка из папки generated_images (рядом со скриптом или в текущей папке)."""
    folders = [os.path.join(os.path.dirname(os.path.abspath(__file__)), DEFAULT_FOLDER),
               os.path.join(os.getcwd(), DEFAULT_FOLDER)]
    best = None
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if name.lower().endswith(IMAGE_EXT) and (best is None or os.path.getmtime(path) > os.path.getmtime(best)):
                best = path
    return best


def list_ports_text():
    _, list_ports = import_serial()
    ports = list(list_ports.comports())
    if not ports:
        return "  (последовательных портов не найдено)"
    return "\n".join("  %s  %s%s" % (p.device, p.description, "   <- Raspberry Pi" if p.vid == PICO_VID else "")
                     for p in ports)


def find_port(requested):
    if requested:
        return requested
    _, list_ports = import_serial()
    pico = [p for p in list_ports.comports() if p.vid == PICO_VID]
    if len(pico) == 1:
        return pico[0].device
    if not pico:
        sys.exit("Плата Raspberry Pi Pico не найдена. Проверь USB-кабель (нужен кабель с данными, не только для зарядки).\n"
                 "Доступные порты:\n%s\nМожно указать порт вручную: --port COM5" % list_ports_text())
    sys.exit("Найдено несколько плат, укажи нужную: --port ИМЯ_ПОРТА\n%s" % list_ports_text())


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
    else:                                           # cover: заполнить экран, лишнее обрезать
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


def transfer(ser, payload, w, h, line_delay=0.001, timeout=20):
    """Отправляет кадр. Возвращает ответ Pico ("OK" или "ERR ...") или None, если ответа нет."""
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    ser.reset_input_buffer()
    ser.write(("IMG1 %d %d %08x\n" % (w, h, crc)).encode())
    data = base64.b64encode(payload)
    for i in range(0, len(data), LINE_BYTES):
        ser.write(data[i:i + LINE_BYTES] + b"\n")
        if line_delay:
            time.sleep(line_delay)
    ser.write(b"END\n")
    ser.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode("utf-8", "replace").strip()
        if line.startswith("OK") or line.startswith("ERR"):
            return line
    return None


def open_port(name):
    serial, _ = import_serial()
    return serial.Serial(name, baudrate=115200, timeout=1, write_timeout=10)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Отправить картинку на Pico по USB и показать на TFT")
    parser.add_argument("image", nargs="?", help="файл картинки (по умолчанию - самая свежая из generated_images)")
    parser.add_argument("--port", help="порт платы, например COM5 или /dev/ttyACM0 (по умолчанию - ищется сам)")
    parser.add_argument("--fit", choices=("cover", "contain", "stretch"), default="cover",
                        help="cover - заполнить экран с обрезкой (по умолчанию), contain - вписать целиком, stretch - растянуть")
    parser.add_argument("--width", type=int, default=128, help="ширина экрана (по умолчанию 128)")
    parser.add_argument("--height", type=int, default=160, help="высота экрана (по умолчанию 160)")
    parser.add_argument("--delay", type=float, default=0.001, help="пауза между строками данных, секунд")
    parser.add_argument("--list", action="store_true", help="показать доступные порты и выйти")
    args = parser.parse_args(argv)

    if args.list:
        print("Доступные порты:\n" + list_ports_text())
        return 0

    path = args.image or latest_image()
    if not path or not os.path.isfile(path):
        print("Картинка не найдена. Сначала создай её скриптом generate_image.py или укажи файл:  python send_image.py файл.jpg")
        return 1
    try:
        img = Image.open(path)
        img.load()
    except OSError as e:
        print("Не удалось открыть картинку: %s" % e)
        return 1
    print("Картинка: %s (%dx%d)" % (path, img.width, img.height))

    port = find_port(args.port)
    print("Порт: %s" % port)
    try:
        ser = open_port(port)
    except Exception as e:                                  # noqa: BLE001
        print("Не удалось открыть порт %s: %s" % (port, e))
        print("Закрой Thonny и другие программы, которые используют плату, и попробуй снова.")
        return 1

    w, h = args.width, args.height
    try:
        for attempt in range(1, 4):
            print("Отправляю %dx%d (%d КБ)..." % (w, h, w * h * 2 // 1024))
            payload = to_rgb565(img, w, h, args.fit)
            reply = transfer(ser, payload, w, h, args.delay)
            if reply is None:
                print("Pico не ответила. Проверь, что на плате запущен serial_frame.py (сохранён как main.py)")
                print("и что Thonny закрыт. Попробуй отключить и снова подключить плату.")
                return 1
            if reply == "OK":
                print("Готово: картинка на экране.")
                return 0
            m = re.match(r"ERR size (\d+)x(\d+)", reply)
            if m and (int(m.group(1)), int(m.group(2))) != (w, h):
                w, h = int(m.group(1)), int(m.group(2))
                print("Размер экрана на плате %dx%d, отправляю заново в этом размере." % (w, h))
                continue
            print("Pico сообщила об ошибке: %s. Повторяю (попытка %d из 3)..." % (reply, attempt))
        print("Не удалось передать картинку.")
        return 1
    finally:
        ser.close()


if __name__ == "__main__":
    sys.exit(main())