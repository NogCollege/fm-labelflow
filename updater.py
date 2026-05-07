"""
updater.py - Проверка и установка обновлений LabelFlow.

Манифест — JSON-файл по MANIFEST_URL следующего формата:
{
    "version": "1.0.1",
    "download_url": "https://example.com/releases/LabelFlow.exe",
    "release_notes": "Что нового в этой версии"
}

Обновление работает только в frozen-режиме (собранный exe).
При запуске из исходников проверка пропускается.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

logger = logging.getLogger(__name__)

# URL манифеста обновления — замените на свой хостинг
MANIFEST_URL = "https://stepso.ru/shared-files/stepteam/labelflow-updates.json"

# Таймаут запроса манифеста (сек) — короткий, чтобы не задерживать запуск
MANIFEST_TIMEOUT = 8


# ---------------------------------------------------------------------------
# Парсинг и сравнение версий
# ---------------------------------------------------------------------------

def _parse_version(v: str) -> tuple:
    """
    Парсит строку вида '1.2.3' или '1.2.3-beta2' в сортируемый кортеж.
    Стабильная версия (без суффикса) считается новее любой pre-release.
    """
    import re
    m = re.match(r'^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$', v.strip())
    if not m:
        return (0, 0, 0, "")
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre = m.group(4) or ""
    return (major, minor, patch, pre)


def _is_newer(remote: str, current: str) -> bool:
    """Возвращает True если remote строго новее current."""
    r = _parse_version(remote)
    c = _parse_version(current)
    # Сравниваем числовую часть
    if r[:3] != c[:3]:
        return r[:3] > c[:3]
    # Одинаковая числовая часть: stable ("") > любой pre-release
    if r[3] == c[3]:
        return False
    if r[3] == "":
        return True   # remote — stable, current — pre-release → newer
    if c[3] == "":
        return False  # remote — pre-release, current — stable → older
    return r[3] > c[3]  # оба pre-release — сравниваем строково (beta2 > beta1)


# ---------------------------------------------------------------------------
# Сетевые операции
# ---------------------------------------------------------------------------

def _fetch_manifest() -> dict | None:
    try:
        req = urllib.request.Request(
            MANIFEST_URL,
            headers={"User-Agent": "LabelFlow-Updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=MANIFEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Не удалось загрузить манифест обновления: %s", exc)
        return None


def _download_exe(url: str, dest: str) -> bool:
    """Скачивает новый exe в dest с отображением прогресса в логах."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "LabelFlow-Updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            chunk = 1024 * 256  # 256 KB
            with open(dest, "wb") as f:
                while True:
                    data = resp.read(chunk)
                    if not data:
                        break
                    f.write(data)
                    downloaded += len(data)
                    if total:
                        pct = downloaded * 100 // total
                        logger.info("Загрузка обновления: %d%% (%d / %d bytes)", pct, downloaded, total)
        return True
    except Exception as exc:
        logger.error("Ошибка загрузки обновления: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Самообновление (Windows)
# ---------------------------------------------------------------------------

def _schedule_self_replace(new_exe: str) -> None:
    """
    Запускает bat-скрипт, который после завершения текущего процесса:
      1. Ждёт пока процесс завершится
      2. Заменяет exe
      3. Запускает новую версию
    """
    current_exe = os.path.abspath(sys.executable)
    bat_path = os.path.join(tempfile.gettempdir(), "labelflow_update.bat")
    # Используем cp1251 чтобы кириллика в путях не ломала bat
    with open(bat_path, "w", encoding="cp1251") as f:
        f.write(
            "@echo off\n"
            "title LabelFlow Update\n"
            # Ждём завершения текущего процесса (3 попытки по 2 сек)
            "ping -n 4 127.0.0.1 >nul\n"
            f'copy /Y "{new_exe}" "{current_exe}" >nul\n'
            f'del /F /Q "{new_exe}" >nul\n'
            f'start "" "{current_exe}"\n'
            'del /F /Q "%~f0"\n'
        )
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )


# ---------------------------------------------------------------------------
# UI диалоги (tkinter)
# ---------------------------------------------------------------------------

def _show_update_dialog(current_version: str, remote_version: str, release_notes: str) -> bool:
    """Показывает диалог предложения обновления. Возвращает True если пользователь согласился."""
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        return False

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    notes_block = f"\n\nЧто нового:\n{release_notes}" if release_notes.strip() else ""
    answer = messagebox.askyesno(
        "Доступно обновление LabelFlow",
        f"Установлена версия:  {current_version}\n"
        f"Новая версия:         {remote_version}"
        f"{notes_block}\n\n"
        "Скачать и установить обновление?\n"
        "(Приложение перезапустится автоматически)",
        icon="info",
    )
    root.destroy()
    return bool(answer)


def _show_progress_window(label_text: str = "Загрузка обновления..."):
    """Создаёт маленькое окно прогресса. Возвращает (root, label, update_fn)."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return None, None, None

    root = tk.Tk()
    root.title("LabelFlow — Обновление")
    root.geometry("380x90")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg="#151515")

    lbl = tk.Label(root, text=label_text, fg="#FFFFFF", bg="#151515", font=("Segoe UI", 10))
    lbl.pack(pady=(14, 4))

    bar = ttk.Progressbar(root, mode="indeterminate", length=340)
    bar.pack(pady=4)
    bar.start(12)

    root.update()

    def update_label(text: str):
        lbl.config(text=text)
        root.update()

    return root, update_label


def _show_error(msg: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("LabelFlow — Ошибка обновления", msg)
        root.destroy()
    except Exception:
        logger.error("Ошибка обновления: %s", msg)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def check_and_apply(current_version: str) -> None:
    """
    Проверяет обновления и предлагает установить, если есть новая версия.
    Работает только в frozen-режиме (собранный exe).

    Вызывать из launcher.py ДО запуска Flask, в основном потоке.
    """
    if not getattr(sys, "frozen", False):
        logger.debug("Updater: запуск из исходников — пропуск проверки обновлений")
        return

    if not MANIFEST_URL.startswith("https://"):
        logger.warning("Updater: MANIFEST_URL не настроен — пропуск")
        return

    logger.info("Updater: проверка обновлений (текущая версия %s)...", current_version)
    manifest = _fetch_manifest()
    if not manifest:
        return

    remote_version = str(manifest.get("version") or "").strip()
    download_url = str(manifest.get("download_url") or "").strip()
    release_notes = str(manifest.get("release_notes") or "").strip()

    if not remote_version or not download_url:
        logger.warning("Updater: манифест не содержит version/download_url")
        return

    if not _is_newer(remote_version, current_version):
        logger.info("Updater: версия актуальна (%s)", current_version)
        return

    logger.info("Updater: доступно обновление %s → %s", current_version, remote_version)

    if not _show_update_dialog(current_version, remote_version, release_notes):
        logger.info("Updater: пользователь отказался от обновления")
        return

    # Скачиваем
    tmp_exe = os.path.join(tempfile.gettempdir(), "LabelFlow_update.exe")
    progress_root, update_label = _show_progress_window("Загрузка обновления...")

    logger.info("Updater: скачивание %s → %s", download_url, tmp_exe)
    ok = _download_exe(download_url, tmp_exe)

    if progress_root is not None:
        try:
            progress_root.destroy()
        except Exception:
            pass

    if not ok:
        _show_error("Не удалось загрузить обновление.\nПроверьте соединение и попробуйте позже.")
        return

    logger.info("Updater: обновление загружено, планирую замену exe и перезапуск")
    _schedule_self_replace(tmp_exe)
    sys.exit(0)
