"""
Скрипт для скачивания внешних библиотек для локального использования
"""
import os
import sys
import urllib.request
import zipfile
import shutil
from pathlib import Path

# Исправление кодировки для Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static" / "vendor"

def download_file(url: str, dest_path: Path):
    """Скачивает файл по URL"""
    print(f"Скачивание {url}...")
    os.makedirs(dest_path.parent, exist_ok=True)
    urllib.request.urlretrieve(url, dest_path)
    file_size = os.path.getsize(dest_path) / 1024  # Размер в KB
    print(f"[OK] Скачано: {dest_path} ({file_size:.1f} KB)")

def download_bootstrap():
    """Скачивает Bootstrap 5.3.0"""
    print("\n=== Скачивание Bootstrap 5.3.0 ===")
    
    # CSS
    css_url = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
    css_path = STATIC_DIR / "bootstrap" / "css" / "bootstrap.min.css"
    download_file(css_url, css_path)
    
    # JS Bundle
    js_url = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"
    js_path = STATIC_DIR / "bootstrap" / "js" / "bootstrap.bundle.min.js"
    download_file(js_url, js_path)
    
    print("[OK] Bootstrap загружен")

def download_fontawesome():
    """Скачивает Font Awesome 6.4.0"""
    print("\n=== Скачивание Font Awesome 6.4.0 ===")
    
    # Скачиваем CSS напрямую с jsDelivr (он содержит правильные пути)
    css_url = "https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/css/all.min.css"
    css_path = STATIC_DIR / "fontawesome" / "css" / "all.min.css"
    download_file(css_url, css_path)
    
    # Список шрифтов для скачивания (только woff2 - современный формат)
    webfonts = [
        "fa-solid-900.woff2",
        "fa-regular-400.woff2",
        "fa-brands-400.woff2",
        "fa-v4compatibility.woff2",
    ]
    
    print("Скачивание шрифтов Font Awesome...")
    base_url = "https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/webfonts/"
    
    for font in webfonts:
        try:
            font_url = base_url + font
            font_path = STATIC_DIR / "fontawesome" / "webfonts" / font
            download_file(font_url, font_path)
        except Exception as e:
            print(f"[WARNING] Не удалось скачать {font}: {e}")
            # Продолжаем загрузку остальных файлов
    
    # Обновляем пути в CSS файле
    css_file = STATIC_DIR / "fontawesome" / "css" / "all.min.css"
    if css_file.exists():
        with open(css_file, 'r', encoding='utf-8') as f:
            content = f.read()
        # Заменяем пути к шрифтам на относительные
        # jsDelivr использует ../webfonts/, нам нужно ../../fontawesome/webfonts/
        content = content.replace('../webfonts/', '../../fontawesome/webfonts/')
        with open(css_file, 'w', encoding='utf-8') as f:
            f.write(content)
        print("[OK] Пути к шрифтам обновлены в CSS")
    
    print("[OK] Font Awesome загружен")

def download_chartjs():
    """Скачивает Chart.js 4.4.0"""
    print("\n=== Скачивание Chart.js 4.4.0 ===")
    
    js_url = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
    js_path = STATIC_DIR / "chartjs" / "chart.umd.min.js"
    download_file(js_url, js_path)
    
    print("[OK] Chart.js загружен")

def main():
    """Основная функция"""
    print("Начало загрузки внешних библиотек...")
    print(f"Целевая директория: {STATIC_DIR}")
    
    try:
        download_bootstrap()
        download_fontawesome()
        download_chartjs()
        
        print("\n" + "="*60)
        print("[OK] Все библиотеки успешно загружены!")
        print("="*60)
    except Exception as e:
        print(f"\n[ERROR] Ошибка при загрузке: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())

