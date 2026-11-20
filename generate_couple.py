#!/usr/bin/env python3
"""
Скрипт генерации новых сцен для фоторамки ai-frame:
1. Генерирует сцену через Pollinations.ai (FLUX.1).
2. Заменяет лицо парня (слева) на faces/me.jpg.
3. Заменяет лицо девушки (справа) на faces/her.jpg.
4. Бесшовно объединяет результат и сохраняет в generated_images/.
5. (Опционально) сразу отправляет картинку на Raspberry Pi Pico W по Wi-Fi.

Использование:
    python generate_couple.py                        # Случайный сеттинг из списка
    python generate_couple.py --prompt "викинги в заснеженных горах"
    python generate_couple.py --prompt "cyberpunk detectives in neon tokyo" --send
"""

import argparse
import datetime
import os
import random
import re
import socket
import sys
import time
import urllib.parse
import zlib

try:
    import requests
    from PIL import Image, ImageOps
    from gradio_client import Client, handle_file
except ImportError as e:
    sys.exit(f"Не установлена нужная библиотека: {e}\nУстановите: pip install requests pillow gradio_client")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FACES_DIR = os.path.join(BASE_DIR, "faces")
OUTPUT_DIR = os.path.join(BASE_DIR, "generated_images")
HOST_FILE = os.path.join(BASE_DIR, "do-not-touch", "last_host.txt")
DEFAULT_PORT = 5050

# Описание внешности (цвет волос, стрижка) для точной генерации под вас
DEFAULT_MAN_DESC = "a young handsome man on the left with short light brown hair"
DEFAULT_WOMAN_DESC = "a beautiful young woman on the right with blonde hair"

# Коллекция интересных сеттингов на случай запуска без аргументов
PRESET_SETTINGS = [
    ("cyberpunk_city", "cyberpunk pilots in neon-lit futuristic city street, night, volumetric neon light, highly detailed"),
    ("space_explorers", "astronaut explorers standing on surface of Mars near futuristic habitat dome, red planet mountains"),
    ("retro_80s", "retro 1980s synthwave style, stylish couple in vintage denim jackets near convertible car at sunset beach"),
    ("ghibli_picnic", "cozy sunny day picnic on a green grassy hill overlooking the ocean, warm studio ghibli anime atmosphere"),
    ("victorian_ball", "elegant 19th century victorian royal ballroom, luxurious historical clothing, warm chandelier light"),
    ("viking_warriors", "viking warrior man and shieldmaiden woman in warm furs, dramatic snowy norwegian mountains background"),
    ("steampunk_airship", "steampunk adventurers on deck of a giant brass flying airship among clouds, goggles and vintage gear"),
    ("magic_academy", "magicians wearing dark coats in ancient gothic magical academy library, glowing floating runes and books"),
    ("noir_detectives", "1940s film noir private detectives in trench coats on rain-slicked city street with lampposts and misty fog"),
    ("tropical_travel", "cheerful travelers on a sun-drenched tropical beach in Bali, turquoise ocean and palm trees"),
    ("kyoto_lanterns", "romantic evening stroll in traditional Kyoto alley, glowing paper lanterns, cherry blossom sakura petals"),
    ("medieval_castle", "medieval fantasy castle terrace overlooking green kingdom, royal garments, golden hour lighting"),
    ("campfire_forest", "cozy autumn camping near warm campfire in pine forest, starry night sky, woolen knit sweaters"),
    ("blade_runner", "futuristic retro-sci-fi rainy street scene, neon reflections in puddles, dramatic cinematic photography"),
]


def sanitize_filename(text):
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "_", text).strip("-_")
    return text[:40] if text else "scene"


def download_base_scene(setting_prompt, man_desc=DEFAULT_MAN_DESC, woman_desc=DEFAULT_WOMAN_DESC, width=512, height=512):
    """Генерация базовой сцены через Pollinations.ai (FLUX) с точным описанием внешности и волос"""
    full_prompt = (
        f"cinematic photo of a couple: {man_desc}, and {woman_desc}, "
        f"standing together, looking at camera, smiling, {setting_prompt}, "
        f"clear visible faces, 8k resolution, cinematic lighting, photorealistic masterpiece"
    )

    encoded = urllib.parse.quote(full_prompt)
    seed = random.randint(1, 9999999)
    url = f"https://image.pollinations.ai/prompt/{encoded}?model=flux&width={width}&height={height}&nologo=true&seed={seed}"

    print(f"[1/4] Генерация сцены через Pollinations.ai...")
    print(f"      Сеттинг: {setting_prompt}")
    
    resp = requests.get(url, timeout=90)
    if resp.status_code != 200:
        raise RuntimeError(f"Ошибка запроса к Pollinations: HTTP {resp.status_code}")

    temp_path = os.path.join(OUTPUT_DIR, f"_temp_base_{seed}.jpg")
    with open(temp_path, "wb") as f:
        f.write(resp.content)
    
    return temp_path


def swap_couple_faces(base_image_path, me_path, her_path):
    """
    Разделяет сцену на левую (парень) и правую (девушка) половины,
    производит замену лиц в HuggingFace Space и бесшовно объединяет результат.
    """
    print("[2/4] Подготовка и вырезка областей для замены лиц...")
    img = Image.open(base_image_path).convert("RGB")
    w, h = img.size

    # Левая часть (0..60% ширины) содержит лицо парня
    left_crop = img.crop((0, 0, int(w * 0.6), h))
    temp_left = os.path.join(OUTPUT_DIR, "_temp_left.jpg")
    left_crop.save(temp_left, quality=95)

    # Правая часть (40..100% ширины) содержит лицо девушки
    right_crop = img.crop((int(w * 0.4), 0, w, h))
    temp_right = os.path.join(OUTPUT_DIR, "_temp_right.jpg")
    right_crop.save(temp_right, quality=95)

    print("[3/4] Замена лиц через AI модель (Hugging Face Space)...")
    client = Client("tonyassi/face-swap")

    print("      -> Пересаживаем твое лицо (слева)...")
    res_left = client.predict(
        src_img=handle_file(me_path),
        dest_img=handle_file(temp_left),
        api_name="/swap_faces"
    )
    swapped_left = Image.open(res_left).convert("RGB")

    print("      -> Пересаживаем лицо девушки (справа)...")
    res_right = client.predict(
        src_img=handle_file(her_path),
        dest_img=handle_file(temp_right),
        api_name="/swap_faces"
    )
    swapped_right = Image.open(res_right).convert("RGB")

    # Бесшовное сведение двух половин
    print("      -> Сведение результата с мягким градиентом...")
    final = img.copy()

    # Левая зона без перекрытия
    final.paste(swapped_left.crop((0, 0, int(w * 0.4), h)), (0, 0))
    # Правая зона без перекрытия
    final.paste(swapped_right.crop((int(w * 0.2), 0, swapped_right.width, h)), (int(w * 0.6), 0))

    # Зона перекрытия (от 40% до 60% ширины) смешивается линейным градиентом
    overlap_w = int(w * 0.6) - int(w * 0.4)
    middle_left = swapped_left.crop((int(w * 0.4), 0, int(w * 0.6), h))
    middle_right = swapped_right.crop((0, 0, overlap_w, h))

    mask = Image.linear_gradient("L").resize((overlap_w, h)).rotate(270)
    blended_middle = Image.composite(middle_right, middle_left, mask)
    final.paste(blended_middle, (int(w * 0.4), 0))

    # Удаляем временные кропы
    for p in (temp_left, temp_right, base_image_path):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

    return final


# ---------------- Wi-Fi функции для отправки на Pico W ----------------

def to_rgb565(img, w, h, fit="cover"):
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


def send_to_pico(image_path, host, port=DEFAULT_PORT):
    print(f"[4/4] Отправка новой картинки на Pico W ({host}:{port})...")
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.settimeout(20)
        line = b""
        while not line.endswith(b"\n"):
            c = sock.recv(1)
            if not c:
                raise ConnectionError("плата закрыла соединение")
            line += c
        parts = line.decode("utf-8", "replace").strip().split()
        if len(parts) != 3 or parts[0] != "READY":
            raise ConnectionError(f"неожиданный ответ платы: {line}")
        w, h = int(parts[1]), int(parts[2])

        with Image.open(image_path) as img:
            payload = to_rgb565(img, w, h, fit="cover")
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        sock.sendall((f"IMG1 {w} {h} {crc:08x}\n").encode() + payload)
        
        reply = b""
        while not reply.endswith(b"\n"):
            c = sock.recv(1)
            if not c:
                break
            reply += c
        reply_str = reply.decode("utf-8", "replace").strip()
        print(f"      Ответ платы: {reply_str}")


def main():
    parser = argparse.ArgumentParser(description="Генерация сцены с заменой лиц для ai-frame")
    parser.add_argument("--prompt", help="Описание сеттинга (если не указано, выберется случайный)")
    parser.add_argument("--man-desc", default=DEFAULT_MAN_DESC, help="Описание внешности/волос парня")
    parser.add_argument("--woman-desc", default=DEFAULT_WOMAN_DESC, help="Описание внешности/волос девушки")
    parser.add_argument("--me", default=os.path.join(FACES_DIR, "me.jpg"), help="Путь к фото парня")
    parser.add_argument("--her", default=os.path.join(FACES_DIR, "her.jpg"), help="Путь к фото девушки")
    parser.add_argument("--send", action="store_true", help="Сразу отправить сгенерированное фото на экран Pico W")
    parser.add_argument("--host", help="IP платы Pico W (если не указан, берется из last_host.txt)")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.exists(args.me):
        sys.exit(f"Ошибка: не найден файл {args.me}")
    if not os.path.exists(args.her):
        sys.exit(f"Ошибка: не найден файл {args.her}")

    if args.prompt:
        slug = sanitize_filename(args.prompt)
        setting = args.prompt
    else:
        slug, setting = random.choice(PRESET_SETTINGS)

    # 1. Генерация базовой сцены
    base_temp = download_base_scene(setting, man_desc=args.man_desc, woman_desc=args.woman_desc)

    # 2. Замена лиц
    final_img = swap_couple_faces(base_temp, args.me, args.her)

    # 3. Сохранение
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_filename = f"{timestamp}_{slug}.jpg"
    out_path = os.path.join(OUTPUT_DIR, out_filename)
    final_img.save(out_path, quality=95)
    print(f"\n[ГОТОВО] Картинка успешно создана и сохранена:")
    print(f"         {out_path}")

    # 4. Отправка на экран при флаге --send
    if args.send:
        host = args.host
        if not host and os.path.exists(HOST_FILE):
            try:
                with open(HOST_FILE, "r", encoding="utf-8") as f:
                    host = f.read().strip()
            except Exception:
                pass
        if not host:
            print("[ПРЕДУПРЕЖДЕНИЕ] Не удалось определить IP для --send. Укажите через --host <IP>")
        else:
            try:
                send_to_pico(out_path, host)
            except Exception as e:
                print(f"[ОШИБКА ОТПРАВКИ] Не удалось передать на Pico W: {e}")


if __name__ == "__main__":
    main()
