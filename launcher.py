"""
launcher.py - LabelFlow desktop entrypoint (Eel + Flask)
"""
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import json
import urllib.request
import zipfile
from datetime import datetime

import eel


def _setup_logging() -> None:
    """Настраивает логирование в файл с ротацией (макс. 10 файлов), как в warehouse-assistant."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    log_dir = os.path.join(base, "LabelFlow", "server-logs")
    try:
        os.makedirs(log_dir, exist_ok=True)

        # Ротация: оставляем не более 10 файлов
        existing = sorted(
            [
                os.path.join(log_dir, f)
                for f in os.listdir(log_dir)
                if f.startswith("launcher_") and f.endswith(".log")
            ],
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
        )
        while len(existing) >= 10:
            old = existing.pop(0)
            try:
                os.remove(old)
            except Exception:
                pass

        log_path = os.path.join(log_dir, f"launcher_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # Werkzeug слишком шумит в режиме ожидания — снижаем до WARNING
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
    except Exception:
        # Если логирование недоступно — продолжаем без него
        pass


_setup_logging()
logger = logging.getLogger(__name__)
logger.info("Bootstrap: launcher module imported")

try:
    from app import app, _cleanup_expired_editor_layouts, APP_VERSION, _start_heartbeat_watchdog
    logger.info("Import app: success (version=%s)", APP_VERSION)
except Exception as exc:
    logger.error("Import app failed: %r", exc)
    logger.error(traceback.format_exc())
    raise

try:
    import updater as _updater
except Exception as exc:
    logger.warning("Updater module not available: %r", exc)
    _updater = None


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run_flask(port: int) -> None:
    try:
        logger.info("Starting Flask on 127.0.0.1:%d", port)
        logging.getLogger("werkzeug").setLevel(logging.INFO)
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
        logger.info("Flask exited normally")
    except Exception as exc:
        logger.error("Flask crashed: %r", exc)
        logger.error(traceback.format_exc())
        raise


def _wait_for_port(host: str, port: int, timeout_sec: float = 20.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.15)
    return False


def _build_eel_webroot(target_url: str) -> str:
    webroot = os.path.join(tempfile.gettempdir(), "labelflow-eel-web")
    os.makedirs(webroot, exist_ok=True)
    index_html = os.path.join(webroot, "index.html")
    with open(index_html, "w", encoding="utf-8") as f:
        f.write(
            "<!doctype html>\n"
            "<html><head><meta charset='utf-8'>"
            "<meta http-equiv='refresh' content='0; url={url}'>"
            "<script>window.location.replace('{url}');</script>"
            "</head><body>Opening LabelFlow...</body></html>\n".format(url=target_url)
        )
    return webroot


def _find_chromium_executable() -> str:
    candidates = [
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise RuntimeError(
        "Не найден Edge/Chromium браузер. Установите Microsoft Edge или Google Chrome."
    )


def _get_app_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    app_dir = os.path.join(base, "LabelFlow")
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


def _download_file(url: str, dest_path: str, timeout_sec: int = 120) -> None:
    logger.info("Downloading: %s", url)
    with urllib.request.urlopen(url, timeout=timeout_sec) as response:
        if getattr(response, "status", 200) >= 400:
            raise RuntimeError(f"Download failed with status {response.status}: {url}")
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(response, f)
    logger.info("Downloaded to: %s", dest_path)


def _resolve_cft_download_url() -> str:
    # Use Google official endpoint with latest stable URLs.
    manifest_url = (
        "https://googlechromelabs.github.io/chrome-for-testing/"
        "last-known-good-versions-with-downloads.json"
    )
    logger.info("Fetching CFT manifest: %s", manifest_url)
    with urllib.request.urlopen(manifest_url, timeout=60) as response:
        if getattr(response, "status", 200) >= 400:
            raise RuntimeError(f"CFT manifest HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))

    channels = payload.get("channels") or {}
    stable = channels.get("Stable") or {}
    downloads = (stable.get("downloads") or {}).get("chrome") or []
    for item in downloads:
        if str(item.get("platform", "")).lower() == "win64" and item.get("url"):
            return str(item["url"])

    raise RuntimeError("Не найден URL Chrome for Testing (win64) в манифесте.")


def _ensure_portable_chromium() -> str:
    app_dir = _get_app_data_dir()
    chromium_root = os.path.join(app_dir, "chromium")
    chrome_exe = os.path.join(chromium_root, "chrome-win64", "chrome.exe")
    if os.path.isfile(chrome_exe):
        logger.info("Portable Chromium already exists: %s", chrome_exe)
        return chrome_exe

    os.makedirs(chromium_root, exist_ok=True)
    zip_path = os.path.join(chromium_root, "chrome-win64.zip")

    url = _resolve_cft_download_url()
    _download_file(url, zip_path, timeout_sec=300)

    logger.info("Extracting archive: %s", zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(chromium_root)
    logger.info("Extraction complete: %s", chromium_root)

    try:
        os.remove(zip_path)
    except OSError:
        pass

    if not os.path.isfile(chrome_exe):
        raise RuntimeError("Portable Chromium распакован, но chrome.exe не найден.")
    return chrome_exe


def _get_browser_executable() -> str:
    try:
        return _find_chromium_executable()
    except Exception as e:
        logger.warning("System Chromium browser not found: %r", e)
        logger.info("Attempting to download portable Chromium...")
        return _ensure_portable_chromium()


def main() -> None:
    logger.info("=" * 60)
    logger.info("Launcher start (version=%s)", APP_VERSION)

    # Проверка обновлений — до запуска Flask, в основном потоке
    if _updater is not None:
        try:
            _updater.check_and_apply(APP_VERSION)
        except Exception as exc:
            logger.warning("Updater error (non-fatal): %r", exc)

    _cleanup_expired_editor_layouts()
    logger.info("Cleanup expired editor layouts: done")

    env_port = os.environ.get("LABELFLOW_PORT")
    port = int(env_port) if env_port else _find_free_port()
    logger.info("Selected port: %d", port)

    server_thread = threading.Thread(target=_run_flask, args=(port,), daemon=True)
    server_thread.start()
    logger.info("Flask thread started")

    if not _wait_for_port("127.0.0.1", port, timeout_sec=20.0):
        logger.error("ERROR: Flask port is not reachable within timeout")
        raise RuntimeError(
            "Локальный сервер LabelFlow не стартовал за 20 секунд. "
            "Проверьте блокировку localhost/фаервол."
        )
    logger.info("Flask port is reachable")
    _start_heartbeat_watchdog()

    url = f"http://127.0.0.1:{port}/"
    logger.info("App URL: %s", url)
    eel.init(_build_eel_webroot(url))
    logger.info("Eel initialized")
    # Keep Eel runtime initialized, but launch browser shell ourselves
    # to enforce app-mode in Edge/Chromium only.
    eel.start(
        "index.html",
        mode=False,
        host="127.0.0.1",
        port=0,
        block=False,
        disable_cache=True,
    )
    logger.info("Eel web server started")

    browser_exe = _get_browser_executable()
    logger.info("Browser executable: %s", browser_exe)
    browser_args = [
        browser_exe,
        f"--app={url}",
        "--window-size=1366,900",
        "--kiosk",
        "--new-window",
        "--disable-dev-tools",
        "--disable-features=Translate,msAutofillServerCommunication",
        "--disable-session-crashed-bubble",
        "--no-first-run",
    ]
    logger.info("Launching browser args: %s", browser_args)
    browser_proc = subprocess.Popen(browser_args)
    logger.info("Browser process launched (PID=%d)", browser_proc.pid)

    logger.info("Waiting for browser window to close (PID=%d)...", browser_proc.pid)
    _launch_ts = time.time()
    browser_proc.wait()
    _elapsed = time.time() - _launch_ts

    if _elapsed < 5.0 and _wait_for_port("127.0.0.1", port, timeout_sec=0.5):
        # Браузер вышел мгновенно — делегировал окно уже запущенному процессу.
        # Flask всё ещё жив, браузер работает. Watchdog отследит конец пингов и завершит нас.
        logger.info(
            "Browser delegated to existing instance (elapsed=%.1fs) — watchdog will handle shutdown",
            _elapsed,
        )
        while True:
            time.sleep(60)  # watchdog вызовет os._exit() когда браузер закроется
    else:
        logger.info("Browser process exited (exit code=%d, elapsed=%.1fs)", browser_proc.returncode, _elapsed)
        sys.exit(0)


if __name__ == "__main__":
    main()
