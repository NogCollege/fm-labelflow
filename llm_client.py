"""
llm_client.py - Единый интерфейс для LLM-провайдеров.

Переключается через переменные окружения:
  LLM_PROVIDER = yandex | openai | alice | none   (default: none)
  LLM_API_KEY  = ключ API

  Для yandex:
    YANDEX_PROJECT              = ID проекта в Yandex Cloud
    YANDEX_PROMPT_BLOCK_PARSER  = ID промпта для разбивки блоков
    YANDEX_PROMPT_LAYOUT_COMPOSER = ID промпта для вариантов компоновки

  Для openai:
    LLM_MODEL = gpt-4o-mini (default)

  Для alice:
    LLM_API_URL = URL эндпоинта
    LLM_MODEL   = название модели

При LLM_PROVIDER=none (или недоступном API) используется HeuristicClient,
который возвращает пустую строку — вызывающий код переходит на regex-эвристику.
"""

import os
import sys
import json
import logging


logger = logging.getLogger(__name__)


class LLMClient:
    """Базовый интерфейс."""

    def complete(self, system: str, user: str) -> str:
        """Отправить system+user промпт, вернуть текст ответа."""
        raise NotImplementedError

    def complete_json(self, system: str, user: str) -> dict | list | None:
        """Вызвать complete() и попробовать распарсить JSON из ответа."""
        raw = self.complete(system, user)
        if not raw:
            return None
        for chunk in (raw, raw.strip()):
            chunk = chunk.strip()
            if chunk.startswith("```"):
                chunk = chunk.split("```")[1]
                if chunk.startswith("json"):
                    chunk = chunk[4:]
                chunk = chunk.strip()
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                pass
        return None


class HeuristicClient(LLMClient):
    """Заглушка — используется когда LLM недоступен."""

    def complete(self, system: str, user: str) -> str:
        return ""


class OpenAICompatibleClient(LLMClient):
    """
    Работает с любым OpenAI-совместимым API:
    - OpenAI (api.openai.com/v1)
    - Alice AI / YandexGPT через /chat/completions
    - GigaChat, Mistral и др.
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def complete(self, system: str, user: str) -> str:
        try:
            import requests as _http
        except ImportError:
            logger.warning("requests не установлен")
            return ""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        try:
            r = _http.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("LLM request failed: %s", e)
            return ""


class YandexStudioClient(LLMClient):
    """
    Клиент для Yandex AI Studio (Responses API).

    Использует pre-configured промпт из Yandex Studio (prompt_id).
    Схема ответа и system-инструкции задаются там же — в коде не дублируются.
    """

    BASE_URL = "https://ai.api.cloud.yandex.net/v1"

    def __init__(self, api_key: str, prompt_id: str, project: str):
        self.api_key = api_key
        self.prompt_id = prompt_id
        self.project = project
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError:
                raise RuntimeError(
                    "Установите пакет openai: pip install openai>=1.66"
                )
            self._client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.BASE_URL,
                project=self.project,
            )
        return self._client

    def complete(self, system: str, user: str) -> str:
        """system-промпт игнорируется — он уже задан в Yandex Studio."""
        logger.info("Yandex complete() prompt=%s input=%.60r...", self.prompt_id, user)
        try:
            client = self._get_client()
            logger.info("Yandex: клиент создан, отправляю запрос...")
            response = client.responses.create(
                prompt={"id": self.prompt_id},
                input=user,
            )
            result = response.output_text or ""
            logger.info("Yandex: ответ получен len=%d preview=%.120r", len(result), result)
            return result
        except Exception as e:
            import traceback
            logger.error("Yandex ОШИБКА (prompt=%s): %r", self.prompt_id, e)
            logger.error(traceback.format_exc())
            return ""

    def complete_json(self, system: str, user: str) -> dict | list | None:
        """
        Схема JSON уже задана в Yandex Studio — ответ должен быть валидным JSON.
        Парсим напрямую, с fallback на очистку markdown-обёрток.
        """
        raw = self.complete(system, user)
        if not raw:
            return None
        raw = raw.strip()
        # убрать возможные ```json ... ``` обёртки
        if raw.startswith("```"):
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if not part:
                    continue
                try:
                    return json.loads(part)
                except json.JSONDecodeError:
                    pass
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(
                "Yandex Studio JSON parse error: %s | raw: %.300s", e, raw
            )
            return None


def get_llm_client(agent_type: str = "default") -> LLMClient:
    """
    Создать LLM-клиент по переменным окружения.

    agent_type: "block_parser" | "layout_composer" | "default"
    Используется для выбора нужного prompt_id в Yandex Studio.

    Перечитывает .env при каждом вызове — изменения вступают в силу
    без перезапуска приложения (авто-обновление конфигурации).
    """
    # Перечитываем .env при каждом вызове — авто-обновление без перезапуска.
    # Порядок: рядом с exe → AppData (выше приоритет)
    try:
        from dotenv import load_dotenv as _load_dotenv
        _frozen = getattr(sys, "frozen", False)
        if _frozen:
            _exe_env = os.path.join(os.path.dirname(sys.executable), ".env")
            if os.path.isfile(_exe_env):
                _load_dotenv(_exe_env, override=False)
        _local_env = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~"),
            "LabelFlow", ".env",
        )
        if os.path.isfile(_local_env):
            _load_dotenv(_local_env, override=True)
    except ImportError:
        pass

    # provider = os.environ.get("LLM_PROVIDER", "none").strip().lower()
    provider = "yandex"
    # api_key  = os.environ.get("LLM_API_KEY", "").strip()
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    logger.info("get_llm_client(%s): provider=%r, api_key=%s", agent_type, provider, "set" if api_key else "EMPTY")

    # ── Yandex AI Studio ──────────────────────────────────────────────────────
    if provider == "yandex":
        if not api_key:
            logger.warning("%s: LLM_PROVIDER=yandex, но LLM_API_KEY пустой → эвристика", agent_type)
            return HeuristicClient()

        # project = os.environ.get("YANDEX_PROJECT", "").strip()
        project = "b1gi8siplbhm25uhpdgm"

        prompt_env = {
            "block_parser":    "YANDEX_PROMPT_BLOCK_PARSER",
            "layout_composer": "YANDEX_PROMPT_LAYOUT_COMPOSER",
        }.get(agent_type, "YANDEX_PROMPT_DEFAULT")

        prompt_id = os.environ.get(prompt_env, "").strip()

        if not project:
            logger.warning("%s: YANDEX_PROJECT не задан → эвристика", agent_type)
            return HeuristicClient()
        if not prompt_id:
            logger.warning("%s: %s не задан → эвристика", agent_type, prompt_env)
            return HeuristicClient()

        logger.info("%s: Yandex Studio, prompt=%s", agent_type, prompt_id)
        return YandexStudioClient(api_key, prompt_id, project)

    # ── OpenAI ────────────────────────────────────────────────────────────────
    if provider == "openai" and api_key:
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini").strip()
        logger.info("%s: OpenAI model=%s", agent_type, model)
        return OpenAICompatibleClient(api_key, "https://api.openai.com/v1", model)

    # ── Alice / другой OpenAI-совместимый ─────────────────────────────────────
    if provider == "alice" and api_key:
        api_url = os.environ.get("LLM_API_URL", "").strip()
        model   = os.environ.get("LLM_MODEL", "yandexgpt-lite").strip()
        if api_url:
            logger.info("%s: Alice/compat url=%s model=%s", agent_type, api_url, model)
            return OpenAICompatibleClient(api_key, api_url, model)

    if provider not in ("none", ""):
        logger.warning("%s: LLM_PROVIDER='%s' — настройки неполные → эвристика", agent_type, provider)
    else:
        logger.info("%s: LLM_PROVIDER=none → эвристика", agent_type)

    return HeuristicClient()
