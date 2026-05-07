import base64
import io
import json
import os
import tempfile
import time
import traceback
from datetime import datetime, timezone

import requests
from PIL import Image

import block_parser
import layout_composer
from llm_client import HeuristicClient


class SmokeFailure(Exception):
    def __init__(self, message, stop_stage="check", actual_status=None, response_excerpt=None):
        super().__init__(message)
        self.stop_stage = stop_stage
        self.actual_status = actual_status
        self.response_excerpt = response_excerpt


class SmokeSkip(Exception):
    pass


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers or {}

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        if self._json_data is None:
            raise ValueError("No JSON payload")
        return self._json_data


class _FakeCookies(dict):
    def clear(self):
        super().clear()


class _FakeSession:
    def __init__(self):
        self.cookies = _FakeCookies()
        self.headers = {}

    def close(self):
        return None

    def post(self, url, json=None, data=None, timeout=None, verify=None):
        payload = json if json is not None else data or {}
        username = (
            payload.get("username")
            or payload.get("login")
            or payload.get("user")
            or "smoke-user"
        )

        if username == "http-error":
            return _FakeResponse(
                status_code=503,
                json_data={"error": "upstream unavailable"},
                headers={"Content-Type": "application/json"},
            )

        if username == "no-token":
            self.cookies.clear()
            return _FakeResponse(
                status_code=200,
                json_data={"ok": True},
                headers={"Content-Type": "application/json"},
            )

        if username == "header-token":
            self.cookies.clear()
            return _FakeResponse(
                status_code=200,
                json_data={"ok": True},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer smoke-header-token",
                },
            )

        if username == "cookie-only":
            self.cookies["smoke-session"] = "cookie-ok"
            return _FakeResponse(
                status_code=200,
                json_data={"ok": True},
                headers={"Content-Type": "application/json"},
            )

        self.cookies["smoke-session"] = "ok"
        return _FakeResponse(
            status_code=200,
            json_data={"access_token": "smoke-access-token", "username": username},
            headers={"Content-Type": "application/json"},
        )

    def request(self, method, url, headers=None, params=None, json=None, data=None, timeout=None, verify=None):
        if "request-error" in url:
            raise requests.RequestException("Smoke external failure")

        if url.rstrip("/").endswith("text-response"):
            return _FakeResponse(
                status_code=200,
                text="plain-text-response",
                headers={"Content-Type": "text/plain"},
            )

        if url.rstrip("/").endswith("auth/v1/profile"):
            return _FakeResponse(
                status_code=200,
                json_data={
                    "data": {
                        "username": "smoke-user",
                        "email": "smoke@example.test",
                        "role": "tester",
                    }
                },
                headers={"Content-Type": "application/json"},
            )

        payload = {
            "method": str(method).upper(),
            "url": url,
            "headers": headers or {},
            "params": params,
            "json": json,
            "data": data,
        }
        return _FakeResponse(
            status_code=200,
            json_data={"echo": payload, "ok": True},
            headers={"Content-Type": "application/json"},
        )


def _sample_text():
    return "\n".join(
        [
            "Hair shampoo",
            "Elegance line",
            "Volume and shine",
            "- floral extract",
            "- amino acids",
            "Use on wet hair",
            "Art.313756",
        ]
    )


def _body_excerpt(response):
    try:
        raw = response.get_data(as_text=True)
    except Exception:
        return None
    raw = str(raw or "").strip()
    return raw[:500] if raw else None


def _json_excerpt(payload):
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)[:700]
    except Exception:
        return str(payload)[:700]


def _status_list(expected):
    if isinstance(expected, (list, tuple, set)):
        return sorted(expected)
    return [expected]


def _fail(message, stop_stage="check", actual_status=None, response_excerpt=None):
    raise SmokeFailure(
        message=message,
        stop_stage=stop_stage,
        actual_status=actual_status,
        response_excerpt=response_excerpt,
    )


def _skip(message):
    raise SmokeSkip(message)


def _expect(condition, message, stop_stage="check"):
    if not condition:
        _fail(message, stop_stage=stop_stage)


def _expect_status(response, expected, label):
    expected_list = _status_list(expected)
    if response.status_code not in expected_list:
        _fail(
            f"{label}: получен HTTP {response.status_code}, ожидался {', '.join(map(str, expected_list))}",
            stop_stage="http_status",
            actual_status=response.status_code,
            response_excerpt=_body_excerpt(response),
        )


def _json_response(response, label):
    body = response.get_data()
    if not body:
        _fail(
            f"{label}: ответ пустой",
            stop_stage="empty_response",
            actual_status=response.status_code,
        )

    content_type = response.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        _fail(
            f"{label}: ожидался JSON, пришел {content_type or 'unknown'}",
            stop_stage="response_format",
            actual_status=response.status_code,
            response_excerpt=_body_excerpt(response),
        )

    payload = response.get_json(silent=True)
    if payload is None:
        _fail(
            f"{label}: JSON не разобрался",
            stop_stage="response_format",
            actual_status=response.status_code,
            response_excerpt=_body_excerpt(response),
        )
    return payload


def _ok(message, actual_status=None, details=None, response_excerpt=None, artifacts=None):
    return {
        "message": message,
        "actual_status": actual_status,
        "details": details or [],
        "response_excerpt": response_excerpt,
        "artifacts": artifacts or [],
    }


def run_smoke_tests(app_module):
    started_at = time.perf_counter()
    generated_at = datetime.now(timezone.utc).isoformat()
    results = []

    temp_root = tempfile.TemporaryDirectory(prefix="labelflow-smoke-")
    temp_output_dir = os.path.join(temp_root.name, "output")
    temp_editor_dir = os.path.join(temp_output_dir, "editor-layouts")
    os.makedirs(temp_editor_dir, exist_ok=True)

    original_output_dir = app_module.output_dir
    original_editor_layout_dir = app_module.editor_layout_dir
    original_auth_config = dict(app_module.AUTH_CONFIG)
    original_auth_clients = app_module._auth_clients
    original_testing = app_module.app.testing
    original_session_factory = app_module.requests.Session
    original_block_parser_factory = block_parser.get_llm_client
    original_layout_factory = layout_composer.get_llm_client

    def set_auth_enabled(enabled):
        app_module.AUTH_CONFIG["enabled"] = enabled

    def reset_auth_state(client):
        app_module._auth_clients.clear()
        with client.session_transaction() as session_data:
            session_data.pop(app_module.AUTH_SESSION_COOKIE_KEY, None)

    def require_blocks(ctx):
        if ctx.get("blocks"):
            return ctx["blocks"]
        _skip("Пропущено: операция parse-blocks не вернула блоки.")

    def require_symbols(ctx):
        if ctx.get("symbols"):
            return ctx["symbols"]
        _skip("Пропущено: операция symbols не вернула список символов.")

    def require_layout_url(ctx):
        if ctx.get("layout_url"):
            return ctx["layout_url"]
        _skip("Пропущено: layout не был создан.")

    def require_png_url(ctx):
        if ctx.get("png_url"):
            return ctx["png_url"]
        _skip("Пропущено: PNG файл не был сгенерирован.")

    def require_svg_url(ctx):
        if ctx.get("svg_url"):
            return ctx["svg_url"]
        _skip("Пропущено: SVG файл не был сгенерирован.")

    def require_fixed_png_url(ctx):
        if ctx.get("fixed_png_url"):
            return ctx["fixed_png_url"]
        _skip("Пропущено: fix-png не вернул ссылку на файл.")

    def require_login(ctx):
        if ctx.get("login_ok"):
            return
        _skip("Пропущено: операция login не прошла, дальнейшие шаги auth-цепочки невалидны.")

    def record_operation(index, group, title, method, endpoint, expected_status, func, ctx):
        op_started = time.perf_counter()
        row = {
            "id": index,
            "group": group,
            "title": title,
            "method": method,
            "endpoint": endpoint,
            "expected_status": _status_list(expected_status),
            "actual_status": None,
            "status": "ok",
            "stop_stage": "done",
            "message": "OK",
            "details": [],
            "response_excerpt": None,
            "artifacts": [],
            "duration_ms": 0.0,
        }

        try:
            outcome = func(ctx) or {}
            row["actual_status"] = outcome.get("actual_status")
            row["message"] = outcome.get("message", "OK")
            row["details"] = outcome.get("details", [])
            row["response_excerpt"] = outcome.get("response_excerpt")
            row["artifacts"] = outcome.get("artifacts", [])
        except SmokeSkip as exc:
            row["status"] = "skip"
            row["stop_stage"] = "dependency"
            row["message"] = str(exc)
        except SmokeFailure as exc:
            row["status"] = "fail"
            row["stop_stage"] = exc.stop_stage
            row["actual_status"] = exc.actual_status
            row["message"] = str(exc)
            row["response_excerpt"] = exc.response_excerpt
        except Exception as exc:
            row["status"] = "fail"
            row["stop_stage"] = "exception"
            row["message"] = f"Необработанное исключение: {exc}"
            row["details"] = ["".join(traceback.format_exception_only(type(exc), exc)).strip()]
        finally:
            row["duration_ms"] = round((time.perf_counter() - op_started) * 1000, 2)
            results.append(row)

    try:
        app_module.output_dir = temp_output_dir
        app_module.editor_layout_dir = temp_editor_dir
        app_module.AUTH_CONFIG.update(
            {
                "enabled": False,
                "login_url": "https://smoke.example.test/auth/auth/token",
                "login_mode": "json",
                "username_field": "username",
                "password_field": "password",
                "token_keys": ["access_token", "token", "data.access_token"],
                "token_prefix": "Bearer",
                "verify_ssl": True,
                "request_timeout_seconds": 5.0,
                "api_base_url": "https://smoke.example.test/",
                "connection_test_path": "auth/v1/profile",
                "profile_path": "auth/v1/profile",
            }
        )
        app_module._auth_clients = {}
        app_module.app.testing = True
        app_module.requests.Session = _FakeSession
        block_parser.get_llm_client = lambda agent_type="default": HeuristicClient()
        layout_composer.get_llm_client = lambda agent_type="default": HeuristicClient()

        with app_module.app.test_client() as client:
            ctx = {
                "blocks": None,
                "symbols": None,
                "layout_url": None,
                "png_url": None,
                "svg_url": None,
                "fixed_png_url": None,
                "login_ok": False,
            }

            def assert_auth_required(path, method="get", json_body=None, label=None):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = getattr(client, method)(path, json=json_body)
                _expect_status(response, 401, label or f"{method.upper()} {path}")
                payload = _json_response(response, label or f"{method.upper()} {path}")
                _expect(
                    payload.get("auth_required") is True,
                    f"{label or f'{method.upper()} {path}'}: auth_required should be true",
                )
                return response, payload

            def assert_login_redirect(path, label=None):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.get(path, follow_redirects=False)
                _expect_status(response, (302, 308), label or f"GET {path}")
                location = response.headers.get("Location", "")
                _expect(
                    location.startswith("/login?next="),
                    f"{label or f'GET {path}'}: redirect to /login was expected",
                    stop_stage="response_format",
                )
                return response, location

            def op_auth_config(_ctx):
                set_auth_enabled(False)
                response = client.get("/api/auth/config")
                _expect_status(response, 200, "GET /api/auth/config")
                payload = _json_response(response, "GET /api/auth/config")
                _expect(payload.get("success") is True, "GET /api/auth/config: поле success должно быть true")
                return _ok("Конфигурация авторизации читается.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_status_public(_ctx):
                set_auth_enabled(False)
                reset_auth_state(client)
                response = client.get("/api/auth/status")
                _expect_status(response, 200, "GET /api/auth/status")
                payload = _json_response(response, "GET /api/auth/status")
                _expect(payload.get("authenticated") is False, "GET /api/auth/status: без логина authenticated должен быть false")
                return _ok("Публичный статус авторизации отвечает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_heartbeat(_ctx):
                set_auth_enabled(False)
                response = client.post("/api/heartbeat")
                _expect_status(response, 204, "POST /api/heartbeat")
                return _ok("Heartbeat отвечает 204.", actual_status=response.status_code)

            def op_status_public(_ctx):
                set_auth_enabled(False)
                response = client.get("/api/status")
                _expect_status(response, 200, "GET /api/status")
                payload = _json_response(response, "GET /api/status")
                _expect(payload.get("status") == "running", "GET /api/status: поле status должно быть running")
                return _ok("API status отвечает без авторизации, когда auth выключен.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_symbols(_ctx):
                set_auth_enabled(False)
                response = client.get("/api/symbols")
                _expect_status(response, 200, "GET /api/symbols")
                payload = _json_response(response, "GET /api/symbols")
                symbols = payload.get("symbols") or []
                _expect(symbols, "GET /api/symbols: список symbols пуст")
                _ctx["symbols"] = symbols
                return _ok(f"Список symbols получен: {len(symbols)} шт.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_block_types(_ctx):
                set_auth_enabled(False)
                response = client.get("/api/block-types")
                _expect_status(response, 200, "GET /api/block-types")
                payload = _json_response(response, "GET /api/block-types")
                _expect("header" in (payload.get("block_types") or {}), "GET /api/block-types: type header отсутствует")
                return _ok("Типы блоков получены.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_parse_blocks(_ctx):
                set_auth_enabled(False)
                response = client.post("/api/parse-blocks", json={"text": _sample_text(), "size_id": "46x46"})
                _expect_status(response, 200, "POST /api/parse-blocks")
                payload = _json_response(response, "POST /api/parse-blocks")
                blocks = payload.get("blocks") or []
                _expect(blocks, "POST /api/parse-blocks: блоки не вернулись")
                _ctx["blocks"] = blocks
                return _ok(f"Текст разбит на блоки: {len(blocks)} шт.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_parse_blocks_empty(_ctx):
                set_auth_enabled(False)
                response = client.post("/api/parse-blocks", json={"text": "", "size_id": "46x46"})
                _expect_status(response, 400, "POST /api/parse-blocks with empty text")
                payload = _json_response(response, "POST /api/parse-blocks with empty text")
                _expect(payload.get("success") is False, "POST /api/parse-blocks with empty text: success должен быть false")
                return _ok("Валидация пустого текста работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_get_variants(_ctx):
                set_auth_enabled(False)
                blocks = require_blocks(_ctx)
                symbols = require_symbols(_ctx)
                response = client.post(
                    "/api/get-variants",
                    json={
                        "size_id": "46x46",
                        "blocks": blocks,
                        "graphic_symbols": [symbols[0]["id"]],
                        "qr_size_percent": 100,
                        "symbol_size_percent": 100,
                    },
                )
                _expect_status(response, 200, "POST /api/get-variants")
                payload = _json_response(response, "POST /api/get-variants")
                variants = payload.get("variants") or []
                _expect(len(variants) == 4, "POST /api/get-variants: должно вернуться 4 варианта")
                return _ok("Варианты компоновки получены.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_generate_preview_png(_ctx):
                set_auth_enabled(False)
                blocks = require_blocks(_ctx)
                symbols = require_symbols(_ctx)
                response = client.post(
                    "/api/generate",
                    json={
                        "size_id": "46x46",
                        "blocks": blocks,
                        "graphic_symbols": [symbols[0]["id"]],
                        "symbol_positions": {symbols[0]["id"]: "left"},
                        "preview_mode": True,
                        "format": "png",
                        "variant_index": 0,
                    },
                )
                _expect_status(response, 200, "POST /api/generate preview png")
                payload = _json_response(response, "POST /api/generate preview png")
                variants = payload.get("variants") or []
                _expect(variants, "POST /api/generate preview png: variants пуст")
                data_url = variants[0].get("image_data_url", "")
                _expect(data_url.startswith("data:image/png;base64,"), "POST /api/generate preview png: PNG data URL не вернулся")
                return _ok("Preview PNG сгенерирован.", actual_status=response.status_code, response_excerpt=_json_excerpt(variants[0]))

            def op_generate_preview_svg(_ctx):
                set_auth_enabled(False)
                blocks = require_blocks(_ctx)
                response = client.post(
                    "/api/generate",
                    json={
                        "size_id": "46x46",
                        "blocks": blocks,
                        "preview_mode": True,
                        "format": "svg",
                        "all_variants": True,
                    },
                )
                _expect_status(response, 200, "POST /api/generate preview svg")
                payload = _json_response(response, "POST /api/generate preview svg")
                variants = payload.get("variants") or []
                _expect(len(variants) == 4, "POST /api/generate preview svg: должно вернуться 4 варианта")
                _expect(str(variants[0].get("image_data_url", "")).startswith("data:image/svg+xml;base64,"), "POST /api/generate preview svg: SVG data URL не вернулся")
                return _ok("Preview SVG сгенерирован.", actual_status=response.status_code, response_excerpt=_json_excerpt(variants[0]))

            def op_generate_without_blocks(_ctx):
                set_auth_enabled(False)
                response = client.post("/api/generate", json={"size_id": "46x46"})
                _expect_status(response, 400, "POST /api/generate without blocks")
                payload = _json_response(response, "POST /api/generate without blocks")
                _expect(payload.get("success") is False, "POST /api/generate without blocks: success должен быть false")
                return _ok("Валидация отсутствующих blocks работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_generate_file_png(_ctx):
                set_auth_enabled(False)
                blocks = require_blocks(_ctx)
                response = client.post(
                    "/api/generate",
                    json={
                        "size_id": "46x46",
                        "blocks": blocks,
                        "preview_mode": False,
                        "format": "png",
                        "variant_index": 1,
                        "high_quality_png": True,
                    },
                )
                _expect_status(response, 200, "POST /api/generate file png")
                payload = _json_response(response, "POST /api/generate file png")
                variants = payload.get("variants") or []
                _expect(variants, "POST /api/generate file png: variants пуст")
                image_url = variants[0].get("image_url")
                _expect(image_url, "POST /api/generate file png: image_url пуст")
                _ctx["png_url"] = image_url
                return _ok("PNG файл сгенерирован.", actual_status=response.status_code, response_excerpt=_json_excerpt(variants[0]), artifacts=[image_url])

            def op_fetch_file_png(_ctx):
                set_auth_enabled(False)
                image_url = require_png_url(_ctx)
                response = client.get(image_url)
                _expect_status(response, 200, f"GET {image_url}")
                content_type = response.headers.get("Content-Type", "")
                _expect("image/png" in content_type, f"GET {image_url}: ожидался PNG, пришел {content_type or 'unknown'}", stop_stage="response_format")
                return _ok("Сгенерированный PNG открывается через /output.", actual_status=response.status_code, artifacts=[image_url])

            def op_generate_file_svg(_ctx):
                set_auth_enabled(False)
                blocks = require_blocks(_ctx)
                response = client.post(
                    "/api/generate",
                    json={
                        "size_id": "46x46",
                        "blocks": blocks,
                        "preview_mode": False,
                        "format": "svg",
                        "variant_index": 0,
                    },
                )
                _expect_status(response, 200, "POST /api/generate file svg")
                payload = _json_response(response, "POST /api/generate file svg")
                variants = payload.get("variants") or []
                _expect(variants, "POST /api/generate file svg: variants пуст")
                image_url = variants[0].get("image_url")
                _expect(image_url, "POST /api/generate file svg: image_url пуст")
                _ctx["svg_url"] = image_url
                return _ok("SVG файл сгенерирован.", actual_status=response.status_code, response_excerpt=_json_excerpt(variants[0]), artifacts=[image_url])

            def op_fetch_file_svg(_ctx):
                set_auth_enabled(False)
                image_url = require_svg_url(_ctx)
                response = client.get(image_url)
                _expect_status(response, 200, f"GET {image_url}")
                content_type = response.headers.get("Content-Type", "")
                _expect("image/svg+xml" in content_type, f"GET {image_url}: ожидался SVG, пришел {content_type or 'unknown'}", stop_stage="response_format")
                return _ok("Сгенерированный SVG открывается через /output.", actual_status=response.status_code, artifacts=[image_url])

            def op_fix_png(_ctx):
                set_auth_enabled(False)
                image = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                png_base64 = base64.b64encode(buffer.getvalue()).decode("ascii")
                response = client.post("/api/fix-png", json={"png_base64": png_base64, "size_id": "smoke/test"})
                _expect_status(response, 200, "POST /api/fix-png")
                payload = _json_response(response, "POST /api/fix-png")
                image_url = payload.get("image_url")
                _expect(payload.get("filename") == "label_smoke_test.png", "POST /api/fix-png: filename санитизировался неверно")
                _expect(image_url, "POST /api/fix-png: image_url пуст")
                _ctx["fixed_png_url"] = image_url
                return _ok("fix-png сохраняет исправленный PNG.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload), artifacts=[image_url])

            def op_fix_png_invalid(_ctx):
                set_auth_enabled(False)
                response = client.post("/api/fix-png", json={"size_id": "smoke"})
                _expect_status(response, 400, "POST /api/fix-png without png_base64")
                payload = _json_response(response, "POST /api/fix-png without png_base64")
                _expect(payload.get("success") is False, "POST /api/fix-png without png_base64: success должен быть false")
                return _ok("Валидация fix-png на пустом payload работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_fetch_fixed_png(_ctx):
                set_auth_enabled(False)
                image_url = require_fixed_png_url(_ctx)
                response = client.get(image_url)
                _expect_status(response, 200, f"GET {image_url}")
                content_type = response.headers.get("Content-Type", "")
                _expect("image/png" in content_type, f"GET {image_url}: ожидался PNG, пришел {content_type or 'unknown'}", stop_stage="response_format")
                return _ok("Файл после fix-png открывается через /output.", actual_status=response.status_code, artifacts=[image_url])

            def op_editor_layout_create(_ctx):
                set_auth_enabled(False)
                blocks = require_blocks(_ctx)
                response = client.post("/api/editor-layouts", json={"name": "Smoke layout", "size_id": "46x46", "blocks": blocks[:2]})
                _expect_status(response, 200, "POST /api/editor-layouts")
                payload = _json_response(response, "POST /api/editor-layouts")
                layout_url = payload.get("layout_url")
                _expect(layout_url, "POST /api/editor-layouts: layout_url пуст")
                _ctx["layout_url"] = layout_url
                return _ok("Editor layout создан.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_create_invalid(_ctx):
                set_auth_enabled(False)
                response = client.post("/api/editor-layouts", json={})
                _expect_status(response, 400, "POST /api/editor-layouts with empty body")
                payload = _json_response(response, "POST /api/editor-layouts with empty body")
                _expect(payload.get("success") is False, "POST /api/editor-layouts with empty body: success должен быть false")
                return _ok("Валидация пустого editor layout работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_get(_ctx):
                set_auth_enabled(False)
                layout_url = require_layout_url(_ctx)
                response = client.get(layout_url)
                _expect_status(response, 200, f"GET {layout_url}")
                try:
                    payload = json.loads(response.get_data(as_text=True))
                except Exception:
                    _fail(f"GET {layout_url}: тело не разобралось как JSON", stop_stage="response_format", actual_status=response.status_code, response_excerpt=_body_excerpt(response))
                _expect(payload.get("name") == "Smoke layout", f"GET {layout_url}: вернулся неверный layout")
                return _ok("Editor layout читается обратно.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_delete(_ctx):
                set_auth_enabled(False)
                layout_url = require_layout_url(_ctx)
                response = client.delete(layout_url)
                _expect_status(response, 200, f"DELETE {layout_url}")
                payload = _json_response(response, f"DELETE {layout_url}")
                _expect(payload.get("deleted") is True, f"DELETE {layout_url}: deleted должен быть true")
                _ctx["layout_url"] = None
                return _ok("Editor layout удаляется.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_get_deleted(_ctx):
                set_auth_enabled(False)
                response = client.get("/api/editor-layouts/smoke-deleted")
                _expect_status(response, 404, "GET /api/editor-layouts/smoke-deleted")
                payload = _json_response(response, "GET /api/editor-layouts/smoke-deleted")
                _expect(payload.get("success") is False, "GET /api/editor-layouts/smoke-deleted: success должен быть false")
                return _ok("Отсутствующий editor layout корректно дает 404.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_delete_post(_ctx):
                set_auth_enabled(False)
                response = client.post("/api/editor-layouts/not-found")
                _expect_status(response, 200, "POST /api/editor-layouts/not-found")
                payload = _json_response(response, "POST /api/editor-layouts/not-found")
                _expect(payload.get("deleted") is False, "POST /api/editor-layouts/not-found: deleted должен быть false")
                return _ok("POST-ветка delete для editor layout отвечает корректно.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_status_auth_guard(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                _ctx["login_ok"] = False
                response = client.get("/api/status")
                _expect_status(response, 401, "GET /api/status with auth enabled and no session")
                payload = _json_response(response, "GET /api/status with auth enabled and no session")
                _expect(payload.get("auth_required") is True, "GET /api/status with auth enabled and no session: auth_required должен быть true")
                return _ok("Guard защищенного статуса работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_token_guard(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.get("/api/auth/token")
                _expect_status(response, 401, "GET /api/auth/token without session")
                payload = _json_response(response, "GET /api/auth/token without session")
                _expect(payload.get("auth_required") is True, "GET /api/auth/token without session: auth_required должен быть true")
                return _ok("Guard token-эндпоинта работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_config_enabled(_ctx):
                set_auth_enabled(True)
                response = client.get("/api/auth/config")
                _expect_status(response, 200, "GET /api/auth/config with auth enabled")
                payload = _json_response(response, "GET /api/auth/config with auth enabled")
                _expect(payload.get("enabled") is True, "GET /api/auth/config with auth enabled: enabled should be true")
                return _ok("Auth config shows enabled mode.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_labelflow_editor_guard(_ctx):
                response, location = assert_login_redirect("/labelflow-editor/", "GET /labelflow-editor/ without session")
                return _ok("Editor page redirects to login.", actual_status=response.status_code, details=[location])

            def op_smoke_page_guard(_ctx):
                response, location = assert_login_redirect("/smoke-test", "GET /smoke-test without session")
                return _ok("Smoke page redirects to login.", actual_status=response.status_code, details=[location])

            def op_symbols_auth_guard(_ctx):
                response, payload = assert_auth_required("/api/symbols", "get", None, "GET /api/symbols without session")
                return _ok("Symbols endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_block_types_auth_guard(_ctx):
                response, payload = assert_auth_required("/api/block-types", "get", None, "GET /api/block-types without session")
                return _ok("Block-types endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_parse_blocks_auth_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/parse-blocks",
                    "post",
                    {"text": _sample_text(), "size_id": "46x46"},
                    "POST /api/parse-blocks without session",
                )
                return _ok("Parse-blocks endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_get_variants_auth_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/get-variants",
                    "post",
                    {"size_id": "46x46", "blocks": []},
                    "POST /api/get-variants without session",
                )
                return _ok("Get-variants endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_generate_auth_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/generate",
                    "post",
                    {"size_id": "46x46", "blocks": []},
                    "POST /api/generate without session",
                )
                return _ok("Generate endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_fix_png_auth_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/fix-png",
                    "post",
                    {"png_base64": "AA==", "size_id": "46x46"},
                    "POST /api/fix-png without session",
                )
                return _ok("Fix-png endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_create_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/editor-layouts",
                    "post",
                    {"name": "guard-layout", "blocks": []},
                    "POST /api/editor-layouts without session",
                )
                return _ok("Editor-layout create is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_get_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/editor-layouts/smoke-layout",
                    "get",
                    None,
                    "GET /api/editor-layouts/<id> without session",
                )
                return _ok("Editor-layout get is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_delete_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/editor-layouts/smoke-layout",
                    "delete",
                    None,
                    "DELETE /api/editor-layouts/<id> without session",
                )
                return _ok("Editor-layout delete is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_profile_guard(_ctx):
                response, payload = assert_auth_required("/api/auth/profile", "get", None, "GET /api/auth/profile without session")
                return _ok("Profile endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_profile_refresh_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/auth/profile/refresh",
                    "post",
                    {},
                    "POST /api/auth/profile/refresh without session",
                )
                return _ok("Profile refresh endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_request_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/external/request",
                    "post",
                    {"method": "GET", "endpoint": "echo/test"},
                    "POST /api/external/request without session",
                )
                return _ok("External request endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_test_connection_guard(_ctx):
                response, payload = assert_auth_required(
                    "/api/external/test-connection",
                    "post",
                    {"method": "GET"},
                    "POST /api/external/test-connection without session",
                )
                return _ok("External test-connection endpoint is protected.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_token_put_invalid(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.put("/api/auth/token", json={"token": "", "username": "manual-user"})
                _expect_status(response, 400, "PUT /api/auth/token with empty token")
                payload = _json_response(response, "PUT /api/auth/token with empty token")
                _expect(payload.get("success") is False, "PUT /api/auth/token with empty token: success should be false")
                return _ok("Empty token validation works.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_login_invalid(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.post("/api/auth/login", json={"username": "", "password": ""})
                _expect_status(response, 400, "POST /api/auth/login with empty credentials")
                payload = _json_response(response, "POST /api/auth/login with empty credentials")
                _expect(payload.get("success") is False, "POST /api/auth/login with empty credentials: success должен быть false")
                return _ok("Валидация пустых credentials работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_login_success(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.post("/api/auth/login", json={"username": "smoke-user", "password": "smoke-pass"})
                _expect_status(response, 200, "POST /api/auth/login")
                payload = _json_response(response, "POST /api/auth/login")
                _expect(payload.get("authenticated") is True, "POST /api/auth/login: authenticated должен быть true")
                _ctx["login_ok"] = True
                return _ok("Авторизация проходит успешно.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_status_after_login(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.get("/api/auth/status")
                _expect_status(response, 200, "GET /api/auth/status after login")
                payload = _json_response(response, "GET /api/auth/status after login")
                _expect(payload.get("authenticated") is True, "GET /api/auth/status after login: authenticated должен быть true")
                return _ok("Статус авторизации отражает активную сессию.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_token_get(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.get("/api/auth/token")
                _expect_status(response, 200, "GET /api/auth/token")
                payload = _json_response(response, "GET /api/auth/token")
                _expect(payload.get("has_token") is True, "GET /api/auth/token: token не найден")
                return _ok("Токен доступен после логина.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_profile_get(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.get("/api/auth/profile")
                _expect_status(response, 200, "GET /api/auth/profile")
                payload = _json_response(response, "GET /api/auth/profile")
                _expect(payload.get("profile", {}).get("username") == "smoke-user", "GET /api/auth/profile: профиль не загрузился")
                return _ok("Профиль загружается после логина.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_profile_refresh(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.post("/api/auth/profile/refresh")
                _expect_status(response, 200, "POST /api/auth/profile/refresh")
                payload = _json_response(response, "POST /api/auth/profile/refresh")
                _expect(payload.get("profile", {}).get("role") == "tester", "POST /api/auth/profile/refresh: роль не обновилась")
                return _ok("Принудительное обновление профиля работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_request_success(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.post(
                    "/api/external/request",
                    json={
                        "method": "POST",
                        "endpoint": "echo/test",
                        "headers": {"X-Smoke": "1"},
                        "params": {"ping": "pong"},
                        "json": {"smoke": True},
                    },
                )
                _expect_status(response, 200, "POST /api/external/request")
                payload = _json_response(response, "POST /api/external/request")
                echo_payload = payload.get("json", {}).get("echo", {})
                _expect(echo_payload.get("json", {}).get("smoke") is True, "POST /api/external/request: JSON body не дошел")
                return _ok("Внешний proxy-запрос проходит.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_request_invalid_method(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.post("/api/external/request", json={"method": "TRACE", "endpoint": "echo/test"})
                _expect_status(response, 400, "POST /api/external/request with invalid method")
                payload = _json_response(response, "POST /api/external/request with invalid method")
                _expect(payload.get("success") is False, "POST /api/external/request with invalid method: success должен быть false")
                return _ok("Валидация недопустимого HTTP method работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_request_forbidden_url(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.post("/api/external/request", json={"method": "GET", "endpoint": "https://forbidden.example/api"})
                _expect_status(response, 400, "POST /api/external/request with forbidden url")
                payload = _json_response(response, "POST /api/external/request with forbidden url")
                _expect(payload.get("success") is False, "POST /api/external/request with forbidden url: success должен быть false")
                return _ok("Запрещенный внешний URL режется корректно.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_test_connection(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.post("/api/external/test-connection", json={"method": "GET"})
                _expect_status(response, 200, "POST /api/external/test-connection")
                payload = _json_response(response, "POST /api/external/test-connection")
                _expect(payload.get("json", {}).get("data", {}).get("role") == "tester", "POST /api/external/test-connection: профиль не вернулся")
                return _ok("Проверка соединения проходит.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_test_connection_invalid_config(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.put("/api/auth/token", json={"token": "manual-token-1", "username": "manual-user"})
                _expect_status(response, 200, "PUT /api/auth/token before invalid external connection test")
                old_path = app_module.AUTH_CONFIG["connection_test_path"]
                app_module.AUTH_CONFIG["connection_test_path"] = ""
                try:
                    response = client.post("/api/external/test-connection", json={"method": "GET"})
                    _expect_status(response, 400, "POST /api/external/test-connection with empty config")
                    payload = _json_response(response, "POST /api/external/test-connection with empty config")
                    _expect(payload.get("success") is False, "POST /api/external/test-connection with empty config: success должен быть false")
                finally:
                    app_module.AUTH_CONFIG["connection_test_path"] = old_path
                return _ok("Валидация пустого connection_test_path работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_profile_delete(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.delete("/api/auth/profile")
                _expect_status(response, 200, "DELETE /api/auth/profile")
                payload = _json_response(response, "DELETE /api/auth/profile")
                _expect(payload.get("profile") is None, "DELETE /api/auth/profile: profile должен быть None")
                return _ok("Профиль очищается.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_logout(_ctx):
                set_auth_enabled(True)
                response = client.post("/api/auth/logout")
                _expect_status(response, 200, "POST /api/auth/logout")
                payload = _json_response(response, "POST /api/auth/logout")
                _ctx["login_ok"] = False
                _expect(payload.get("authenticated") is False, "POST /api/auth/logout: authenticated должен стать false")
                return _ok("Logout отрабатывает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_token_put(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.put("/api/auth/token", json={"token": "manual-token-1234", "username": "manual-user"})
                _expect_status(response, 200, "PUT /api/auth/token")
                payload = _json_response(response, "PUT /api/auth/token")
                _expect(payload.get("authenticated") is True, "PUT /api/auth/token: authenticated должен быть true")
                return _ok("Ручная установка токена работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_token_delete(_ctx):
                set_auth_enabled(True)
                response = client.delete("/api/auth/token")
                _expect_status(response, 200, "DELETE /api/auth/token")
                payload = _json_response(response, "DELETE /api/auth/token")
                _expect(payload.get("authenticated") is False, "DELETE /api/auth/token: authenticated должен быть false")
                return _ok("Удаление токена работает.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_login_http_error(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.post("/api/auth/login", json={"username": "http-error", "password": "smoke-pass"})
                _expect_status(response, 401, "POST /api/auth/login upstream error")
                payload = _json_response(response, "POST /api/auth/login upstream error")
                _expect(payload.get("success") is False, "POST /api/auth/login upstream error: success должен быть false")
                return _ok("Ошибка внешнего auth-сервиса отражается корректно.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_login_no_token(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.post("/api/auth/login", json={"username": "no-token", "password": "smoke-pass"})
                _expect_status(response, 502, "POST /api/auth/login without token")
                payload = _json_response(response, "POST /api/auth/login without token")
                _expect(payload.get("success") is False, "POST /api/auth/login without token: success должен быть false")
                return _ok("Сценарий без token/cookies ловится как ошибка.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_login_header_token(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.post("/api/auth/login", json={"username": "header-token", "password": "smoke-pass"})
                _expect_status(response, 200, "POST /api/auth/login with header token")
                payload = _json_response(response, "POST /api/auth/login with header token")
                _expect(payload.get("authenticated") is True, "POST /api/auth/login with header token: authenticated should be true")
                _expect(payload.get("has_cookie_session") is False, "POST /api/auth/login with header token: cookie session should be false")
                _expect(payload.get("token_masked"), "POST /api/auth/login with header token: token mask should not be empty")
                return _ok("Login works with bearer token from response header.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_auth_login_cookie_only(_ctx):
                set_auth_enabled(True)
                reset_auth_state(client)
                response = client.post("/api/auth/login", json={"username": "cookie-only", "password": "smoke-pass"})
                _expect_status(response, 200, "POST /api/auth/login with cookie-only session")
                payload = _json_response(response, "POST /api/auth/login with cookie-only session")
                _expect(payload.get("authenticated") is True, "POST /api/auth/login with cookie-only session: authenticated should be true")
                _expect(payload.get("has_cookie_session") is True, "POST /api/auth/login with cookie-only session: cookie session should be true")
                return _ok("Login works with cookie-only upstream session.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_request_text_response(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.post("/api/external/request", json={"method": "GET", "endpoint": "text-response"})
                _expect_status(response, 200, "POST /api/external/request text response")
                payload = _json_response(response, "POST /api/external/request text response")
                _expect(payload.get("text") == "plain-text-response", "POST /api/external/request text response: plain text payload was expected")
                return _ok("External proxy keeps plain-text response.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_request_upstream_error(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.post("/api/external/request", json={"method": "GET", "endpoint": "request-error"})
                _expect_status(response, 502, "POST /api/external/request upstream error")
                payload = _json_response(response, "POST /api/external/request upstream error")
                _expect(payload.get("success") is False, "POST /api/external/request upstream error: success should be false")
                return _ok("External proxy surfaces upstream transport errors.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_external_request_missing_endpoint(_ctx):
                set_auth_enabled(True)
                require_login(_ctx)
                response = client.post("/api/external/request", json={"method": "GET"})
                _expect_status(response, 400, "POST /api/external/request without endpoint")
                payload = _json_response(response, "POST /api/external/request without endpoint")
                _expect(payload.get("success") is False, "POST /api/external/request without endpoint: success should be false")
                return _ok("External proxy validates missing endpoint.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_fix_png_bad_base64(_ctx):
                set_auth_enabled(False)
                response = client.post("/api/fix-png", json={"png_base64": "not-base64!!", "size_id": "smoke"})
                _expect_status(response, 500, "POST /api/fix-png with invalid base64")
                payload = _json_response(response, "POST /api/fix-png with invalid base64")
                _expect(payload.get("success") is False, "POST /api/fix-png with invalid base64: success should be false")
                return _ok("Invalid base64 is surfaced as rendering error.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_invalid_get(_ctx):
                set_auth_enabled(False)
                response = client.get("/api/editor-layouts/!!!")
                _expect_status(response, 400, "GET /api/editor-layouts/!!!")
                payload = _json_response(response, "GET /api/editor-layouts/!!!")
                _expect(payload.get("success") is False, "GET /api/editor-layouts/!!!: success should be false")
                return _ok("Editor-layout get validates bad id.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_editor_layout_invalid_delete(_ctx):
                set_auth_enabled(False)
                response = client.delete("/api/editor-layouts/!!!")
                _expect_status(response, 400, "DELETE /api/editor-layouts/!!!")
                payload = _json_response(response, "DELETE /api/editor-layouts/!!!")
                _expect(payload.get("success") is False, "DELETE /api/editor-layouts/!!!: success should be false")
                return _ok("Editor-layout delete validates bad id.", actual_status=response.status_code, response_excerpt=_json_excerpt(payload))

            def op_generate_invalid_format_fallback(_ctx):
                set_auth_enabled(False)
                blocks = require_blocks(_ctx)
                response = client.post("/api/generate", json={"size_id": "46x46", "blocks": blocks, "preview_mode": True, "format": "pdf"})
                _expect_status(response, 200, "POST /api/generate with invalid format")
                payload = _json_response(response, "POST /api/generate with invalid format")
                variants = payload.get("variants") or []
                _expect(variants, "POST /api/generate with invalid format: variants should not be empty")
                _expect(str(variants[0].get("image_data_url", "")).startswith("data:image/png;base64,"), "POST /api/generate with invalid format: fallback PNG preview was expected")
                return _ok("Generate falls back to PNG on invalid format.", actual_status=response.status_code, response_excerpt=_json_excerpt(variants[0]))

            def op_generate_all_variants_png(_ctx):
                set_auth_enabled(False)
                blocks = require_blocks(_ctx)
                response = client.post("/api/generate", json={"size_id": "46x46", "blocks": blocks, "preview_mode": True, "format": "png", "all_variants": True})
                _expect_status(response, 200, "POST /api/generate all variants png")
                payload = _json_response(response, "POST /api/generate all variants png")
                variants = payload.get("variants") or []
                _expect(len(variants) == 4, "POST /api/generate all variants png: exactly 4 variants were expected")
                return _ok("Generate returns all 4 PNG preview variants.", actual_status=response.status_code, response_excerpt=_json_excerpt(variants[0] if variants else payload))

            def op_self_skip(_ctx):
                _skip("Маршрут /api/smoke-test не вызывается внутри самого себя, чтобы не запускать рекурсивный прогон.")

            operations = [
                ("Общее", "Конфиг авторизации", "GET", "/api/auth/config", 200, op_auth_config),
                ("Общее", "Статус авторизации", "GET", "/api/auth/status", 200, op_auth_status_public),
                ("Общее", "Heartbeat", "POST", "/api/heartbeat", 204, op_heartbeat),
                ("Общее", "Статус API", "GET", "/api/status", 200, op_status_public),
                ("Парсинг", "Список symbols", "GET", "/api/symbols", 200, op_symbols),
                ("Парсинг", "Типы блоков", "GET", "/api/block-types", 200, op_block_types),
                ("Парсинг", "Разбор текста в блоки", "POST", "/api/parse-blocks", 200, op_parse_blocks),
                ("Парсинг", "Валидация пустого текста", "POST", "/api/parse-blocks", 400, op_parse_blocks_empty),
                ("Компоновка", "Получение вариантов", "POST", "/api/get-variants", 200, op_get_variants),
                ("Генерация", "Preview PNG", "POST", "/api/generate", 200, op_generate_preview_png),
                ("Генерация", "Preview SVG", "POST", "/api/generate", 200, op_generate_preview_svg),
                ("Генерация", "Валидация blocks", "POST", "/api/generate", 400, op_generate_without_blocks),
                ("Генерация", "Файл PNG", "POST", "/api/generate", 200, op_generate_file_png),
                ("Генерация", "Чтение PNG файла", "GET", "/output/<png>", 200, op_fetch_file_png),
                ("Генерация", "Файл SVG", "POST", "/api/generate", 200, op_generate_file_svg),
                ("Генерация", "Чтение SVG файла", "GET", "/output/<svg>", 200, op_fetch_file_svg),
                ("Генерация", "fix-png", "POST", "/api/fix-png", 200, op_fix_png),
                ("Генерация", "fix-png invalid", "POST", "/api/fix-png", 400, op_fix_png_invalid),
                ("Генерация", "Чтение fix-png файла", "GET", "/output/<fixed-png>", 200, op_fetch_fixed_png),
                ("Editor", "Создание layout", "POST", "/api/editor-layouts", 200, op_editor_layout_create),
                ("Editor", "Пустой layout", "POST", "/api/editor-layouts", 400, op_editor_layout_create_invalid),
                ("Editor", "Чтение layout", "GET", "/api/editor-layouts/<id>", 200, op_editor_layout_get),
                ("Editor", "Удаление layout", "DELETE", "/api/editor-layouts/<id>", 200, op_editor_layout_delete),
                ("Editor", "Чтение отсутствующего layout", "GET", "/api/editor-layouts/<id>", 404, op_editor_layout_get_deleted),
                ("Editor", "POST-ветка удаления", "POST", "/api/editor-layouts/<id>", 200, op_editor_layout_delete_post),
                ("Auth", "Guard защищенного API", "GET", "/api/status", 401, op_status_auth_guard),
                ("Auth", "Guard token API", "GET", "/api/auth/token", 401, op_auth_token_guard),
                ("Auth", "Логин с пустыми credentials", "POST", "/api/auth/login", 400, op_auth_login_invalid),
                ("Auth", "Успешный логин", "POST", "/api/auth/login", 200, op_auth_login_success),
                ("Auth", "Статус после логина", "GET", "/api/auth/status", 200, op_auth_status_after_login),
                ("Auth", "Получение токена", "GET", "/api/auth/token", 200, op_auth_token_get),
                ("Auth", "Получение профиля", "GET", "/api/auth/profile", 200, op_auth_profile_get),
                ("Auth", "Обновление профиля", "POST", "/api/auth/profile/refresh", 200, op_auth_profile_refresh),
                ("External", "Proxy запрос", "POST", "/api/external/request", 200, op_external_request_success),
                ("External", "Недопустимый method", "POST", "/api/external/request", 400, op_external_request_invalid_method),
                ("External", "Запрещенный URL", "POST", "/api/external/request", 400, op_external_request_forbidden_url),
                ("External", "Проверка соединения", "POST", "/api/external/test-connection", 200, op_external_test_connection),
                ("External", "Пустой connection_test_path", "POST", "/api/external/test-connection", 400, op_external_test_connection_invalid_config),
                ("Auth", "Удаление профиля", "DELETE", "/api/auth/profile", 200, op_auth_profile_delete),
                ("Auth", "Logout", "POST", "/api/auth/logout", 200, op_auth_logout),
                ("Auth", "Ручная установка токена", "PUT", "/api/auth/token", 200, op_auth_token_put),
                ("Auth", "Удаление токена", "DELETE", "/api/auth/token", 200, op_auth_token_delete),
                ("Auth", "Ошибка auth upstream", "POST", "/api/auth/login", 401, op_auth_login_http_error),
                ("Auth", "Нет token/cookies после login", "POST", "/api/auth/login", 502, op_auth_login_no_token),
                ("Meta", "Самопроверка smoke-route", "GET", "/api/smoke-test", 0, op_self_skip),
            ]

            smoke_operations = [
                ("General", "Auth config", "GET", "/api/auth/config", 200, op_auth_config),
                ("General", "Auth config enabled", "GET", "/api/auth/config", 200, op_auth_config_enabled),
                ("General", "Auth status public", "GET", "/api/auth/status", 200, op_auth_status_public),
                ("General", "Heartbeat", "POST", "/api/heartbeat", 204, op_heartbeat),
                ("General", "API status public", "GET", "/api/status", 200, op_status_public),
                ("UI Guard", "Editor redirect", "GET", "/labelflow-editor/", (302, 308), op_labelflow_editor_guard),
                ("UI Guard", "Smoke redirect", "GET", "/smoke-test", (302, 308), op_smoke_page_guard),
                ("Auth Guard", "Status guard", "GET", "/api/status", 401, op_status_auth_guard),
                ("Auth Guard", "Token guard", "GET", "/api/auth/token", 401, op_auth_token_guard),
                ("Auth Guard", "Profile guard", "GET", "/api/auth/profile", 401, op_auth_profile_guard),
                ("Auth Guard", "Profile refresh guard", "POST", "/api/auth/profile/refresh", 401, op_auth_profile_refresh_guard),
                ("Auth Guard", "Symbols guard", "GET", "/api/symbols", 401, op_symbols_auth_guard),
                ("Auth Guard", "Block types guard", "GET", "/api/block-types", 401, op_block_types_auth_guard),
                ("Auth Guard", "Parse blocks guard", "POST", "/api/parse-blocks", 401, op_parse_blocks_auth_guard),
                ("Auth Guard", "Get variants guard", "POST", "/api/get-variants", 401, op_get_variants_auth_guard),
                ("Auth Guard", "Generate guard", "POST", "/api/generate", 401, op_generate_auth_guard),
                ("Auth Guard", "Fix PNG guard", "POST", "/api/fix-png", 401, op_fix_png_auth_guard),
                ("Auth Guard", "Editor create guard", "POST", "/api/editor-layouts", 401, op_editor_layout_create_guard),
                ("Auth Guard", "Editor get guard", "GET", "/api/editor-layouts/<id>", 401, op_editor_layout_get_guard),
                ("Auth Guard", "Editor delete guard", "DELETE", "/api/editor-layouts/<id>", 401, op_editor_layout_delete_guard),
                ("Auth Guard", "External request guard", "POST", "/api/external/request", 401, op_external_request_guard),
                ("Auth Guard", "External connection guard", "POST", "/api/external/test-connection", 401, op_external_test_connection_guard),
                ("Parsing", "Symbols list", "GET", "/api/symbols", 200, op_symbols),
                ("Parsing", "Block types", "GET", "/api/block-types", 200, op_block_types),
                ("Parsing", "Parse blocks", "POST", "/api/parse-blocks", 200, op_parse_blocks),
                ("Parsing", "Parse blocks empty text", "POST", "/api/parse-blocks", 400, op_parse_blocks_empty),
                ("Layout", "Get variants", "POST", "/api/get-variants", 200, op_get_variants),
                ("Rendering", "Generate preview PNG", "POST", "/api/generate", 200, op_generate_preview_png),
                ("Rendering", "Generate preview SVG", "POST", "/api/generate", 200, op_generate_preview_svg),
                ("Rendering", "Generate all variants PNG", "POST", "/api/generate", 200, op_generate_all_variants_png),
                ("Rendering", "Generate invalid format fallback", "POST", "/api/generate", 200, op_generate_invalid_format_fallback),
                ("Rendering", "Generate without blocks", "POST", "/api/generate", 400, op_generate_without_blocks),
                ("Rendering", "Generate file PNG", "POST", "/api/generate", 200, op_generate_file_png),
                ("Rendering", "Fetch file PNG", "GET", "/output/<png>", 200, op_fetch_file_png),
                ("Rendering", "Generate file SVG", "POST", "/api/generate", 200, op_generate_file_svg),
                ("Rendering", "Fetch file SVG", "GET", "/output/<svg>", 200, op_fetch_file_svg),
                ("Rendering", "Fix PNG", "POST", "/api/fix-png", 200, op_fix_png),
                ("Rendering", "Fix PNG missing payload", "POST", "/api/fix-png", 400, op_fix_png_invalid),
                ("Rendering", "Fix PNG bad base64", "POST", "/api/fix-png", 500, op_fix_png_bad_base64),
                ("Rendering", "Fetch fixed PNG", "GET", "/output/<fixed-png>", 200, op_fetch_fixed_png),
                ("Editor", "Create layout", "POST", "/api/editor-layouts", 200, op_editor_layout_create),
                ("Editor", "Create empty layout", "POST", "/api/editor-layouts", 400, op_editor_layout_create_invalid),
                ("Editor", "Get layout", "GET", "/api/editor-layouts/<id>", 200, op_editor_layout_get),
                ("Editor", "Delete layout", "DELETE", "/api/editor-layouts/<id>", 200, op_editor_layout_delete),
                ("Editor", "Get deleted layout", "GET", "/api/editor-layouts/<id>", 404, op_editor_layout_get_deleted),
                ("Editor", "Delete via POST", "POST", "/api/editor-layouts/<id>", 200, op_editor_layout_delete_post),
                ("Editor", "Get invalid layout id", "GET", "/api/editor-layouts/!!!", 400, op_editor_layout_invalid_get),
                ("Editor", "Delete invalid layout id", "DELETE", "/api/editor-layouts/!!!", 400, op_editor_layout_invalid_delete),
                ("Auth Flow", "Login invalid credentials", "POST", "/api/auth/login", 400, op_auth_login_invalid),
                ("Auth Flow", "Login success", "POST", "/api/auth/login", 200, op_auth_login_success),
                ("Auth Flow", "Status after login", "GET", "/api/auth/status", 200, op_auth_status_after_login),
                ("Auth Flow", "Token after login", "GET", "/api/auth/token", 200, op_auth_token_get),
                ("Auth Flow", "Profile get", "GET", "/api/auth/profile", 200, op_auth_profile_get),
                ("Auth Flow", "Profile refresh", "POST", "/api/auth/profile/refresh", 200, op_auth_profile_refresh),
                ("External", "Proxy request", "POST", "/api/external/request", 200, op_external_request_success),
                ("External", "Proxy text response", "POST", "/api/external/request", 200, op_external_request_text_response),
                ("External", "Proxy invalid method", "POST", "/api/external/request", 400, op_external_request_invalid_method),
                ("External", "Proxy forbidden URL", "POST", "/api/external/request", 400, op_external_request_forbidden_url),
                ("External", "Proxy missing endpoint", "POST", "/api/external/request", 400, op_external_request_missing_endpoint),
                ("External", "Proxy upstream error", "POST", "/api/external/request", 502, op_external_request_upstream_error),
                ("External", "Test connection", "POST", "/api/external/test-connection", 200, op_external_test_connection),
                ("External", "Test connection bad config", "POST", "/api/external/test-connection", 400, op_external_test_connection_invalid_config),
                ("Auth Flow", "Delete profile", "DELETE", "/api/auth/profile", 200, op_auth_profile_delete),
                ("Auth Flow", "Logout", "POST", "/api/auth/logout", 200, op_auth_logout),
                ("Auth Flow", "Manual token put", "PUT", "/api/auth/token", 200, op_auth_token_put),
                ("Auth Flow", "Manual token put invalid", "PUT", "/api/auth/token", 400, op_auth_token_put_invalid),
                ("Auth Flow", "Manual token delete", "DELETE", "/api/auth/token", 200, op_auth_token_delete),
                ("Auth Flow", "Login upstream HTTP error", "POST", "/api/auth/login", 401, op_auth_login_http_error),
                ("Auth Flow", "Login without token or cookies", "POST", "/api/auth/login", 502, op_auth_login_no_token),
                ("Auth Flow", "Login with header token", "POST", "/api/auth/login", 200, op_auth_login_header_token),
                ("Auth Flow", "Login with cookie-only session", "POST", "/api/auth/login", 200, op_auth_login_cookie_only),
                ("Meta", "Smoke route self-skip", "GET", "/api/smoke-test", 0, op_self_skip),
            ]

            for index, operation in enumerate(smoke_operations, start=1):
                record_operation(
                    index=index,
                    group=operation[0],
                    title=operation[1],
                    method=operation[2],
                    endpoint=operation[3],
                    expected_status=operation[4],
                    func=operation[5],
                    ctx=ctx,
                )

    finally:
        app_module.output_dir = original_output_dir
        app_module.editor_layout_dir = original_editor_layout_dir
        app_module.AUTH_CONFIG.clear()
        app_module.AUTH_CONFIG.update(original_auth_config)
        app_module._auth_clients = original_auth_clients
        app_module.app.testing = original_testing
        app_module.requests.Session = original_session_factory
        block_parser.get_llm_client = original_block_parser_factory
        layout_composer.get_llm_client = original_layout_factory
        temp_root.cleanup()

    passed = sum(1 for item in results if item["status"] == "ok")
    failed = sum(1 for item in results if item["status"] == "fail")
    skipped = sum(1 for item in results if item["status"] == "skip")

    groups = {}
    for item in results:
        bucket = groups.setdefault(
            item["group"],
            {"group": item["group"], "total": 0, "passed": 0, "failed": 0, "skipped": 0},
        )
        bucket["total"] += 1
        if item["status"] == "ok":
            bucket["passed"] += 1
        elif item["status"] == "fail":
            bucket["failed"] += 1
        else:
            bucket["skipped"] += 1

    return {
        "success": failed == 0,
        "generated_at": generated_at,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "groups": list(groups.values()),
        "operations": results,
    }


if __name__ == "__main__":
    import app as app_module

    print(json.dumps(run_smoke_tests(app_module), ensure_ascii=False, indent=2))
