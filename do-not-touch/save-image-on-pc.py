#!/usr/bin/env python3
"""
Генератор картинок: просит у бесплатной нейросети (Pollinations, ключ не нужен)
нарисовать картинку по описанию и сохраняет её на компьютер.

Нужен только Python 3, дополнительные библиотеки не требуются.

Примеры:
    python generate_image.py "a friendly robot, watercolor"
    python generate_image.py "cat astronaut" --width 512 --height 640 --out pictures
    python generate_image.py "space station" --seed 42 --open
    python generate_image.py                      # спросит описание в консоли

Описание лучше писать по-английски: так нейросеть рисует точнее.
Бесплатный доступ ограничен: примерно один запрос в 15 секунд.
"""
import argparse
import datetime
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://image.pollinations.ai/prompt/"   # адрес бесплатного генератора
DEFAULT_MODEL = "flux"                              # flux - качество, turbo - быстрее
DEFAULT_OUT_DIR = "generated_images"


class GenerationError(Exception):
    """Не удалось получить картинку (текст ошибки показывается пользователю)."""


def detect_extension(data):
    """Определяет формат картинки по первым байтам. None - это не картинка."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def generate_image(prompt, width=768, height=768, model=DEFAULT_MODEL, seed=None,
                   timeout=120, retries=3, retry_wait=20, logo=False):
    """Запрашивает картинку у нейросети. Возвращает (байты картинки, seed)."""
    if not prompt.strip():
        raise GenerationError("описание пустое")
    if seed is None:
        seed = random.randrange(1000000)

    params = {"width": width, "height": height, "model": model, "seed": seed}
    if not logo:
        params["nologo"] = "true"
    url = API_URL + urllib.parse.quote(prompt.strip()) + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ImageScript/1.0"})

    last_error = "неизвестная ошибка"
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
            if detect_extension(data) is None:
                snippet = data[:200].decode("utf-8", "replace").strip()
                raise GenerationError("сервис вернул не картинку: %s" % (snippet or "пустой ответ"))
            return data, seed
        except urllib.error.HTTPError as e:
            last_error = "сервис ответил ошибкой HTTP %d" % e.code
            if e.code not in (429, 500, 502, 503, 504):
                raise GenerationError(last_error)
        except urllib.error.URLError as e:
            last_error = "нет связи с сервисом (%s)" % e.reason
        except TimeoutError:
            last_error = "сервис слишком долго не отвечает"
        if attempt < retries:
            print("  %s. Повтор через %d с (попытка %d из %d)..." % (last_error, retry_wait, attempt + 1, retries))
            time.sleep(retry_wait)
    raise GenerationError(last_error)


def make_filename(prompt, extension):
    slug = re.sub(r"\W+", "_", prompt.strip(), flags=re.UNICODE).strip("_")[:40] or "image"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return "%s_%s%s" % (stamp, slug, extension)


def save_image(data, out_dir, prompt):
    """Сохраняет картинку в папку out_dir. Возвращает путь к файлу."""
    extension = detect_extension(data) or ".jpg"
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, make_filename(prompt, extension))
    with open(path, "wb") as f:
        f.write(data)
    return os.path.abspath(path)


def open_file(path):
    """Открывает файл программой просмотра по умолчанию."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                      # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except OSError as e:
        print("Не удалось открыть картинку автоматически: %s" % e)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Сгенерировать картинку нейросетью и сохранить на компьютер")
    parser.add_argument("prompt", nargs="*", help="описание картинки (лучше по-английски)")
    parser.add_argument("--width", type=int, default=768, help="ширина, пикселей (по умолчанию 768)")
    parser.add_argument("--height", type=int, default=768, help="высота, пикселей (по умолчанию 768)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="модель: flux или turbo (по умолчанию flux)")
    parser.add_argument("--seed", type=int, default=None, help="число для повторяемого результата (по умолчанию случайное)")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help="папка для картинок (по умолчанию generated_images)")
    parser.add_argument("--open", action="store_true", help="открыть картинку после сохранения")
    parser.add_argument("--logo", action="store_true", help="не отключать водяной знак сервиса")
    parser.add_argument("--timeout", type=int, default=120, help="сколько секунд ждать ответ (по умолчанию 120)")
    parser.add_argument("--retries", type=int, default=3, help="число попыток при ошибках сервиса (по умолчанию 3)")
    parser.add_argument("--retry-wait", type=int, default=20, help="пауза между попытками, секунд (по умолчанию 20)")
    args = parser.parse_args(argv)

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        try:
            prompt = input("Что нарисовать? (лучше по-английски): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
    if not prompt:
        print("Описание не введено.")
        return 1
    if not (64 <= args.width <= 2048 and 64 <= args.height <= 2048):
        print("Ширина и высота должны быть от 64 до 2048 пикселей.")
        return 1

    print("Рисую: %s" % prompt)
    print("  размер %dx%d, модель %s. Обычно это занимает 5-30 секунд..." % (args.width, args.height, args.model))
    started = time.time()
    try:
        data, seed = generate_image(prompt, args.width, args.height, args.model, args.seed,
                                    args.timeout, args.retries, args.retry_wait, args.logo)
    except GenerationError as e:
        print("Ошибка: %s" % e)
        return 1
    except KeyboardInterrupt:
        print("\nОтменено.")
        return 1

    try:
        path = save_image(data, args.out, prompt)
    except OSError as e:
        print("Не удалось сохранить файл: %s" % e)
        return 1

    print("Готово за %.0f с." % (time.time() - started))
    print("  файл:  %s (%d КБ)" % (path, len(data) // 1024))
    print("  seed:  %d  (с тем же описанием и seed получится такая же картинка)" % seed)
    if args.open:
        open_file(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())