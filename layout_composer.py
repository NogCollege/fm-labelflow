"""
layout_composer.py - Генерирует варианты компоновки этикетки.

Принимает список Block + size_id, возвращает 4 LayoutVariant.

Режимы:
  - LLM: просит нейросеть предложить 4 варианта компоновки
  - Эвристика (fallback): 4 предопределённых шаблона
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from block_parser import Block, BLOCK_TYPES
from llm_client import get_llm_client, HeuristicClient

logger = logging.getLogger(__name__)


@dataclass
class LayoutVariant:
    name: str
    qr_position: str = "bottom_right"
    symbols_position: str = "auto"
    blocks: list[dict] = field(default_factory=list)
    experimental: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "qr_position": self.qr_position,
            "symbols_position": self.symbols_position,
            "blocks": self.blocks,
            "experimental": self.experimental,
        }


def _block_to_variant_dict(block: Block, **updates) -> dict:
    data = block.to_dict()
    data.pop("type_label", None)
    data["visible"] = True
    for key, value in updates.items():
        data[key] = value
    return data


def _clamp_mm(value: float, min_value: float = 0.2, max_value: float = 12.0) -> float:
    return max(min_value, min(max_value, round(float(value), 2)))


def _resized(block: Block, factor: float = 1.0, delta_mm: float = 0.0, **updates) -> dict:
    base = float(block.font_size_mm or 1.0)
    return _block_to_variant_dict(
        block,
        font_size_mm=_clamp_mm(base * factor + delta_mm),
        **updates,
    )


def _sorted_blocks(blocks: list[Block], priorities: dict[str, int]) -> list[Block]:
    return sorted(blocks, key=lambda b: (priorities.get(b.type, 99),))


def _important_blocks(blocks: list[Block]) -> list[Block]:
    keywords = ("состав", "ingredients", "изготов", "производ", "manufacturer", "импортер", "import", "article", "арт")
    selected: list[Block] = []
    seen: set[str] = set()
    for block in blocks:
        text = (block.text or "").lower()
        if block.type in {"header", "subheader", "article"} or any(k in text for k in keywords):
            if block.id in seen:
                continue
            seen.add(block.id)
            selected.append(block)
    if not selected:
        return blocks[: min(3, len(blocks))]
    return selected


def _experimental_blocks(blocks: list[Block]) -> list[dict]:
    chosen = _important_blocks(blocks)
    out: list[dict] = []
    for block in chosen:
        text = " ".join((block.text or "").split())
        if block.type == "header":
            out.append(_resized(block, factor=1.2, bold=True, align="center", text=text))
        elif block.type == "subheader":
            out.append(_resized(block, factor=1.05, bold=True, align="center", text=text))
        elif block.type == "article":
            out.append(_resized(block, factor=0.95, align="right", text=text))
        else:
            out.append(_resized(block, factor=0.95, lineSpacing=0.95, text=text))
    return out


def _preset_variants(blocks: list[Block]) -> list[LayoutVariant]:
    original = [_block_to_variant_dict(b) for b in blocks]

    focus_blocks = []
    for block in _sorted_blocks(blocks, {
        "header": 0, "subheader": 1, "list_text": 2, "text": 3, "category": 4, "article": 5, "separator": 6
    }):
        if block.type == "header":
            focus_blocks.append(_resized(block, factor=1.18, bold=True, align="center"))
        elif block.type == "subheader":
            focus_blocks.append(_resized(block, factor=1.08, bold=True, align="center"))
        elif block.type == "list_text":
            focus_blocks.append(_resized(block, factor=1.12, bold=True, lineSpacing=1.0))
        elif block.type == "text":
            focus_blocks.append(_resized(block, factor=0.9, lineSpacing=0.95))
        else:
            focus_blocks.append(_block_to_variant_dict(block))

    info_blocks = []
    for block in _sorted_blocks(blocks, {
        "category": 0, "header": 1, "subheader": 2, "text": 3, "list_text": 4, "article": 5, "separator": 6
    }):
        if block.type == "header":
            info_blocks.append(_resized(block, factor=0.98, bold=True, align="left"))
        elif block.type == "subheader":
            info_blocks.append(_resized(block, factor=0.92, bold=False, align="left"))
        elif block.type == "text":
            info_blocks.append(_resized(block, factor=0.9, lineSpacing=0.9))
        elif block.type == "list_text":
            info_blocks.append(_resized(block, factor=0.88, lineSpacing=0.9))
        else:
            info_blocks.append(_block_to_variant_dict(block))

    return [
        LayoutVariant(
            name="Классический",
            qr_position="bottom_right",
            symbols_position="top_left",
            blocks=original,
            experimental=False,
        ),
        LayoutVariant(
            name="Акцент на списке",
            qr_position="top_left",
            symbols_position="bottom_right",
            blocks=focus_blocks,
            experimental=False,
        ),
        LayoutVariant(
            name="Информационный",
            qr_position="bottom_left",
            symbols_position="top_right",
            blocks=info_blocks,
            experimental=False,
        ),
        LayoutVariant(
            name="Экспериментальный",
            qr_position="top_right",
            symbols_position="bottom_left",
            blocks=_experimental_blocks(blocks),
            experimental=True,
        ),
    ]


_SYSTEM_PROMPT_COMPOSER = """Ты дизайнер этикеток.
Сейчас нужно сгенерировать ТОЛЬКО варианты: {variant_request}
Для этикетки размера {size} мм.

Ты получаешь:
- список блоков с id, типом, текстом и текущими стилями;
- информацию о количестве графических символов и их размере;
- размер QR-кода.

Требования:
- Верни ТОЛЬКО JSON-массив из 2 объектов.
- Если требуется вариант 1, 2 или 3:
  - он должен содержать ВСЕ исходные блоки;
  - он не должен скрывать блоки;
  - он не должен терять текст.
- Варианты 1, 2, 3 можно делать разными за счёт:
  - перестановки блоков местами;
  - разных qr_position;
  - разных symbols_position;
  - разных font_size_mm у блоков;
  - разных lineSpacing;
  - разных bold / italic / uppercase / align.
- Варианты 1, 2, 3 должны быть реально не похожи друг на друга.
- Нельзя делать почти одинаковые макеты с разными углами QR.

Если требуется вариант 4, он должен быть экспериментальным:
- здесь разрешена полная свобода;
- можно оставить только самую важную информацию;
- можно менять или сокращать второстепенные тексты;
- можно убрать описание, список, слоган и прочее второстепенное.

Очень важно:
- Варианты 1, 2, 3: каждый исходный id должен встретиться ровно один раз.
- Вариант 4: можно использовать подмножество блоков или новые id вида exp_1.
- Следи, чтобы итог по высоте выглядел реалистично и влезал в макет.
- Если символов много или QR крупный, делай текст компактнее.

Допустимые позиции:
- qr_position: bottom_right, bottom_left, top_right, top_left, bottom_center
- symbols_position: auto, bottom_right, bottom_left, top_right, top_left, bottom_center

Доступные типы блоков:
{type_list}

Формат ответа:
[
  {{
    "name": "Название варианта",
    "experimental": false,
    "qr_position": "bottom_right",
    "symbols_position": "top_left",
    "blocks": [
      {{
        "id": "block_id",
        "type": "header",
        "text": "Текст блока",
        "bold": true,
        "italic": false,
        "uppercase": false,
        "align": "center",
        "font": "Arial",
        "font_size_mm": 2.0,
        "lineSpacing": 1.0,
        "visible": true
      }}
    ]
  }}
]

Возвращай только JSON."""


class LayoutComposer:

    def __init__(self):
        self._llm = get_llm_client("layout_composer")

    def compose(
        self,
        blocks: list[Block],
        size_id: str,
        graphic_symbols: Optional[list[str]] = None,
        qr_size_percent: int = 100,
        symbol_size_percent: int = 100,
    ) -> list[LayoutVariant]:
        """Вернуть 4 варианта компоновки для данных блоков и размера."""
        # Пересоздаём клиент при каждом вызове — подхватывает изменения .env без перезапуска
        self._llm = get_llm_client("layout_composer")

        if not isinstance(self._llm, HeuristicClient) and blocks:
            pair12 = self._compose_with_llm_pair(
                blocks,
                size_id,
                graphic_symbols or [],
                qr_size_percent,
                symbol_size_percent,
                requested_numbers=(1, 2),
            )
            pair34 = self._compose_with_llm_pair(
                blocks,
                size_id,
                graphic_symbols or [],
                qr_size_percent,
                symbol_size_percent,
                requested_numbers=(3, 4),
            )
            variants = pair12 + pair34
            if len(variants) >= 4:
                return variants[:4]

        return _preset_variants(blocks)

    def _compose_with_llm_pair(
        self,
        blocks: list[Block],
        size_id: str,
        graphic_symbols: list[str],
        qr_size_percent: int,
        symbol_size_percent: int,
        requested_numbers: tuple[int, int],
    ) -> list[LayoutVariant]:
        type_list = "\n".join(f"  {k}: {v}" for k, v in BLOCK_TYPES.items())
        size_label = size_id.replace("x", " × ")
        request_label = f"{requested_numbers[0]} и {requested_numbers[1]}"
        system = _SYSTEM_PROMPT_COMPOSER.format(
            size=size_label,
            type_list=type_list,
            variant_request=request_label,
        )

        blocks_summary = "\n".join(
            f"- id={b.id} type={b.type} size={b.font_size_mm}mm text={b.text[:120]}"
            for b in blocks
        )
        symbols_summary = ", ".join(graphic_symbols) if graphic_symbols else "нет"
        user_msg = (
            f"Нужно вернуть только варианты: {request_label}\n"
            f"Размер этикетки: {size_label} мм\n"
            f"QR размер: {qr_size_percent}%\n"
            f"Символы: {len(graphic_symbols)} шт. ({symbols_summary})\n"
            f"Размер символов: {symbol_size_percent}%\n\n"
            f"Блоки:\n{blocks_summary}"
        )
        raw = self._llm.complete_json(system, user_msg)
        presets = _preset_variants(blocks)
        if not isinstance(raw, list) or len(raw) < 1:
            return [presets[requested_numbers[0] - 1], presets[requested_numbers[1] - 1]]

        source_by_id = {b.id: b for b in blocks}
        variants_by_slot: list[Optional[LayoutVariant]] = [None, None]
        target_numbers = list(requested_numbers)
        for idx, item in enumerate(raw[:2]):
            if not isinstance(item, dict):
                continue
            try:
                variant_number = target_numbers[idx]
                experimental = variant_number == 4 or bool(item.get("experimental", False))
                variant_blocks = self._normalize_variant_blocks(
                    item.get("blocks") or [],
                    blocks,
                    source_by_id,
                    experimental,
                )
                if not variant_blocks:
                    continue
                variants_by_slot[idx] = LayoutVariant(
                    name=str(item.get("name", f"Вариант {target_numbers[idx]}")),
                    qr_position=str(item.get("qr_position", "bottom_right")),
                    symbols_position=str(item.get("symbols_position", "auto")),
                    blocks=variant_blocks,
                    experimental=experimental,
                )
            except Exception as e:
                logger.warning("LLM variant parse error: %s", e)

        for idx, variant in enumerate(variants_by_slot):
            if variant is None:
                variants_by_slot[idx] = presets[requested_numbers[idx] - 1]

        return [v for v in variants_by_slot if v is not None][:2]

    def _normalize_variant_blocks(
        self,
        raw_blocks: list,
        source_blocks: list[Block],
        source_by_id: dict[str, Block],
        experimental: bool,
    ) -> list[dict]:
        if not isinstance(raw_blocks, list):
            return []

        normalized: list[dict] = []
        seen_ids: set[str] = set()
        for item in raw_blocks:
            if not isinstance(item, dict):
                continue

            raw_id = str(item.get("id", "")).strip()
            source = source_by_id.get(raw_id)

            if source is not None:
                base = _block_to_variant_dict(source)
            elif experimental:
                block_type = str(item.get("type", "text")).strip()
                if block_type not in BLOCK_TYPES and block_type != "custom":
                    block_type = "text"
                base = {
                    "id": raw_id or f"exp_{len(normalized)+1}",
                    "type": block_type,
                    "text": str(item.get("text", "")).strip() or " ",
                    "bold": bool(item.get("bold", False)),
                    "italic": bool(item.get("italic", False)),
                    "uppercase": bool(item.get("uppercase", False)),
                    "align": str(item.get("align", "left")),
                    "font": str(item.get("font", "Arial")),
                    "font_size_mm": _clamp_mm(float(item.get("font_size_mm", 1.0))),
                    "lineSpacing": max(0.5, min(2.0, float(item.get("lineSpacing", 1.0)))),
                    "visible": True,
                }
            else:
                continue

            for key in ("type", "text", "bold", "italic", "uppercase", "align", "font", "visible"):
                if key in item and item[key] is not None:
                    base[key] = item[key]

            if "font_size_mm" in item and item["font_size_mm"] is not None:
                try:
                    base["font_size_mm"] = _clamp_mm(float(item["font_size_mm"]))
                except Exception:
                    pass

            if "lineSpacing" in item and item["lineSpacing"] is not None:
                try:
                    base["lineSpacing"] = max(0.5, min(2.0, float(item["lineSpacing"])))
                except Exception:
                    pass

            if base.get("type") == "separator":
                base["text"] = " "
                base["lineSpacing"] = 1.0

            if not experimental and source is not None:
                # For non-experimental variants keep source text and preserve all ids exactly once.
                base["text"] = source.text

            block_id = str(base.get("id", "")).strip()
            if not block_id or block_id in seen_ids:
                continue
            seen_ids.add(block_id)
            normalized.append(base)

        if experimental:
            return normalized

        if len(normalized) != len(source_blocks):
            return []

        source_ids = {b.id for b in source_blocks}
        if seen_ids != source_ids:
            return []

        return normalized
