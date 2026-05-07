"""
app.py - LabelFlow Flask Server
"""
import logging
import os
import sys
import io
import base64
import json
import hashlib
import threading
import time
import uuid
import ctypes
from functools import wraps

logger = logging.getLogger(__name__)

APP_VERSION = "1.0.1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_resource_dir() -> str:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return BASE_DIR


def _get_data_dir() -> str:
    if getattr(sys, 'frozen', False):
        base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'LabelFlow')
    return BASE_DIR


def _mark_hidden_windows(path: str) -> None:
    if os.name != 'nt':
        return
    try:
        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


def _ensure_local_env_file(data_dir: str) -> str:
    os.makedirs(data_dir, exist_ok=True)
    env_path = os.path.join(data_dir, '.env')
    if not os.path.exists(env_path):
        # Шаблон содержит только комментарии — значения закомментированы,
        # чтобы не перетирать настройки из встроенного .env при сборке.
        # Пользователь может раскомментировать нужные строки для локального переопределения.
        template = (
            "# LabelFlow local settings\n"
            "# Раскомментируйте и заполните нужные строки для локального переопределения.\n"
            "# provider: none | openai | yandex | alice\n"
            "# LLM_PROVIDER=openai\n"
            "# LLM_API_KEY=\n"
            "# LLM_MODEL=gpt-4o-mini\n"
            "\n"
            "# External auth / protected API\n"
            "# AUTH_ENABLED=true\n"
            "# AUTH_DEFAULT_ENV=prod\n"
            "# AUTH_LOGIN_MODE=json\n"
            "# AUTH_USERNAME_FIELD=login\n"
            "# AUTH_PASSWORD_FIELD=password\n"
            "# AUTH_TOKEN_KEYS=access_token,token,jwt,id_token\n"
            "# AUTH_PROD_LOGIN_URL=https://etg.pxt.fmlogistic.ru/auth/auth/token\n"
            "# AUTH_PROD_API_BASE_URL=https://etg.pxt.fmlogistic.ru/\n"
            "# AUTH_TEST_LOGIN_URL=https://test.pxt.fmlogistic.ru/auth/auth/token\n"
            "# AUTH_TEST_API_BASE_URL=https://test.pxt.fmlogistic.ru/\n"
            "# PRINTX_TEMPLATE_METHOD=POST\n"
            "# PRINTX_TEMPLATE_ENDPOINT=/printing/templates\n"
            "# PRINTX_TEMPLATE_TYPE=zpl\n"
            "# PRINTX_TEMPLATE_CLASS=PrintTemplate\n"
            "# PRINTX_ACTIVITY_ID=\n"
            "# EXTERNAL_CONNECTION_TEST_PATH=api/health\n"
            "\n"
            "# Optional legacy alias:\n"
            "# OPENAI_API_KEY=\n"
        )
        try:
            with open(env_path, 'w', encoding='utf-8') as f:
                f.write(template)
        except Exception:
            pass
    _mark_hidden_windows(env_path)
    return env_path


DATA_DIR = _get_data_dir()

# Загружаем .env в порядке приоритета (последующий override предыдущий):
#   1. Рядом с project-папкой / в _MEIPASS (при сборке)
#   2. Рядом с exe-файлом (пользователь кладёт .env рядом с LabelFlow.exe)
#   3. %LOCALAPPDATA%\LabelFlow\.env (локальные переопределения пользователя)
try:
    from dotenv import load_dotenv
    _frozen = getattr(sys, 'frozen', False)

    # 1. Встроенный/проектный .env (без override — базовые значения)
    if _frozen and hasattr(sys, '_MEIPASS'):
        _base_env = os.path.join(sys._MEIPASS, '.env')
    else:
        _base_env = os.path.join(BASE_DIR, '.env')
    load_dotenv(_base_env, override=False)
    logger.info(".env base: %s (exists=%s)", _base_env, os.path.isfile(_base_env))

    # 2. Рядом с exe (только в frozen-режиме, override — пользователь может заменить ключи)
    if _frozen:
        _exe_env = os.path.join(os.path.dirname(sys.executable), '.env')
        if os.path.isfile(_exe_env) and _exe_env != _base_env:
            load_dotenv(_exe_env, override=True)
            logger.info(".env exe-dir: %s (loaded)", _exe_env)

    # 3. AppData — самый высокий приоритет
    local_env = _ensure_local_env_file(DATA_DIR)
    load_dotenv(local_env, override=True)
    logger.info(".env local: %s", local_env)
except ImportError:
    pass

# Встроенные ключи — используются если .env не найден или пустой
_BUILTIN = {
    'LLM_PROVIDER': 'yandex',
    'LLM_API_KEY': os.environ.get('LLM_API_KEY', ''),
    'YANDEX_PROJECT': os.environ.get('YANDEX_PROJECT', ''),
    'YANDEX_PROMPT_BLOCK_PARSER': os.environ.get('YANDEX_PROMPT_BLOCK_PARSER', ''),
    'YANDEX_PROMPT_LAYOUT_COMPOSER': os.environ.get('YANDEX_PROMPT_LAYOUT_COMPOSER', ''),
}
for _k, _v in _BUILTIN.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v

# Backward compatibility: allow OPENAI_API_KEY without explicit LLM_* settings
if not os.environ.get('LLM_API_KEY') and os.environ.get('OPENAI_API_KEY'):
    os.environ['LLM_API_KEY'] = os.environ['OPENAI_API_KEY']
if not os.environ.get('LLM_PROVIDER') and os.environ.get('LLM_API_KEY'):
    os.environ['LLM_PROVIDER'] = 'openai'

logger.info("LLM config: provider=%r, api_key=%s",
            os.environ.get('LLM_PROVIDER', 'not set'),
            'set' if os.environ.get('LLM_API_KEY') else 'EMPTY')

# Force UTF-8 stdout/stderr so emoji in submodules don't crash on Windows cp1251
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from urllib.parse import quote, urljoin
import requests
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from PIL import Image


def _get_downloads_dir() -> str:
    home = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    return os.path.join(home, 'Downloads')


RESOURCE_DIR = _get_resource_dir()

if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    from label_layout import (
        EtoileLabelLayout,
        get_available_sizes,
        PREDEFINED_SIZES,
        SYMBOL_ASSET_DIR,
    )
    logger.info("label_layout loaded")
except ImportError as e:
    logger.error("label_layout import failed: %s", e)
    sys.exit(1)

from block_parser import BlockParser, Block, BLOCK_TYPES
from layout_composer import LayoutComposer, LayoutVariant, _preset_variants
from label_layout import render_from_blocks
logger.info("block_parser / layout_composer loaded")

# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get('LABELFLOW_SECRET_KEY') or os.environ.get('SECRET_KEY') or uuid.uuid4().hex
_block_parser = BlockParser()
_layout_composer = LayoutComposer()

RENDER_SUPERSAMPLE = 4.0
PREVIEW_SUPERSAMPLE = 6.0

output_dir = os.path.join(DATA_DIR, 'output')
os.makedirs(output_dir, exist_ok=True)
editor_layout_dir = os.path.join(output_dir, 'editor-layouts')
os.makedirs(editor_layout_dir, exist_ok=True)
label_history_dir = os.path.join(output_dir, 'label-history')
os.makedirs(label_history_dir, exist_ok=True)
EDITOR_LAYOUT_TTL_SECONDS = 60 * 60
LABEL_HISTORY_MAX_ITEMS = 100
AUTH_SESSION_COOKIE_KEY = 'labelflow_auth_sid'
_auth_clients = {}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _split_csv_env(name: str, default: str = ''):
    raw = os.environ.get(name, default)
    return [item.strip() for item in str(raw).split(',') if item.strip()]


def _normalize_auth_environment_name(name: str | None) -> str:
    raw = str(name or '').strip().lower()
    aliases = {
        'prod': 'prod',
        'production': 'prod',
        'main': 'prod',
        'default': 'prod',
        'normal': 'prod',
        'обычная': 'prod',
        'обычный': 'prod',
        'test': 'test',
        'testing': 'test',
        'stage': 'test',
        'staging': 'test',
        'тест': 'test',
        'тестовая': 'test',
    }
    return aliases.get(raw, 'prod')


AUTH_CONFIG = {
    'enabled': _env_flag(
        'AUTH_ENABLED',
        default=bool(
            os.environ.get('AUTH_LOGIN_URL')
            or os.environ.get('AUTH_PROD_LOGIN_URL')
            or os.environ.get('AUTH_TEST_LOGIN_URL')
        ),
    ),
    'login_mode': os.environ.get('AUTH_LOGIN_MODE', 'json').strip().lower(),
    'username_field': os.environ.get('AUTH_USERNAME_FIELD', 'username').strip(),
    'password_field': os.environ.get('AUTH_PASSWORD_FIELD', 'password').strip(),
    'username_label': os.environ.get('AUTH_USERNAME_LABEL', 'Логин').strip(),
    'password_label': os.environ.get('AUTH_PASSWORD_LABEL', 'Пароль').strip(),
    'token_keys': _split_csv_env('AUTH_TOKEN_KEYS', 'access_token,accessToken,token,jwt,id_token,data.access_token,data.accessToken,data.token'),
    'token_prefix': os.environ.get('AUTH_TOKEN_PREFIX', 'Bearer').strip(),
    'verify_ssl': _env_flag('AUTH_VERIFY_SSL', default=True),
    'request_timeout_seconds': float(os.environ.get('AUTH_REQUEST_TIMEOUT_SECONDS', '20') or 20),
    'connection_test_path': os.environ.get('EXTERNAL_CONNECTION_TEST_PATH', 'auth/v1/profile').strip(),
    'profile_path': os.environ.get('AUTH_PROFILE_PATH', 'auth/v1/profile').strip(),
    'printx_template_method': os.environ.get('PRINTX_TEMPLATE_METHOD', 'POST').strip().upper() or 'POST',
    'printx_template_endpoint': os.environ.get('PRINTX_TEMPLATE_ENDPOINT', '/wh/v1/print-templates').strip() or '/wh/v1/print-templates',
    'printx_template_type': os.environ.get('PRINTX_TEMPLATE_TYPE', 'zpl').strip() or 'zpl',
    'printx_template_class': os.environ.get('PRINTX_TEMPLATE_CLASS', 'PrintTemplate').strip() or 'PrintTemplate',
    'printx_activity_id': os.environ.get('PRINTX_ACTIVITY_ID', '').strip(),
}

AUTH_ENVIRONMENTS = {
    'prod': {
        'key': 'prod',
        'label': os.environ.get('AUTH_PROD_LABEL', 'Обычная').strip() or 'Обычная',
        'login_url': os.environ.get(
            'AUTH_PROD_LOGIN_URL',
            os.environ.get('AUTH_LOGIN_URL', 'https://etg.pxt.fmlogistic.ru/auth/auth/token'),
        ).strip(),
        'api_base_url': os.environ.get(
            'AUTH_PROD_API_BASE_URL',
            os.environ.get('EXTERNAL_API_BASE_URL', 'https://etg.pxt.fmlogistic.ru/'),
        ).strip(),
    },
    'test': {
        'key': 'test',
        'label': os.environ.get('AUTH_TEST_LABEL', 'Тест').strip() or 'Тест',
        'login_url': os.environ.get(
            'AUTH_TEST_LOGIN_URL',
            'https://test.pxt.fmlogistic.ru/auth/auth/token',
        ).strip(),
        'api_base_url': os.environ.get(
            'AUTH_TEST_API_BASE_URL',
            'https://test.pxt.fmlogistic.ru/',
        ).strip(),
    },
}
AUTH_DEFAULT_ENVIRONMENT = _normalize_auth_environment_name(os.environ.get('AUTH_DEFAULT_ENV', 'prod'))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_anchor(value, default='auto'):
    if value is None:
        return default
    raw = str(value).strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {
        'left': 'bottom_left', 'right': 'bottom_right',
        'top': 'top_right', 'bottom': 'bottom_right', 'center': 'bottom_center',
        'top_left': 'top_left', 'topleft': 'top_left',
        'top_right': 'top_right', 'topright': 'top_right',
        'bottom_left': 'bottom_left', 'bottomleft': 'bottom_left',
        'bottom_right': 'bottom_right', 'bottomright': 'bottom_right',
        'bottom_center': 'bottom_center', 'bottomcenter': 'bottom_center',
        'auto': 'auto',
    }
    normalized = aliases.get(raw, raw)
    allowed = {'auto', 'top_left', 'top_right', 'bottom_left', 'bottom_center', 'bottom_right'}
    return normalized if normalized in allowed else default


def _normalize_size_id(size_id):
    if not size_id:
        return '46x46'
    size_id = str(size_id).strip()
    if size_id == '100x100':
        size_id = '99.5x99.5'
    # backward compatibility for legacy IDs
    if size_id == '40x100':
        size_id = '100x40'
    if size_id == '35x100':
        size_id = '100x35'
    if size_id not in PREDEFINED_SIZES:
        logger.warning("Size %s not found, using 46x46", size_id)
        return '46x46'
    return size_id


def _get_available_symbols():
    exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    result = []
    if not os.path.isdir(SYMBOL_ASSET_DIR):
        return result
    seen = set()
    for name in sorted(os.listdir(SYMBOL_ASSET_DIR)):
        base, ext = os.path.splitext(name)
        if ext.lower() not in exts or not base:
            continue
        stem = base.strip().lower()
        if stem in seen:
            continue
        seen.add(stem)
        if not os.path.isfile(os.path.join(SYMBOL_ASSET_DIR, name)):
            continue
        result.append({
            'id': stem,
            'label': base.strip(),
            'image_url': f'/assets/symbols/{quote(name)}',
        })
    return result


def _cleanup_expired_editor_layouts():
    if not os.path.isdir(editor_layout_dir):
        return

    now = time.time()
    for name in os.listdir(editor_layout_dir):
        if not name.lower().endswith('.json'):
            continue
        path = os.path.join(editor_layout_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            age_seconds = now - os.path.getmtime(path)
            if age_seconds > EDITOR_LAYOUT_TTL_SECONDS:
                os.remove(path)
        except OSError:
            continue


def _get_auth_sid() -> str:
    sid = session.get(AUTH_SESSION_COOKIE_KEY)
    if sid and sid in _auth_clients:
        return sid
    sid = uuid.uuid4().hex
    session[AUTH_SESSION_COOKIE_KEY] = sid
    return sid


def _get_auth_state(create: bool = False):
    sid = session.get(AUTH_SESSION_COOKIE_KEY)
    if sid and sid in _auth_clients:
        state = _auth_clients[sid]
        if not state.get('auth_environment'):
            state['auth_environment'] = AUTH_DEFAULT_ENVIRONMENT
        return state
    if not create:
        return None
    sid = _get_auth_sid()
    state = {
        'session': requests.Session(),
        'token': None,
        'username': None,
        'profile': None,
        'login_at': None,
        'last_status_code': None,
        'auth_environment': AUTH_DEFAULT_ENVIRONMENT,
    }
    _auth_clients[sid] = state
    return state


def _clear_auth_state():
    sid = session.pop(AUTH_SESSION_COOKIE_KEY, None)
    if not sid:
        return
    state = _auth_clients.pop(sid, None)
    if state and state.get('session'):
        try:
            state['session'].close()
        except Exception:
            pass


def _extract_token(payload, token_keys):
    if not payload:
        return None
    candidates = token_keys or AUTH_CONFIG['token_keys']
    for candidate in candidates:
        if '.' in candidate:
            current = payload
            ok = True
            for part in candidate.split('.'):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    ok = False
                    break
            if ok and isinstance(current, str) and current.strip():
                return current.strip()
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key in candidates:
                value = current.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return None


def _mask_token(token: str) -> str:
    if not token:
        return ''
    if len(token) <= 8:
        return '*' * len(token)
    return f'{token[:4]}...{token[-4:]}'


def _get_auth_environment_name(state=None) -> str:
    current_state = state if state is not None else _get_auth_state(create=False)
    name = current_state.get('auth_environment') if current_state else None
    normalized = _normalize_auth_environment_name(name or AUTH_DEFAULT_ENVIRONMENT)
    return normalized if normalized in AUTH_ENVIRONMENTS else AUTH_DEFAULT_ENVIRONMENT


def _get_auth_environment(state=None) -> dict:
    env_name = _get_auth_environment_name(state=state)
    env_config = AUTH_ENVIRONMENTS.get(env_name) or AUTH_ENVIRONMENTS[AUTH_DEFAULT_ENVIRONMENT]
    return {
        'key': env_name,
        'label': env_config.get('label') or env_name,
        'login_url': env_config.get('login_url', '').strip(),
        'api_base_url': env_config.get('api_base_url', '').strip(),
    }


def _auth_status_payload():
    state = _get_auth_state(create=False)
    environment = _get_auth_environment(state=state)
    is_authenticated = False
    token_masked = ''
    username = None
    profile = None
    if state:
        session_obj = state.get('session')
        has_cookies = bool(session_obj and session_obj.cookies)
        has_token = bool(state.get('token'))
        is_authenticated = has_token or has_cookies
        token_masked = _mask_token(state.get('token') or '')
        username = state.get('username')
        profile = state.get('profile')
    return {
        'success': True,
        'enabled': AUTH_CONFIG['enabled'],
        'authenticated': is_authenticated,
        'username': username,
        'token_masked': token_masked,
        'profile': profile,
        'auth_environment': environment['key'],
        'auth_environment_label': environment['label'],
        'login_url': environment['login_url'],
        'api_base_url_configured': bool(environment['api_base_url']),
        'connection_test_configured': bool(environment['api_base_url'] and AUTH_CONFIG['connection_test_path']),
    }


def _build_outbound_url(path_or_url: str) -> str:
    raw = str(path_or_url or '').strip()
    environment = _get_auth_environment()
    api_base_url = environment['api_base_url']
    if not raw:
        raise ValueError('endpoint не передан')
    if raw.startswith('http://') or raw.startswith('https://'):
        base = api_base_url
        if not base:
            raise ValueError('EXTERNAL_API_BASE_URL не настроен')
        if not raw.startswith(base):
            raise ValueError('endpoint вне разрешённого EXTERNAL_API_BASE_URL')
        return raw
    if not api_base_url:
        raise ValueError('EXTERNAL_API_BASE_URL не настроен')
    return urljoin(api_base_url.rstrip('/') + '/', raw.lstrip('/'))


def _perform_external_request(method='GET', endpoint='', headers=None, params=None, json_body=None, data_body=None):
    environment = _get_auth_environment()
    if not environment['api_base_url']:
        return {'success': False, 'error': 'EXTERNAL_API_BASE_URL не настроен'}, 400

    try:
        target_url = _build_outbound_url(endpoint)
    except ValueError as exc:
        return {'success': False, 'error': str(exc)}, 400

    state = _get_auth_state(create=False)
    if not state:
        return {'success': False, 'error': 'Сессия авторизации не найдена'}, 401

    outbound_headers = dict(headers or {})
    if state.get('token'):
        outbound_headers.setdefault('Authorization', f"{AUTH_CONFIG['token_prefix']} {state['token']}")

    try:
        response = state['session'].request(
            method=method,
            url=target_url,
            headers=outbound_headers,
            params=params,
            json=json_body,
            data=data_body if json_body is None else None,
            timeout=AUTH_CONFIG['request_timeout_seconds'],
            verify=AUTH_CONFIG['verify_ssl'],
        )
    except requests.RequestException as exc:
        return {'success': False, 'error': f'Ошибка внешнего запроса: {exc}'}, 502

    content_type = response.headers.get('Content-Type', '')
    result_payload = None
    text_payload = None
    normalized_content_type = content_type.lower()
    if 'application/json' in normalized_content_type or normalized_content_type.endswith('+json') or '+json;' in normalized_content_type:
        try:
            result_payload = response.json()
        except ValueError:
            text_payload = response.text
    else:
        text_payload = response.text

    result = {
        'success': response.ok,
        'status_code': response.status_code,
        'headers': {
            'Content-Type': content_type,
            'Allow': response.headers.get('Allow', ''),
        },
        'json': result_payload,
        'text': text_payload,
    }

    if not response.ok:
        error_message = f'Внешний сервис вернул HTTP {response.status_code}'
        allow_header = response.headers.get('Allow', '').strip()
        if allow_header:
            error_message += f' (Allow: {allow_header})'
        if isinstance(result_payload, dict):
            upstream_error = (
                result_payload.get('error')
                or result_payload.get('message')
                or result_payload.get('detail')
            )
            if not upstream_error and isinstance(result_payload.get('errors'), list):
                error_parts = []
                for item in result_payload.get('errors', [])[:3]:
                    if not isinstance(item, dict):
                        continue
                    detail = str(item.get('detail') or item.get('title') or '').strip()
                    pointer = ''
                    source = item.get('source')
                    if isinstance(source, dict):
                        pointer = str(source.get('pointer') or '').strip()
                    if detail and pointer:
                        error_parts.append(f'{detail} ({pointer})')
                    elif detail:
                        error_parts.append(detail)
                if error_parts:
                    upstream_error = '; '.join(error_parts)
            if upstream_error:
                error_message += f': {upstream_error}'
        elif text_payload:
            excerpt = str(text_payload).strip().replace('\r', ' ').replace('\n', ' ')
            if excerpt:
                error_message += f': {excerpt[:240]}'
        result['error'] = error_message

    return result, response.status_code


def _extract_profile(payload):
    if isinstance(payload, dict):
        data = payload.get('data')
        if isinstance(data, dict):
            return data
    return payload if isinstance(payload, dict) else None


def _safe_filename_part(value: str | None, default: str = 'user') -> str:
    normalized = ''.join(
        ch if ch.isalnum() or ch in ('-', '_', '.') else '_'
        for ch in str(value or '').strip()
    ).strip('._')
    return normalized[:80] or default


def _get_current_user_identity(state=None) -> dict:
    current_state = state if state is not None else _get_auth_state(create=False)
    profile = current_state.get('profile') if current_state else None
    username = (current_state.get('username') if current_state else None) or ''

    profile_id = ''
    email = ''
    display_name = ''
    if isinstance(profile, dict):
        profile_id = str(
            profile.get('id')
            or profile.get('userId')
            or profile.get('user_id')
            or profile.get('employeeId')
            or profile.get('employee_id')
            or ''
        ).strip()
        email = str(profile.get('email') or '').strip()
        display_name = str(
            profile.get('fullName')
            or profile.get('full_name')
            or profile.get('name')
            or profile.get('username')
            or username
            or email
            or profile_id
        ).strip()

    identity_raw = profile_id or email or username or display_name
    if not identity_raw:
        return {
            'user_key': None,
            'username': username or None,
            'display_name': display_name or username or None,
            'email': email or None,
            'profile_id': profile_id or None,
        }

    digest = hashlib.sha256(identity_raw.encode('utf-8')).hexdigest()[:16]
    key_base = _safe_filename_part(profile_id or username or email or display_name, default='user')
    return {
        'user_key': f'{key_base}_{digest}',
        'username': username or None,
        'display_name': display_name or username or None,
        'email': email or None,
        'profile_id': profile_id or None,
    }


def _get_label_history_path(user_key: str) -> str:
    safe_user_key = _safe_filename_part(user_key, default='user')
    return os.path.join(label_history_dir, f'{safe_user_key}.json')


def _read_label_history(user_key: str) -> list:
    path = _get_label_history_path(user_key)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _write_label_history(user_key: str, items: list) -> None:
    path = _get_label_history_path(user_key)
    trimmed = [item for item in items if isinstance(item, dict)][:LABEL_HISTORY_MAX_ITEMS]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def _build_label_history_list_item(entry: dict) -> dict:
    return {
        'id': entry.get('id'),
        'name': entry.get('name') or 'Без названия',
        'size_id': entry.get('size_id'),
        'created_at': entry.get('created_at'),
        'updated_at': entry.get('updated_at'),
        'source': entry.get('source'),
        'printx_template_id': entry.get('printx_template_id'),
        'printx_activity_id': entry.get('printx_activity_id'),
        'username': entry.get('username'),
        'display_name': entry.get('display_name'),
    }


def _sanitize_history_state(payload: dict) -> dict:
    return {
        'raw': payload.get('raw') or '',
        'sizeId': payload.get('sizeId') or '46x46',
        'qrpos': payload.get('qrpos') or 'bottom_right',
        'sympos': payload.get('sympos') or '',
        'qrSizeScale': payload.get('qrSizeScale') if isinstance(payload.get('qrSizeScale'), (int, float)) else 1,
        'symSizeScale': payload.get('symSizeScale') if isinstance(payload.get('symSizeScale'), (int, float)) else 1,
        'selSyms': payload.get('selSyms') if isinstance(payload.get('selSyms'), list) else [],
        'blocks': payload.get('blocks') if isinstance(payload.get('blocks'), list) else [],
        'variantConfigs': payload.get('variantConfigs') if isinstance(payload.get('variantConfigs'), list) else [],
        'variantsData': payload.get('variantsData') if isinstance(payload.get('variantsData'), list) else [],
        'selVar': int(payload.get('selVar') or 0),
        'printxActivityId': str(payload.get('printxActivityId') or '').strip(),
    }


def _load_remote_profile():
    if not AUTH_CONFIG['profile_path']:
        return {'success': False, 'error': 'AUTH_PROFILE_PATH не настроен'}, 400
    result, status_code = _perform_external_request(
        method='GET',
        endpoint=AUTH_CONFIG['profile_path'],
    )
    if status_code >= 400:
        return result, status_code
    state = _get_auth_state(create=False)
    if state:
        state['profile'] = _extract_profile(result.get('json'))
        if not state.get('username') and isinstance(state['profile'], dict):
            state['username'] = state['profile'].get('username') or state['profile'].get('email')
    result['profile'] = state.get('profile') if state else None
    return result, status_code


def _is_authenticated() -> bool:
    if not AUTH_CONFIG['enabled']:
        return True
    state = _get_auth_state(create=False)
    has_session = bool(state and state.get('session') and state['session'].cookies)
    has_token = bool(state and state.get('token'))
    return has_token or has_session


def _safe_next_path(raw_value: str | None, default: str = '/') -> str:
    raw = str(raw_value or '').strip()
    if not raw.startswith('/'):
        return default
    if raw.startswith('//') or raw.startswith('/\\'):
        return default
    return raw


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if _is_authenticated():
            return view(*args, **kwargs)
        return jsonify({'success': False, 'auth_required': True, 'error': 'Требуется авторизация'}), 401
    return wrapped


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
@app.route('/index.html')
def index():
    if AUTH_CONFIG['enabled'] and not _is_authenticated():
        return redirect(f"/login?next={quote(request.full_path if request.query_string else request.path, safe='/?=&')}")
    return send_from_directory(RESOURCE_DIR, 'index.html')


@app.route('/login')
@app.route('/login.html')
def login_page():
    if not AUTH_CONFIG['enabled']:
        return redirect(_safe_next_path(request.args.get('next'), default='/'))
    if AUTH_CONFIG['enabled'] and _is_authenticated():
        return redirect(_safe_next_path(request.args.get('next'), default='/'))
    return send_from_directory(RESOURCE_DIR, 'login.html')


@app.route('/assets/<path:filename>')
def assets(filename):
    return send_from_directory(os.path.join(RESOURCE_DIR, 'assets'), filename)


@app.route('/favicon.ico')
def favicon():
    icon_dir = os.path.join(RESOURCE_DIR, 'assets', 'icons')
    for filename in ('favicon.ico', 'app-32.png'):
        path = os.path.join(icon_dir, filename)
        if os.path.isfile(path):
            return send_from_directory(icon_dir, filename)
    return ('', 204)

@app.route('/example.txt')
def example_txt():
    return send_from_directory(RESOURCE_DIR, 'example.txt')


@app.route('/api/auth/config')
def api_auth_config():
    current_environment = _get_auth_environment()
    return jsonify({
        'success': True,
        'enabled': AUTH_CONFIG['enabled'],
        'login_url': current_environment['login_url'],
        'username_label': AUTH_CONFIG['username_label'],
        'password_label': AUTH_CONFIG['password_label'],
        'printx_template_method': AUTH_CONFIG['printx_template_method'],
        'printx_template_endpoint': AUTH_CONFIG['printx_template_endpoint'],
        'printx_template_type': AUTH_CONFIG['printx_template_type'],
        'printx_template_class': AUTH_CONFIG['printx_template_class'],
        'printx_activity_id': AUTH_CONFIG['printx_activity_id'],
        'auth_environment': current_environment['key'],
        'auth_environment_label': current_environment['label'],
        'auth_environments': [
            {
                'key': env['key'],
                'label': env['label'],
                'login_url': env['login_url'],
                'api_base_url': env['api_base_url'],
            }
            for env in AUTH_ENVIRONMENTS.values()
            if env.get('login_url') or env.get('api_base_url')
        ],
    })


@app.route('/api/auth/status')
def api_auth_status():
    return jsonify(_auth_status_payload())


@app.route('/api/auth/token', methods=['GET'])
@login_required
def api_auth_token_get():
    state = _get_auth_state(create=False) or {}
    return jsonify({
        'success': True,
        'token_masked': _mask_token(state.get('token') or ''),
        'has_token': bool(state.get('token')),
        'login_at': state.get('login_at'),
        'username': state.get('username'),
    })


@app.route('/api/auth/token', methods=['PUT'])
def api_auth_token_put():
    payload = request.get_json(force=True, silent=True) or {}
    token = str(payload.get('token', '')).strip()
    username = str(payload.get('username', '')).strip() or None
    if not token:
        return jsonify({'success': False, 'error': 'token не передан'}), 400

    state = _get_auth_state(create=True)
    state['token'] = token
    state['username'] = username
    state['login_at'] = int(time.time())
    state['profile'] = None
    return jsonify({
        'success': True,
        'authenticated': True,
        'token_masked': _mask_token(token),
        'username': username,
    })


@app.route('/api/auth/token', methods=['DELETE'])
def api_auth_token_delete():
    state = _get_auth_state(create=False)
    if state:
        state['token'] = None
        state['profile'] = None
        state['login_at'] = None
    return jsonify({'success': True, 'authenticated': False})


@app.route('/api/auth/profile', methods=['GET'])
@login_required
def api_auth_profile_get():
    state = _get_auth_state(create=False) or {}
    if not state.get('profile'):
        result, status_code = _load_remote_profile()
        return jsonify(result), status_code
    return jsonify({'success': True, 'profile': state.get('profile')})


@app.route('/api/auth/profile/refresh', methods=['POST'])
@login_required
def api_auth_profile_refresh():
    result, status_code = _load_remote_profile()
    return jsonify(result), status_code


@app.route('/api/auth/profile', methods=['DELETE'])
def api_auth_profile_delete():
    state = _get_auth_state(create=False)
    if state:
        state['profile'] = None
    return jsonify({'success': True, 'profile': None})


@app.route('/api/label-history', methods=['GET'])
@login_required
def api_label_history_list():
    state = _get_auth_state(create=False)
    user_info = _get_current_user_identity(state=state)
    if not user_info.get('user_key'):
        return jsonify({'success': False, 'error': 'Не удалось определить пользователя PrintX'}), 400

    items = _read_label_history(user_info['user_key'])
    return jsonify({
        'success': True,
        'items': [_build_label_history_list_item(item) for item in items],
        'user': user_info,
    })


@app.route('/api/label-history', methods=['POST'])
@login_required
def api_label_history_save():
    state = _get_auth_state(create=False)
    user_info = _get_current_user_identity(state=state)
    if not user_info.get('user_key'):
        return jsonify({'success': False, 'error': 'Не удалось определить пользователя PrintX'}), 400

    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'success': False, 'error': 'Некорректный JSON'}), 400

    state_payload = payload.get('state')
    if not isinstance(state_payload, dict):
        return jsonify({'success': False, 'error': 'state не передан'}), 400

    entry_id = ''.join(ch for ch in str(payload.get('id') or '').strip() if ch.isalnum() or ch in ('-', '_'))
    entry_name = str(payload.get('name') or '').strip() or 'Без названия'
    now_ts = int(time.time())

    entry = {
        'id': entry_id or uuid.uuid4().hex,
        'name': entry_name[:160],
        'size_id': state_payload.get('sizeId') or '46x46',
        'created_at': now_ts,
        'updated_at': now_ts,
        'source': str(payload.get('source') or 'manual').strip()[:40] or 'manual',
        'printx_template_id': str(payload.get('printx_template_id') or '').strip(),
        'printx_activity_id': str(payload.get('printx_activity_id') or state_payload.get('printxActivityId') or '').strip(),
        'username': user_info.get('username'),
        'display_name': user_info.get('display_name'),
        'email': user_info.get('email'),
        'profile_id': user_info.get('profile_id'),
        'state': _sanitize_history_state(state_payload),
    }

    items = _read_label_history(user_info['user_key'])
    existing_index = next((idx for idx, item in enumerate(items) if item.get('id') == entry['id']), None)
    if existing_index is not None:
        existing = items[existing_index]
        entry['created_at'] = existing.get('created_at') or now_ts
        items.pop(existing_index)
    items.insert(0, entry)
    _write_label_history(user_info['user_key'], items)

    return jsonify({
        'success': True,
        'item': _build_label_history_list_item(entry),
        'user': user_info,
    })


@app.route('/api/label-history/<entry_id>', methods=['GET'])
@login_required
def api_label_history_get(entry_id):
    state = _get_auth_state(create=False)
    user_info = _get_current_user_identity(state=state)
    if not user_info.get('user_key'):
        return jsonify({'success': False, 'error': 'Не удалось определить пользователя PrintX'}), 400

    safe_entry_id = ''.join(ch for ch in str(entry_id) if ch.isalnum() or ch in ('-', '_'))
    if not safe_entry_id:
        return jsonify({'success': False, 'error': 'Некорректный id'}), 400

    items = _read_label_history(user_info['user_key'])
    entry = next((item for item in items if item.get('id') == safe_entry_id), None)
    if not entry:
        return jsonify({'success': False, 'error': 'Запись истории не найдена'}), 404

    return jsonify({'success': True, 'item': entry, 'user': user_info})


@app.route('/api/label-history/<entry_id>', methods=['DELETE'])
@login_required
def api_label_history_delete(entry_id):
    state = _get_auth_state(create=False)
    user_info = _get_current_user_identity(state=state)
    if not user_info.get('user_key'):
        return jsonify({'success': False, 'error': 'Не удалось определить пользователя PrintX'}), 400

    safe_entry_id = ''.join(ch for ch in str(entry_id) if ch.isalnum() or ch in ('-', '_'))
    if not safe_entry_id:
        return jsonify({'success': False, 'error': 'Некорректный id'}), 400

    items = _read_label_history(user_info['user_key'])
    filtered = [item for item in items if item.get('id') != safe_entry_id]
    if len(filtered) == len(items):
        return jsonify({'success': True, 'deleted': False})
    _write_label_history(user_info['user_key'], filtered)
    return jsonify({'success': True, 'deleted': True})


@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    if not AUTH_CONFIG['enabled']:
        return jsonify({'success': False, 'error': 'Авторизация отключена'}), 400

    payload = request.get_json(force=True, silent=True) or {}
    username = str(payload.get('username', '')).strip()
    password = str(payload.get('password', '')).strip()
    requested_environment = _normalize_auth_environment_name(payload.get('environment'))
    if not username or not password:
        return jsonify({'success': False, 'error': 'Укажите логин и пароль'}), 400

    outbound_payload = {
        AUTH_CONFIG['username_field']: username,
        AUTH_CONFIG['password_field']: password,
    }
    state = _get_auth_state(create=True)
    state['auth_environment'] = requested_environment
    environment = _get_auth_environment(state=state)
    if not environment['login_url']:
        return jsonify({'success': False, 'error': 'AUTH login URL не настроен для выбранного контура'}), 400
    state['session'].cookies.clear()
    state['token'] = None
    state['username'] = None
    state['profile'] = None
    state['login_at'] = None
    state['session'].headers.update({'Accept': 'application/json, text/plain, */*'})
    request_kwargs = {
        'timeout': AUTH_CONFIG['request_timeout_seconds'],
        'verify': AUTH_CONFIG['verify_ssl'],
    }
    if AUTH_CONFIG['login_mode'] == 'form':
        request_kwargs['data'] = outbound_payload
    else:
        request_kwargs['json'] = outbound_payload

    try:
        response = state['session'].post(
            environment['login_url'],
            **request_kwargs,
        )
    except requests.RequestException as exc:
        return jsonify({'success': False, 'error': f'Не удалось выполнить login-запрос: {exc}'}), 502

    state['last_status_code'] = response.status_code
    if response.status_code >= 400:
        return jsonify({
            'success': False,
            'error': f'Сервис авторизации вернул HTTP {response.status_code}',
            'status_code': response.status_code,
        }), 401

    token = None
    response_json = None
    try:
        response_json = response.json()
        token = _extract_token(response_json, AUTH_CONFIG['token_keys'])
    except ValueError:
        response_json = None

    auth_header = response.headers.get('Authorization', '').strip()
    if not token and auth_header.lower().startswith('bearer '):
        token = auth_header.split(None, 1)[1].strip()

    state['token'] = token
    state['username'] = username
    state['login_at'] = int(time.time())

    has_cookie_session = bool(state['session'].cookies)
    if not token and not has_cookie_session:
        return jsonify({
            'success': False,
            'error': 'Логин выполнен, но токен или session cookies не найдены. Проверьте AUTH_TOKEN_KEYS.',
            'response_keys': sorted(response_json.keys()) if isinstance(response_json, dict) else [],
        }), 502

    profile_result, profile_status = _load_remote_profile()
    profile_payload = None
    if profile_status < 400:
        profile_payload = profile_result.get('profile')

    return jsonify({
        'success': True,
        'authenticated': True,
        'auth_environment': environment['key'],
        'auth_environment_label': environment['label'],
        'token_masked': _mask_token(token or ''),
        'has_cookie_session': has_cookie_session,
        'status_code': response.status_code,
        'profile_loaded': profile_status < 400,
        'profile': profile_payload,
    })


@app.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    _clear_auth_state()
    return jsonify({'success': True, 'authenticated': False})


@app.route('/api/external/request', methods=['POST'])
@login_required
def api_external_request():
    payload = request.get_json(force=True, silent=True) or {}
    method = str(payload.get('method', 'GET')).strip().upper()
    if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}:
        return jsonify({'success': False, 'error': 'Недопустимый HTTP method'}), 400

    endpoint = payload.get('endpoint') or payload.get('path')
    headers = payload.get('headers') if isinstance(payload.get('headers'), dict) else {}
    params = payload.get('params') if isinstance(payload.get('params'), dict) else None
    json_body = payload.get('json') if isinstance(payload.get('json'), (dict, list)) else None
    data_body = payload.get('data') if isinstance(payload.get('data'), (dict, list, str)) else None

    result, status_code = _perform_external_request(
        method=method,
        endpoint=endpoint,
        headers=headers,
        params=params,
        json_body=json_body,
        data_body=data_body,
    )
    return jsonify(result), status_code


@app.route('/api/external/test-connection', methods=['POST'])
@login_required
def api_external_test_connection():
    if not AUTH_CONFIG['connection_test_path']:
        return jsonify({'success': False, 'error': 'EXTERNAL_CONNECTION_TEST_PATH не настроен'}), 400

    payload = request.get_json(force=True, silent=True) or {}
    method = str(payload.get('method', 'GET')).strip().upper() or 'GET'
    result, status_code = _perform_external_request(
        method=method,
        endpoint=AUTH_CONFIG['connection_test_path'],
        params=payload.get('params') if isinstance(payload.get('params'), dict) else None,
        json_body=payload.get('json') if isinstance(payload.get('json'), (dict, list)) else None,
    )
    return jsonify(result), status_code


@app.route('/labelflow-editor/')
@app.route('/labelflow-editor/index.html')
def labelflow_editor():
    if AUTH_CONFIG['enabled'] and not _is_authenticated():
        return redirect(f"/login?next={quote(request.full_path if request.query_string else request.path, safe='/?=&')}")
    return send_from_directory(RESOURCE_DIR, 'labelflow-editor.html')


@app.route('/smoke-test')
@app.route('/smoke-test.html')
def smoke_test_page():
    if AUTH_CONFIG['enabled'] and not _is_authenticated():
        return redirect(f"/login?next={quote(request.full_path if request.query_string else request.path, safe='/?=&')}")
    response = send_from_directory(RESOURCE_DIR, 'smoke_test.html')
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/api/smoke-test', methods=['GET', 'POST'])
def api_smoke_test():
    if AUTH_CONFIG['enabled'] and not _is_authenticated():
        return jsonify({'success': False, 'auth_required': True, 'error': 'Требуется авторизация'}), 401
    import importlib
    import smoke_test
    smoke_test = importlib.reload(smoke_test)
    response = jsonify(smoke_test.run_smoke_tests(sys.modules[__name__]))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/output/<filename>')
def output_file(filename):
    return send_from_directory(output_dir, filename)


_last_heartbeat: float = time.time()
_HEARTBEAT_TIMEOUT = 10.0  # секунд без пинга → считаем браузер закрытым


@app.route('/api/heartbeat', methods=['POST'])
def api_heartbeat():
    """Браузер присылает пинг каждые 3 сек пока открыт. Используется для авто-завершения."""
    global _last_heartbeat
    _last_heartbeat = time.time()
    return ('', 204)


def _start_heartbeat_watchdog() -> None:
    """Запускает фоновый поток: если пинги прекратились — завершаем процесс."""
    def _watch():
        # Даём браузеру 15 сек на первый пинг после запуска
        time.sleep(15)
        while True:
            time.sleep(2)
            if time.time() - _last_heartbeat > _HEARTBEAT_TIMEOUT:
                logger.info("Heartbeat timeout — browser closed, shutting down")
                os._exit(0)

    threading.Thread(target=_watch, daemon=True, name="heartbeat-watchdog").start()
    logger.info("Heartbeat watchdog started (timeout=%.0fs)", _HEARTBEAT_TIMEOUT)


@app.route('/api/status')
@login_required
def api_status():
    return jsonify({
        'status': 'running',
        'version': APP_VERSION,
        'message': 'LabelFlow API работает',
        'available_sizes': get_available_sizes(),
        'parser_loaded': True,
    })


@app.route('/api/fix-png', methods=['POST'])
@login_required
def api_fix_png():
    """
    Принимает PNG в base64 (без data: префикса), проставляет DPI=600 и сохраняет в /output.
    Возвращает URL и имя файла.
    """
    data = request.get_json(force=True, silent=True) or {}
    b64 = data.get('png_base64') or ''
    size_id = str(data.get('size_id') or 'unknown').strip()
    if not b64:
        return jsonify({'success': False, 'error': 'png_base64 не передан'}), 400

    try:
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw))

        fname_safe = size_id.replace('/', '_').replace('\\', '_')
        filename = f"label_{fname_safe}.png"
        filepath = os.path.join(output_dir, filename)

        img.save(filepath, format='PNG', dpi=(600, 600), optimize=True)

        return jsonify({
            'success': True,
            'image_url': f"/output/{quote(filename)}",
            'filename': filename,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/symbols')
@login_required
def api_symbols():
    return jsonify({'success': True, 'symbols': _get_available_symbols()})


@app.route('/api/block-types')
@login_required
def api_block_types():
    return jsonify({'success': True, 'block_types': BLOCK_TYPES})


@app.route('/api/editor-layouts', methods=['POST'])
@login_required
def api_save_editor_layout():
    _cleanup_expired_editor_layouts()
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict) or not data:
        return jsonify({'success': False, 'error': 'layout JSON не передан'}), 400

    layout_id = uuid.uuid4().hex
    filename = f'{layout_id}.json'
    filepath = os.path.join(editor_layout_dir, filename)

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({
        'success': True,
        'layout_id': layout_id,
        'layout_url': f'/api/editor-layouts/{layout_id}',
        'download_url': f'/api/editor-layouts/{layout_id}',
        'expires_in_seconds': EDITOR_LAYOUT_TTL_SECONDS,
    })


@app.route('/api/editor-layouts/<layout_id>')
@login_required
def api_get_editor_layout(layout_id):
    _cleanup_expired_editor_layouts()
    safe_layout_id = ''.join(ch for ch in str(layout_id) if ch.isalnum() or ch in ('-', '_'))
    if not safe_layout_id:
        return jsonify({'success': False, 'error': 'Некорректный layout_id'}), 400

    filename = f'{safe_layout_id}.json'
    filepath = os.path.join(editor_layout_dir, filename)
    if not os.path.isfile(filepath):
        return jsonify({'success': False, 'error': 'Layout JSON не найден'}), 404

    return send_from_directory(editor_layout_dir, filename, mimetype='application/json')


@app.route('/api/editor-layouts/<layout_id>', methods=['DELETE', 'POST'])
@login_required
def api_delete_editor_layout(layout_id):
    _cleanup_expired_editor_layouts()
    safe_layout_id = ''.join(ch for ch in str(layout_id) if ch.isalnum() or ch in ('-', '_'))
    if not safe_layout_id:
        return jsonify({'success': False, 'error': 'Некорректный layout_id'}), 400

    filename = f'{safe_layout_id}.json'
    filepath = os.path.join(editor_layout_dir, filename)
    if not os.path.isfile(filepath):
        return jsonify({'success': True, 'deleted': False, 'reason': 'not_found'})

    try:
        os.remove(filepath)
    except OSError as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({'success': True, 'deleted': True})


@app.route('/api/get-variants', methods=['POST'])
@login_required
def api_get_variants():
    """Вернуть 4 варианта компоновки без рендеринга (для клиентского HTML-превью)."""
    logger.info("/api/get-variants вызван")
    try:
        data = request.get_json(force=True, silent=True) or {}
        size_id = _normalize_size_id(data.get('size_id', '46x46'))
        raw_blocks = data.get('blocks') or []
        blocks = [Block.from_dict(b) for b in raw_blocks if isinstance(b, dict)]
        graphic_symbols = data.get('graphic_symbols') or []
        if not isinstance(graphic_symbols, list):
            graphic_symbols = []
        qr_size_percent = int(data.get('qr_size_percent', 100) or 100)
        symbol_size_percent = int(data.get('symbol_size_percent', 100) or 100)
        variants_list = _layout_composer.compose(
            blocks,
            size_id,
            graphic_symbols=graphic_symbols,
            qr_size_percent=qr_size_percent,
            symbol_size_percent=symbol_size_percent,
        )
        if not variants_list:
            variants_list = _preset_variants(blocks)
        return jsonify({
            'success': True,
            'variants': [v.to_dict() for v in variants_list[:4]],
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/parse-blocks', methods=['POST'])
@login_required
def api_parse_blocks():
    """Разобрать текст на типизированные блоки."""
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get('text', '')).strip()
    size_id = _normalize_size_id(data.get('size_id', '46x46'))
    if not text:
        return jsonify({'error': 'Текст не передан', 'success': False}), 400
    size_info = PREDEFINED_SIZES[size_id]
    blocks = _block_parser.parse(
        text,
        size_mm=(float(size_info['width_mm']), float(size_info['height_mm']))
    )
    return jsonify({
        'success': True,
        'blocks': [b.to_dict() for b in blocks],
    })


@app.route('/api/generate', methods=['POST'])
@login_required
def api_generate():
    data = request.get_json(force=True, silent=True) or {}

    size_id = _normalize_size_id(data.get('size_id', '46x46'))
    preview_mode = bool(data.get('preview_mode', False))
    output_format = str(data.get('format', 'png')).strip().lower()
    if output_format not in {'png', 'svg'}:
        output_format = 'png'
    high_quality_png = bool(data.get('high_quality_png', False))

    graphic_symbols = data.get('graphic_symbols') or []
    if not isinstance(graphic_symbols, list):
        graphic_symbols = []
    symbol_positions = data.get('symbol_positions') or {}
    if not isinstance(symbol_positions, dict):
        symbol_positions = {}
    symbol_positions = {
        str(k).strip().lower(): _normalize_anchor(v, default='auto')
        for k, v in symbol_positions.items()
    }

    try:
        # ----------------------------------------------------------------
        # Supersample вычисляем один раз
        # ----------------------------------------------------------------
        keep_target_size = True
        if output_format == 'svg':
            supersample = 1.0
        elif preview_mode:
            supersample = PREVIEW_SUPERSAMPLE
        elif high_quality_png:
            max_side = max(PREDEFINED_SIZES[size_id]['width_px'], PREDEFINED_SIZES[size_id]['height_px'])
            supersample = max(RENDER_SUPERSAMPLE, min(8.0, 4200.0 / max(1, float(max_side))))
            keep_target_size = False
        else:
            supersample = RENDER_SUPERSAMPLE

        layout = EtoileLabelLayout(size_key=size_id, supersample=supersample, keep_target_size=keep_target_size)

        raw_blocks = data.get('blocks')
        if raw_blocks and isinstance(raw_blocks, list):
            blocks = [Block.from_dict(b) for b in raw_blocks if isinstance(b, dict)]
            variant_index = int(data.get('variant_index', 0))

            # Получаем варианты компоновки (может использовать LLM)
            variants_list = _layout_composer.compose(
                blocks,
                size_id,
                graphic_symbols=graphic_symbols,
            )
            if not variants_list:
                variants_list = _preset_variants(blocks)

            # Если запрошены все 4 варианта — рендерим все
            if data.get('all_variants'):
                rendered_variants = []
                _sym_pos_override = _normalize_anchor(data.get('symbols_position'), default=None) if data.get('symbols_position') else None
                for i, lv in enumerate(variants_list):
                    lv.qr_position = _normalize_anchor(
                        data.get('qr_position', lv.qr_position), default=lv.qr_position)
                    if _sym_pos_override:
                        lv.symbols_position = _sym_pos_override
                    result = render_from_blocks(layout, blocks, lv, graphic_symbols, symbol_positions, output_format)
                    rv = _build_variant_response(
                        result, layout, size_id, output_format,
                        high_quality_png, preview_mode, lv.qr_position,
                        lv.symbols_position, graphic_symbols, symbol_positions,
                        name=lv.name, variant_id=i + 1,
                    )
                    rendered_variants.append(rv)
                return jsonify({'success': True, 'variants': rendered_variants})

            # Одиночный вариант
            chosen = variants_list[variant_index % len(variants_list)]
            chosen.qr_position = _normalize_anchor(
                data.get('qr_position', chosen.qr_position), default=chosen.qr_position)
            if data.get('symbols_position'):
                _sp = _normalize_anchor(data.get('symbols_position'), default=None)
                if _sp:
                    chosen.symbols_position = _sp
            result = render_from_blocks(layout, blocks, chosen, graphic_symbols, symbol_positions, output_format)
            rv = _build_variant_response(
                result, layout, size_id, output_format,
                high_quality_png, preview_mode, chosen.qr_position,
                chosen.symbols_position, graphic_symbols, symbol_positions,
                name=chosen.name, variant_id=variant_index + 1,
            )
            return jsonify({'success': True, 'variants': [rv]})

        return jsonify({'error': 'Передайте blocks', 'success': False}), 400

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'success': False}), 500


def _build_variant_response(
    result, layout, size_id, output_format, high_quality_png,
    preview_mode, qr_position, symbols_position, graphic_symbols, symbol_positions,
    name=None, variant_id=1,
):
    """Упаковать результат рендеринга (PIL Image или SVG-строку) в dict для ответа."""
    is_svg = output_format == 'svg'
    image = None if is_svg else result
    svg_content = result if is_svg else None

    preview_data_url = None
    filename = None

    if preview_mode:
        if is_svg:
            svg_b64 = base64.b64encode(svg_content.encode('utf-8')).decode('ascii')
            preview_data_url = f"data:image/svg+xml;base64,{svg_b64}"
        else:
            buf = io.BytesIO()
            image.save(buf, format='PNG', dpi=(300, 300))
            preview_data_url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    else:
        filename = f"label_{size_id}.{output_format}"
        filepath = os.path.join(output_dir, filename)
        if is_svg:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(svg_content)
        else:
            dpi = 600 if high_quality_png else 300
            image.save(filepath, dpi=(dpi, dpi), optimize=True)

    size_info = PREDEFINED_SIZES[size_id]
    return {
        'id': variant_id,
        'name': name or f"{size_info['width_mm']}x{size_info['height_mm']} мм",
        'size': f"{size_info['width_mm']} x {size_info['height_mm']} мм",
        'size_id': size_id,
        'width_mm': size_info['width_mm'],
        'height_mm': size_info['height_mm'],
        'width_px': size_info['width_px'],
        'height_px': size_info['height_px'],
        'actual_width_px': image.size[0] if image is not None else layout.target_width_px,
        'actual_height_px': image.size[1] if image is not None else layout.target_height_px,
        'image_url': f"/output/{quote(filename)}" if filename else None,
        'image_data_url': preview_data_url,
        'filename': filename,
        'format': output_format,
        'quality': 'hd' if (output_format == 'png' and high_quality_png) else 'standard',
        'qr_position': qr_position,
        'symbols_position': symbols_position,
        'graphic_symbols': graphic_symbols,
        'symbol_positions': symbol_positions,
    }


if __name__ == '__main__':
    _cleanup_expired_editor_layouts()
    print("=" * 60)
    print("LabelFlow v3.0 (Flask)")
    print(f"Dir: {BASE_DIR}")
    print("http://localhost:8000")
    print("=" * 60)
    app.run(host='0.0.0.0', port=8000, debug=False)
