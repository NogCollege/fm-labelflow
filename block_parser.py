"""
block_parser.py - Разбивает сырой текст этикетки на типизированные блоки.

Режимы:
  - LLM (если LLM_PROVIDER настроен): отправляет промпт, получает JSON с блоками
  - Эвристика (fallback): regex + правила для русского текста этикеток

Пример входного текста — см. example.txt в папке проекта.
"""

from __future__ import annotations

import logging
import re
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional

from llm_client import get_llm_client, HeuristicClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Справочники типов и стилей по умолчанию
# ---------------------------------------------------------------------------

BLOCK_TYPES: dict[str, str] = {
    "category":   "Категория",
    "header":     "Заголовок",
    "subheader":  "Подзаголовок",
    "text":       "Текст",
    "list_text":  "Текст списком",
    "separator":  "Разделитель",
    "article":    "Артикул",
}

# Стили по умолчанию для каждого типа блока
DEFAULT_STYLES: dict[str, dict] = {
    "category":   {"bold": False, "uppercase": False, "align": "left"},
    "header":     {"bold": True,  "uppercase": False, "align": "center"},
    "subheader":  {"bold": True,  "uppercase": False, "align": "center"},
    "text":       {"bold": False, "uppercase": False, "align": "left"},
    "list_text":  {"bold": True,  "uppercase": False, "align": "left"},
    "separator":  {"bold": False, "uppercase": False, "align": "left"},
    "article":    {"bold": False, "uppercase": False, "align": "left"},
}


# ---------------------------------------------------------------------------
# Датакласс Block
# ---------------------------------------------------------------------------

@dataclass
class Block:
    id: str
    type: str
    text: str
    # Стиль — наследуется от DEFAULT_STYLES[type], может быть переопределён
    bold: bool = False
    italic: bool = False
    uppercase: bool = False
    align: str = "left"
    font: str = "Arial"
    font_size_mm: Optional[float] = None
    line_spacing: float = 1.0
    visible: bool = True

    @property
    def size_multiplier(self) -> float:
        # Legacy compatibility for old renderer code.
        return 1.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "type_label": BLOCK_TYPES.get(self.type, self.type),
            "text": self.text,
            "bold": self.bold,
            "italic": self.italic,
            "uppercase": self.uppercase,
            "align": self.align,
            "font": self.font,
            "font_size_mm": self.font_size_mm,
            "lineSpacing": self.line_spacing,
            "visible": self.visible,
        }

    @staticmethod
    def from_dict(d: dict) -> "Block":
        block_type = d.get("type", "text")
        defaults = DEFAULT_STYLES.get(block_type, DEFAULT_STYLES["text"])
        line_spacing = _clamp_float(
            _safe_float(d.get("lineSpacing", d.get("line_spacing")), 1.0),
            0.5,
            2.0,
            1.0,
        )
        if block_type == "separator":
            line_spacing = 1.0
        return Block(
            id=d.get("id") or str(uuid.uuid4())[:8],
            type=block_type,
            text=d.get("text", ""),
            bold=d.get("bold", defaults["bold"]),
            italic=d.get("italic", False),
            uppercase=d.get("uppercase", defaults["uppercase"]),
            align=d.get("align", defaults["align"]),
            font=d.get("font", "Arial"),
            font_size_mm=_clamp_float(_safe_float(d.get("font_size_mm")), 0.1, 100.0, None),
            line_spacing=line_spacing,
            visible=d.get("visible", True),
        )


def _safe_float(value, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp_float(value: Optional[float], min_value: float, max_value: float, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    return max(min_value, min(max_value, value))


def _make_block(block_type: str, text: str) -> Block:
    """Создать блок с дефолтными стилями для данного типа."""
    d = DEFAULT_STYLES.get(block_type, DEFAULT_STYLES["text"])
    return Block(
        id=str(uuid.uuid4())[:8],
        type=block_type,
        text=text.strip(),
        bold=d["bold"],
        uppercase=d["uppercase"],
        align=d["align"],
    )


# ---------------------------------------------------------------------------
# LLM-промпт
# ---------------------------------------------------------------------------

def _build_system_prompt(size_mm: tuple[float, float] | None = None) -> str:
    width_mm, height_mm = size_mm if size_mm else (46.0, 46.0)
    min_font = round(height_mm / 60, 2)
    max_font = round(height_mm / 15, 2)
    max_separator = round(height_mm / 2, 2)
    scale = height_mm / 46.0
    def_header = round(2.0 * scale, 2)
    def_subheader = round(1.5 * scale, 2)
    def_text = round(1.0 * scale, 2)
    return f"""Ты специалист по разметке текстов этикеток.
Твоя задача — превратить исходный текст в компактный набор блоков для макета этикетки размером {width_mm}x{height_mm} мм.
Сам текст не должен меняться: не сокращай, не дублируй и не придумывай новый текст. Допустимо убирать лишние переносы строк внутри блока.

Верни ТОЛЬКО JSON-массив объектов. Без markdown, пояснений и лишнего текста.

Строгая схема JSON:
[
  {{
    "type": "category|header|subheader|text|list_text|separator|article",
    "text": "строка",
    "bold": true,
    "italic": false,
    "uppercase": false,
    "align": "left|center|right",
    "font": "Arial|Times New Roman|Courier New",
    "font_size_mm": 1.0,
    "lineSpacing": 1.0
  }}
]

Правила по типам блоков:
- category: категория товара, короткая строка.
- header: главный заголовок, обычно 1-2 строки.
- subheader: подзаголовок, обычно 1-2 строки.
- list_text: только настоящий список; все пункты объединяй в один блок через "\\n".
- text: основной текст; объединяй близкие по смыслу части, не дроби без причины.
- article: артикул / SKU / строка с "Арт.", "Art.", "№".
- separator: пустой визуальный разрыв между смысловыми частями. У separator текст не печатается, поэтому ставь "text": " ". Размер этого блока управляет только высотой разрыва.

Правила по стилю:
- Используй размеры только в миллиметрах.
- lineSpacing для обычных текстовых блоков: от 0.5 до 2.0. По умолчанию 1.0.
- Для separator всегда lineSpacing = 1.0.
- font_size_mm для обычных блоков: от {min_font} до {max_font} мм.
- font_size_mm для separator: от {min_font} до {max_separator} мм.

Базовые рекомендуемые размеры для этого макета:
- header: {def_header} мм
- subheader: {def_subheader} мм
- category, text, list_text, article: {def_text} мм
- separator: {def_text} мм

Критично важные правила:
- Если объединяешь несколько строк в один блок, убирай лишние переносы строк.
- Если в тексте есть строки, начинающиеся с "-", "•", "*", "—", это должен быть list_text.
- separator используй только если в исходном тексте есть явные смысловые разрывы или пустые строки.
- Не делай слишком много блоков: чем компактнее и логичнее, тем лучше.

Очень важно по высоте:
- Суммарная оценочная высота всех блоков должна быть МЕНЬШЕ высоты макета {height_mm} мм.
- Оценивай высоту консервативно: для обычного блока примерно считай
  (число строк * font_size_mm * lineSpacing) + 0.3 мм запаса.
- Для separator высота блока примерно равна font_size_mm.
- Если видишь, что всё может не влезть, сначала уменьшай font_size_mm, потом убирай лишние separator, потом объединяй близкие text-блоки.
- Итог должен выглядеть реалистично и безопасно помещаться в макет, без переполнения.

Возвращай только валидный JSON-массив объектов по этой схеме."""


# ---------------------------------------------------------------------------
# Основной парсер
# ---------------------------------------------------------------------------

class BlockParser:

    def __init__(self):
        self._llm = get_llm_client("block_parser")

    def parse(self, text: str, size_mm: tuple[float, float] | None = None) -> list[Block]:
        """Разобрать текст на блоки. Если LLM доступен — использует его,
        иначе использует эвристику."""
        if not text or not text.strip():
            return []

        # Пересоздаём клиент при каждом вызове — подхватывает изменения .env без перезапуска
        self._llm = get_llm_client("block_parser")

        if not isinstance(self._llm, HeuristicClient):
            blocks = self._parse_with_llm(text, size_mm=size_mm)
            if blocks:
                return blocks

        return self._parse_heuristic(text)

    # ------------------------------------------------------------------
    # LLM режим
    # ------------------------------------------------------------------

    def _parse_with_llm(self, text: str, size_mm: tuple[float, float] | None = None) -> list[Block]:
        logger.info("_parse_with_llm: llm=%s", type(self._llm).__name__)
        result = self._llm.complete_json(_build_system_prompt(size_mm), text)
        logger.info("_parse_with_llm: result type=%s value=%.200s", type(result).__name__, str(result))

        # Некоторые модели могут вернуть JSON-массив как строку:
        # "[
        #   {...},
        #   {...}
        # ]"
        # В этом случае result будет str, а не list — попробуем распарсить ещё раз.
        if isinstance(result, str):
            try:
                tmp = result.strip()
                parsed = json.loads(tmp)
                if isinstance(parsed, list):
                    result = parsed
            except Exception:
                pass

        if not isinstance(result, list):
            return []
        blocks = []
        for item in result:
            if not isinstance(item, dict):
                continue
            block_type = str(item.get("type", "custom")).strip()
            block_text = str(item.get("text", "")).strip()
            if not block_text:
                continue
            if block_type not in BLOCK_TYPES:
                block_type = "text"
            # Применяем стили от LLM (если есть), иначе DEFAULT_STYLES.
            # None-значения не включаем — from_dict подставит дефолт.
            d: dict = {"type": block_type, "text": block_text}
            for key in ("bold", "italic", "uppercase", "align", "font", "font_size_mm", "lineSpacing"):
                val = item.get(key)
                if val is not None:
                    d[key] = val
            blocks.append(Block.from_dict(d))
        return blocks

    # ------------------------------------------------------------------
    # Эвристический режим
    # ------------------------------------------------------------------

    _RE_ARTICLE       = re.compile(r'^арт\.|art\.|артикул\b', re.I)
    _RE_BENEFIT_ITEM  = re.compile(r'^[-–—•*]\s*\S')
    _RE_ALL_CAPS      = re.compile(r'^[А-ЯA-ZЁ\s\d\-–/&,.!]+$')

    def _parse_heuristic(self, text: str) -> list[Block]:
        raw_lines = [ln.rstrip() for ln in text.splitlines()]

        blocks: list[Block] = []
        list_buf: list[str] = []
        text_buf: list[str] = []

        category_done = False
        header_done = False
        subheader_done = False

        def flush_text():
            nonlocal text_buf
            if text_buf:
                blocks.append(_make_block("text", "\n".join(text_buf).strip()))
                text_buf = []

        def flush_list():
            nonlocal list_buf
            if list_buf:
                blocks.append(_make_block("list_text", "\n".join(list_buf).strip()))
                list_buf = []

        blank_streak = 0
        for raw in raw_lines:
            ln = raw.strip()
            if not ln:
                blank_streak += 1
                if blank_streak >= 1:
                    # Пустая строка — разделитель между смысловыми частями
                    flush_list()
                    flush_text()
                    # Не плодим много разделителей подряд
                    if not blocks or blocks[-1].type != "separator":
                        blocks.append(_make_block("separator", " "))
                continue

            blank_streak = 0

            # Артикул — всегда отдельным блоком
            if self._RE_ARTICLE.match(ln):
                flush_list()
                flush_text()
                blocks.append(_make_block("article", ln))
                continue

            # Пункты списка
            if self._RE_BENEFIT_ITEM.match(ln):
                flush_text()
                list_buf.append(ln)
                continue
            else:
                flush_list()

            # Категория (первая короткая строка)
            if not category_done and len(ln) <= 40:
                blocks.append(_make_block("category", ln))
                category_done = True
                continue

            # Заголовок (CAPS или короткая строка)
            if not header_done and (self._RE_ALL_CAPS.match(ln) or len(ln) <= 55):
                blocks.append(_make_block("header", ln))
                header_done = True
                continue

            # Подзаголовок (следующая строка умеренной длины)
            if not subheader_done and len(ln) <= 90:
                blocks.append(_make_block("subheader", ln))
                subheader_done = True
                continue

            # Всё остальное — в общий текстовый буфер
            text_buf.append(ln)

        flush_list()
        flush_text()

        # Уберём разделитель в конце, если он последний
        while blocks and blocks[-1].type == "separator":
            blocks.pop()

        return blocks
