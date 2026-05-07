"""
label_layout.py - Макет этикетки (стиль L'Etoile)
С метриками Arial и адаптивным соотношением текст/графика 80-90% / 10-20%
Версия 2.1
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from typing import List, Dict, Tuple, Optional
import math
import os
import sys
import base64
from xml.sax.saxutils import escape


# Пути к шрифтам (regular + bold) по имени семейства
FONT_PATHS: dict[str, dict[str, list[str]]] = {
    "Arial": {
        "regular": ["arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
        "bold":    ["arialbd.ttf", "Arial Bold.ttf", "arialb.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    },
    "Times New Roman": {
        "regular": ["times.ttf", "Times New Roman.ttf", "timesnewroman.ttf", "FreeSerif.ttf"],
        "bold":    ["timesbd.ttf", "Times New Roman Bold.ttf", "timesb.ttf", "FreeSerifBold.ttf"],
    },
    "Courier New": {
        "regular": ["cour.ttf", "Courier New.ttf", "couriernew.ttf", "FreeMono.ttf"],
        "bold":    ["courbd.ttf", "Courier New Bold.ttf", "courierb.ttf", "FreeMonoBold.ttf"],
    },
}

# Предопределенные размеры этикеток (мм -> px при 600 dpi)
PREDEFINED_SIZES = {
    "20x10": {"width_mm": 20, "height_mm": 10, "width_px": 472, "height_px": 236, "name": "20x10 мм"},
    "20x15": {"width_mm": 20, "height_mm": 15, "width_px": 472, "height_px": 354, "name": "20x15 мм"},
    "15x17": {"width_mm": 15, "height_mm": 17, "width_px": 354, "height_px": 401, "name": "15x17 мм"},
    "22x22": {"width_mm": 22, "height_mm": 22, "width_px": 519, "height_px": 519, "name": "22x22 мм"},
    "40x15": {"width_mm": 40, "height_mm": 15, "width_px": 944, "height_px": 354, "name": "40x15 мм"},
    "40x20": {"width_mm": 40, "height_mm": 20, "width_px": 944, "height_px": 472, "name": "40x20 мм"},
    "40x30": {"width_mm": 40, "height_mm": 30, "width_px": 944, "height_px": 708, "name": "40x30 мм"},
    "46x46": {"width_mm": 46, "height_mm": 46, "width_px": 1086, "height_px": 1086, "name": "46x46 мм"},
    "48x20": {"width_mm": 48, "height_mm": 20, "width_px": 1133, "height_px": 472, "name": "48x20 мм"},
    "48x30": {"width_mm": 48, "height_mm": 30, "width_px": 1133, "height_px": 708, "name": "48x30 мм"},
    "48x40": {"width_mm": 48, "height_mm": 40, "width_px": 1133, "height_px": 944, "name": "48x40 мм"},
    "55x35": {"width_mm": 55, "height_mm": 35, "width_px": 1299, "height_px": 826, "name": "55x35 мм"},
    "58x60": {"width_mm": 58, "height_mm": 60, "width_px": 1370, "height_px": 1417, "name": "58x60 мм"},
    "100x40": {"width_mm": 100, "height_mm": 40, "width_px": 2362, "height_px": 944, "name": "100x40 мм"},
    "100x35": {"width_mm": 100, "height_mm": 35, "width_px": 2362, "height_px": 826, "name": "100x35 мм"},
    "74x98": {"width_mm": 74, "height_mm": 98, "width_px": 1748, "height_px": 2314, "name": "74x98 мм"},
    "99.5x99.5": {"width_mm": 99.5, "height_mm": 99.5, "width_px": 2350, "height_px": 2350, "name": "99.5x99.5 мм"},
}

# Коэффициент пересчета: 1 мм = 600 / 25.4 px
MM_TO_PX = 600 / 25.4
SMALL_TEXT_SIZE_KEYS = {"20x10", "20x15", "15x17", "22x22"}
def _get_resource_dir() -> str:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


SYMBOL_ASSET_DIR = os.path.join(_get_resource_dir(), "assets", "symbols")

def _build_symbol_asset_candidates() -> Dict[str, List[str]]:
    """Все символы из папки assets/symbols: id (имя без расширения) -> список имён файлов."""
    exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    out: Dict[str, List[str]] = {}
    if not os.path.isdir(SYMBOL_ASSET_DIR):
        return out
    for name in sorted(os.listdir(SYMBOL_ASSET_DIR)):
        base, ext = os.path.splitext(name)
        if ext.lower() not in exts or not base:
            continue
        stem = base.strip().lower()
        out.setdefault(stem, []).append(name)
    return out

SYMBOL_ASSET_CANDIDATES = _build_symbol_asset_candidates()


class ArialMetrics:
    """Точные метрики шрифта Arial"""
    
    # Таблица кегля в мм (из требования). Пересчёт: 1 мм = 600 / 25.4 px.
    FONT_MM_VALUES = [0.6, 0.8, 0.9, 1.0, 1.2, 1.3, 1.4, 1.5, 1.8, 2.5, 3.0]
    TITLE_MM_VALUES = [3.0, 2.5]
    BODY_MM_VALUES = [1.8, 1.5, 1.4, 1.3, 1.2, 1.0, 0.9, 0.8, 0.6]
    MICRO_PX_VALUES = [4, 3]
    # Для Arial Regular:
    # - заглавные ~72-73% кегля
    # - строчные ~52-53% кегля
    # - воздух ~25-30% кегля
    UPPERCASE_RATIO = 0.725
    LOWERCASE_RATIO = 0.525
    AIR_RATIO = 0.275
    
    # Для жирного начертания ширина увеличивается на 15-20%
    CHAR_WIDTH_RATIO = {
        'regular': 1.0,
        'bold': 1.18,  # +18%
    }
    
    # Средняя ширина символа (в % от высоты заглавной)
    AVG_CHAR_WIDTH_RATIO = 0.65

    @classmethod
    def _closest_font_px(cls, font_size_px: int) -> int:
        size_table_px = [max(5, int(round(mm * MM_TO_PX))) for mm in cls.FONT_MM_VALUES]
        return min(size_table_px, key=lambda x: abs(x - font_size_px))

    @classmethod
    def body_px_scale_desc(cls, allow_micro: bool = False) -> List[int]:
        scale = [max(5, int(round(mm * MM_TO_PX))) for mm in cls.BODY_MM_VALUES]
        if allow_micro:
            scale.extend(cls.MICRO_PX_VALUES)
        # unique + desc
        return sorted(set(scale), reverse=True)

    @classmethod
    def title_px_scale_desc(cls) -> List[int]:
        return [max(5, int(round(mm * MM_TO_PX))) for mm in cls.TITLE_MM_VALUES]
    
    @classmethod
    def get_char_height(cls, font_size_px: int, is_uppercase: bool = True) -> float:
        """Возвращает высоту символа в пикселях"""
        # В supersample-режиме нельзя привязываться к дискретной таблице:
        # иначе кегль и интерлиньяж оказываются в разных шкалах.
        real_size = max(1.0, float(font_size_px))
        if is_uppercase:
            return real_size * cls.UPPERCASE_RATIO
        return real_size * cls.LOWERCASE_RATIO
    
    @classmethod
    def get_line_height(cls, font_size_px: int) -> float:
        """Возвращает полную высоту строки"""
        real_size = max(1.0, float(font_size_px))
        uppercase = real_size * cls.UPPERCASE_RATIO
        air = real_size * cls.AIR_RATIO
        return uppercase + air
    
    @classmethod
    def estimate_text_width(cls, text: str, font_size_px: int, is_bold: bool = False) -> int:
        """Оценивает ширину текста"""
        char_height = cls.get_char_height(font_size_px, True)
        avg_char_width = char_height * cls.AVG_CHAR_WIDTH_RATIO
        width_mult = cls.CHAR_WIDTH_RATIO['bold'] if is_bold else cls.CHAR_WIDTH_RATIO['regular']
        
        space_count = text.count(' ')
        text_without_spaces = text.replace(' ', '')
        
        width = len(text_without_spaces) * avg_char_width * width_mult
        width += space_count * (avg_char_width * 0.33)
        
        return int(width)


class GraphicElement:
    """Графический элемент на этикетке"""
    
    def __init__(
        self,
        element_type: str,
        size_mm: float = 8,
        required: bool = True,
        mm_to_px: float = MM_TO_PX,
        anchor: Optional[str] = None
    ):
        self.type = element_type
        self.size_mm = size_mm
        self.size_px = int(round(size_mm * mm_to_px))
        self.required = required
        self.anchor = anchor
        self.x = 0
        self.y = 0
        self.placed = False
    
    def to_dict(self) -> Dict:
        return {
            'type': self.type,
            'size_mm': self.size_mm,
            'size_px': self.size_px,
            'required': self.required,
            'anchor': self.anchor,
            'x': self.x,
            'y': self.y
        }


class EtoileLabelLayout:
    """
    Макет этикетки в стиле L'Etoile
    """
    
    QR_POSITION_OPTIONS = {'auto', 'top_left', 'top_right', 'bottom_left', 'bottom_center', 'bottom_right'}
    SYMBOLS_POSITION_OPTIONS = {'auto', 'top_left', 'top_right', 'bottom_left', 'bottom_center', 'bottom_right'}

    def __init__(self, size_key: str = "46x46", supersample: float = 1.0, keep_target_size: bool = True, font_family: str = "Arial"):
        if size_key not in PREDEFINED_SIZES:
            raise ValueError(f"Неизвестный размер: {size_key}. Доступны: {list(PREDEFINED_SIZES.keys())}")
        
        self.size_key = size_key
        self.size_info = PREDEFINED_SIZES[size_key]
        self.supersample = max(1.0, float(supersample))
        self.keep_target_size = bool(keep_target_size)
        self.width_mm = self.size_info["width_mm"]
        self.height_mm = self.size_info["height_mm"]
        self.target_width_px = self.size_info["width_px"]
        self.target_height_px = self.size_info["height_px"]
        self.width_px = int(round(self.target_width_px * self.supersample))
        self.height_px = int(round(self.target_height_px * self.supersample))
        self.small_text_layout = size_key in SMALL_TEXT_SIZE_KEYS
        
        self.mm_to_px = MM_TO_PX * self.supersample
        self.margin = self.mm(0.8)
        self.text_padding = self.mm(0.55)
        self.zone_gap = self.mm(0.45)
        self.block_gap = self.mm(0.35)
        self.graphics_safe_padding = self.mm(0.25)

        if self.small_text_layout:
            # Компактные отступы для максимального заполнения области.
            # Цель: ~10 px от края, остальное — текст + четкий QR.
            self.margin = 10
            self.text_padding = 0
            self.zone_gap = 2
            self.block_gap = 2
            self.graphics_safe_padding = 2

        self.work_x = self.margin
        self.work_y = self.margin
        self.work_width = self.width_px - 2 * self.margin
        self.work_height = self.height_px - 2 * self.margin
        self.text_bottom_limit = self.work_y + self.work_height - self.zone_gap
        self.last_text_end_y = self.work_y + self.text_padding
        
        # Больше пространства отдаём тексту.
        self.text_area_ratio = 0.95
        self.graphics_area_ratio = 0.05
        
        self.graphic_elements: List[GraphicElement] = []
        self.qr_position = 'auto'
        self.symbols_position = 'auto'
        self.symbol_positions: Dict[str, str] = {}
        self.symbol_image_cache: Dict[str, Optional[Image.Image]] = {}
        
        # Кэш шрифтов
        self.font_cache = {}
        self.font_family = font_family if font_family in FONT_PATHS else "Arial"
        _fp = FONT_PATHS[self.font_family]
        self.font_regular_path = self._find_font_path(_fp["regular"])
        self.font_bold_path = self._find_font_path(_fp["bold"]) or self.font_regular_path
        self.font_regular = self._load_font(self.font_regular_path, 10)
        self.font_bold = self._load_font(self.font_bold_path, 10) or self.font_regular
        
        print(f"\n📐 Этикетка: {self.size_info['name']} → {self.target_width_px}x{self.target_height_px} px")
        if self.supersample > 1.0:
            print(f"   Supersample x{self.supersample:.1f}: {self.width_px}x{self.height_px} px")
        print(f"   Рабочая область: {self.work_width}x{self.work_height} px")
        print(f"   Соотношение: текст {self.text_area_ratio*100:.0f}% / графика {self.graphics_area_ratio*100:.0f}%")

    def mm(self, value_mm: float) -> int:
        """Конвертирует миллиметры в пиксели по формуле 1 мм = 600 / 25.4 px."""
        return max(1, int(round(value_mm * self.mm_to_px)))
    
    def _find_font_path(self, font_names: List[str]) -> Optional[str]:
        """Ищет доступный файл шрифта по списку кандидатов."""
        for font_name in font_names:
            font_paths = [
                font_name,
                f"C:/Windows/Fonts/{font_name}",
                f"/usr/share/fonts/truetype/msttcorefonts/{font_name}",
                f"/usr/share/fonts/truetype/liberation/{font_name}",
                f"/System/Library/Fonts/{font_name}",
                f"/Library/Fonts/{font_name}",
            ]
            for path in font_paths:
                if os.path.exists(path):
                    return path
        return None

    def _load_font(self, font_path: Optional[str], size_px: int) -> Optional[ImageFont.FreeTypeFont]:
        """Загружает шрифт конкретного размера."""
        if not font_path:
            return None
        try:
            return ImageFont.truetype(font_path, size_px)
        except:
            return None
    
    def get_font(self, size_px: int, bold: bool = False, font_family: str | None = None) -> ImageFont.FreeTypeFont:
        """Получает шрифт нужного размера. font_family=None → текущее семейство экземпляра."""
        family = font_family if font_family and font_family in FONT_PATHS else self.font_family
        cache_key = f"{family}_{size_px}_{bold}"

        if cache_key in self.font_cache:
            return self.font_cache[cache_key]

        if family == self.font_family:
            font_path = self.font_bold_path if bold else self.font_regular_path
        else:
            _fp = FONT_PATHS[family]
            font_path = self._find_font_path(_fp["bold"] if bold else _fp["regular"])

        font = self._load_font(font_path, size_px)
        if font is None:
            try:
                fallback = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
                font = ImageFont.truetype(fallback, size_px)
            except Exception:
                font = ImageFont.load_default()

        self.font_cache[cache_key] = font
        return font
    
    def add_graphic_element(self, element_type: str, size_mm: float = 8, required: bool = True, anchor: Optional[str] = None):
        """Добавляет графический элемент"""
        self.graphic_elements.append(GraphicElement(element_type, size_mm, required, mm_to_px=self.mm_to_px, anchor=anchor))
    
    def calculate_required_graphics_area(self) -> int:
        """Рассчитывает площадь для графики"""
        total_area = 0
        for element in self.graphic_elements:
            element_area = element.size_px * element.size_px
            safe_area = (element.size_px + 2 * self.graphics_safe_padding) ** 2
            total_area += max(element_area, safe_area)
        return total_area

    def update_content_ratios(self):
        """Обновляет соотношения: графика 6%, текст 94%."""
        work_area = self.work_width * self.work_height
        if work_area <= 0:
            self.text_area_ratio = 0.94
            self.graphics_area_ratio = 0.06
            return

        # Фиксируем компактную область под графику.
        if self.small_text_layout:
            self.graphics_area_ratio = 0.04
            self.text_area_ratio = 0.96
        else:
            self.graphics_area_ratio = 0.06
            self.text_area_ratio = 0.94

    def _qr_size_mm(self) -> float:
        """QR меньше на сверхмалых этикетках, чтобы освободить место под текст."""
        min_side = min(self.width_mm, self.height_mm)
        if self.small_text_layout:
            return max(2.5, min(3.2, min_side * 0.08))
        return max(4.5, min(10.0, min_side * 0.10))

    def _min_body_font_px(self) -> int:
        scale = ArialMetrics.body_px_scale_desc(allow_micro=True)
        if not scale:
            return 3
        return max(1, int(min(scale)))

    def _estimate_max_words(self) -> int:
        """Грубая оценка допустимого количества слов при минимальном кегле."""
        min_font_px = self._min_body_font_px()
        work_area = max(1, self.work_width * self.work_height)
        ratio = 0.04 if self.small_text_layout else self.graphics_area_ratio
        reserved_graphics_area = int(work_area * ratio)
        available_area = max(1, work_area - reserved_graphics_area)

        char_h = ArialMetrics.get_char_height(min_font_px, True)
        char_w = max(1.0, char_h * ArialMetrics.AVG_CHAR_WIDTH_RATIO)
        line_h = max(1.0, ArialMetrics.get_line_height(min_font_px) * 1.06)

        max_chars = available_area / max(1.0, (char_w * line_h))
        avg_word_len = 6.0
        max_words = int(max_chars / (avg_word_len + 1.0))
        return max(3, max_words)

    def _limit_text_blocks_for_small_sizes(self, text_blocks: List[Dict]) -> List[Dict]:
        """Ограничивает объем читаемого текста на сверхмалых этикетках."""
        if not text_blocks:
            return text_blocks

        # Убираем второстепенные блоки, чтобы освободить место под читаемый текст.
        filtered = [b for b in text_blocks if b.get('type') not in {'short_code', 'batch', 'expiry'}]
        if not filtered:
            filtered = list(text_blocks)

        max_words = self._estimate_max_words()
        remaining = max_words
        out: List[Dict] = []
        for block in filtered:
            text = str(block.get('text', '')).strip()
            if not text:
                continue
            words = text.split()
            if remaining <= 0:
                continue
            if len(words) > remaining:
                words = words[:remaining]
            remaining -= len(words)
            new_block = dict(block)
            new_block['text'] = ' '.join(words)
            out.append(new_block)

        return out
    
    def optimize_text_font_size(self, text_blocks: List[Dict]) -> int:
        """Находит лучший размер: чем больше площадь, тем крупнее шрифт."""
        required_graphics_area = self.calculate_required_graphics_area()
        work_area = self.work_width * self.work_height
        rules_area = self.target_width_px * self.target_height_px
        reserved_graphics_area = max(required_graphics_area, int(work_area * self.graphics_area_ratio))
        available_text_area = max(1, work_area - reserved_graphics_area)

        total_chars = sum(len(block.get('text', '')) for block in text_blocks)
        if total_chars == 0:
            return 8

        char_width_ratio = 0.65
        raw_optimal = math.sqrt(available_text_area / (total_chars * char_width_ratio))

        # Корректировка для больших этикеток.
        if rules_area >= 300000:
            raw_optimal *= 1.45
        elif rules_area >= 200000:
            raw_optimal *= 1.35
        elif rules_area >= 140000:
            raw_optimal *= 1.25

        allow_micro = rules_area <= 120000
        body_scale = self._body_size_candidates()

        optimal_size = body_scale[-1]
        for size_px in body_scale:
            if raw_optimal >= size_px:
                optimal_size = size_px
                break

        # Защита от слишком мелкого шрифта, если текста не так много.
        if rules_area >= 200000 and total_chars <= 10000:
            optimal_size = max(optimal_size, self._scale_font_px(9))
        elif rules_area >= 140000 and total_chars <= 8000:
            optimal_size = max(optimal_size, self._scale_font_px(8))

        # Ограничиваем верхний порог, чтобы не получать "кашу" при больших px-размерах макета.
        max_readable = self._max_readable_body_font_px()
        optimal_size = min(optimal_size, max_readable)

        print(f"   Оптимизация: {total_chars} символов, кегль {optimal_size}px (область={work_area})")
        return optimal_size


    def _body_size_candidates(self) -> List[int]:
        work_area = self.target_width_px * self.target_height_px
        allow_micro = True
        base = [self._scale_font_px(px) for px in ArialMetrics.body_px_scale_desc(allow_micro=allow_micro)]
        base = sorted(
            set(base + [self._scale_font_px(8), self._scale_font_px(7), self._scale_font_px(6)]),
            reverse=True
        )

        # Для крупных этикеток добавляем значительно более крупные body-кегли.
        if work_area >= 500000:
            base = sorted(set([self._scale_font_px(v) for v in [30, 28, 26, 24, 22, 20, 18, 16]] + base), reverse=True)
        elif work_area >= 300000:
            base = sorted(set([self._scale_font_px(v) for v in [26, 24, 22, 20, 18, 16]] + base), reverse=True)
        elif work_area >= 200000:
            base = sorted(set([self._scale_font_px(v) for v in [22, 20, 18, 16]] + base), reverse=True)

        return base

    def _scale_font_px(self, px: int) -> int:
        return max(1, int(round(px * self.supersample)))

    def _max_readable_body_font_px(self) -> int:
        """Верхняя граница body-кегля для читаемого плотного набора."""
        shortest_side = max(1, min(self.width_px, self.height_px))
        # Эмпирический предел: ~4.5% короткой стороны.
        cap = int(round(shortest_side * 0.045))
        return max(self._scale_font_px(8), cap)

    def _font_try_order(self, base_font_size: int) -> List[int]:
        candidates = self._body_size_candidates()
        if not candidates:
            return [base_font_size]
        # Начинаем с самого крупного, чтобы максимально использовать пространство.
        return candidates

    def _text_capacity_height(self) -> int:
        start_y = self.work_y + self.text_padding
        return max(1, self.text_bottom_limit - start_y)

    def _fit_score(self, used_height: int) -> float:
        return float(used_height) / float(self._text_capacity_height())

    def _choose_best_font_size(self, draw, text_blocks: List[Dict], base_font_size: int) -> int:
        best_fit = base_font_size
        best_score = -1.0
        # Меньшая целевая плотность = лучше читаемость.
        target_fill = 0.78

        for candidate in self._font_try_order(base_font_size):
            if not self._place_text_blocks(draw, text_blocks, candidate, dry_run=True):
                continue
            used_height = self.last_text_end_y - (self.work_y + self.text_padding)
            score = self._fit_score(used_height)
            if score > best_score:
                best_score = score
                best_fit = candidate
            if score >= target_fill:
                return candidate

        return best_fit

    def _text_flow_region(self) -> Dict[str, int]:
        """Возвращает область основного потока (без графики)."""
        x = self.work_x + self.text_padding
        w = self.work_width - (2 * self.text_padding)
        min_width = 20 if self.small_text_layout else self.mm(8)
        return {'x': x, 'w': max(min_width, w)}
    
    def render(self, data: Dict) -> Image.Image:
        """Рисует готовую этикетку."""
        image = Image.new('RGB', (self.width_px, self.height_px), 'white')
        draw = ImageDraw.Draw(image)
        self.qr_position = self._normalize_qr_position(data.get('qr_position'))
        self.symbols_position = self._normalize_symbols_position(data.get('symbols_position'))
        self.symbol_positions = self._normalize_symbol_positions(data.get('symbol_positions'))

        draw.rectangle([(0, 0), (self.width_px - 1, self.height_px - 1)], outline='#000000', width=1)

        full_text_mode = bool(data.get('full_text_mode'))
        block_styles = data.get('block_styles') if isinstance(data.get('block_styles'), dict) else {}
        text_blocks = []
        if full_text_mode:
            if data.get('description'):
                block = {'type': 'description', 'text': data['description'], 'priority': 1, 'bold': False, 'multiplier': 1.0}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
        else:
            if data.get('title'):
                block = {
                    'type': 'title',
                    'text': data['title'].upper(),
                    'priority': 1,
                    'bold': bool(data.get('title_bold', True)),
                    'multiplier': float(data.get('title_scale', 1.35)),
                    'align': self._normalize_text_align(data.get('title_align'))
                }
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('description'):
                block = {'type': 'description', 'text': data['description'], 'priority': 2, 'bold': False, 'multiplier': 1.0}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('ingredients'):
                block = {'type': 'ingredients', 'text': f"Состав: {data['ingredients']}", 'priority': 2, 'bold': False, 'multiplier': 1.0}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('short_code'):
                block = {'type': 'short_code', 'text': data['short_code'], 'priority': 3, 'bold': False, 'multiplier': 0.85}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('batch_number'):
                block = {'type': 'batch', 'text': f"№ партии: {data['batch_number']}", 'priority': 4, 'bold': False, 'multiplier': 0.9}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('expiry_date'):
                block = {'type': 'expiry', 'text': f"Годен до: {data['expiry_date']}", 'priority': 4, 'bold': False, 'multiplier': 0.9}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)

        if self.small_text_layout:
            self.update_content_ratios()
            text_blocks = self._limit_text_blocks_for_small_sizes(text_blocks)

        # Графика
        self.graphic_elements = []
        qr_size_mm = None
        if data.get('barcode_data'):
            qr_size_mm = self._qr_size_mm()
            self.add_graphic_element('qr', size_mm=qr_size_mm, required=True, anchor=self.qr_position)
        symbol_size_mm = self._symbol_size_mm_limit(qr_size_mm)
        for symbol in self._collect_graphic_symbols(data):
            if self._resolve_symbol_asset_path(symbol) is None:
                continue
            symbol_anchor = self.symbol_positions.get(symbol, self.symbols_position)
            self.add_graphic_element(symbol, size_mm=symbol_size_mm, required=False, anchor=symbol_anchor)

        self.update_content_ratios()
        print(f"   Соотношение: текст {self.text_area_ratio*100:.0f}% / графика {self.graphics_area_ratio*100:.0f}%")

        base_font_size = self.optimize_text_font_size(text_blocks)

        # Сначала размещаем графику, затем гарантированно подбираем кегль текста.
        self._place_graphic_elements()
        final_font_size = self._fit_font_size_guaranteed(draw, text_blocks, base_font_size)
        self._place_text_blocks(draw, text_blocks, final_font_size, dry_run=False)
        self._draw_graphic_elements(draw, image)

        return self._finalize_image(image)

    def render_svg(self, data: Dict) -> str:
        """Рисует готовую этикетку в SVG (векторный текст)."""
        image = Image.new('RGB', (self.width_px, self.height_px), 'white')
        draw = ImageDraw.Draw(image)
        self.qr_position = self._normalize_qr_position(data.get('qr_position'))
        self.symbols_position = self._normalize_symbols_position(data.get('symbols_position'))
        self.symbol_positions = self._normalize_symbol_positions(data.get('symbol_positions'))

        full_text_mode = bool(data.get('full_text_mode'))
        block_styles = data.get('block_styles') if isinstance(data.get('block_styles'), dict) else {}
        text_blocks = []
        if full_text_mode:
            if data.get('description'):
                block = {'type': 'description', 'text': data['description'], 'priority': 1, 'bold': False, 'multiplier': 1.0}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
        else:
            if data.get('title'):
                block = {
                    'type': 'title',
                    'text': data['title'].upper(),
                    'priority': 1,
                    'bold': bool(data.get('title_bold', True)),
                    'multiplier': float(data.get('title_scale', 1.35)),
                    'align': self._normalize_text_align(data.get('title_align'))
                }
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('description'):
                block = {'type': 'description', 'text': data['description'], 'priority': 2, 'bold': False, 'multiplier': 1.0}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('ingredients'):
                block = {'type': 'ingredients', 'text': f"Состав: {data['ingredients']}", 'priority': 2, 'bold': False, 'multiplier': 1.0}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('short_code'):
                block = {'type': 'short_code', 'text': data['short_code'], 'priority': 3, 'bold': False, 'multiplier': 0.85}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('batch_number'):
                block = {'type': 'batch', 'text': f"№ партии: {data['batch_number']}", 'priority': 4, 'bold': False, 'multiplier': 0.9}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)
            if data.get('expiry_date'):
                block = {'type': 'expiry', 'text': f"Годен до: {data['expiry_date']}", 'priority': 4, 'bold': False, 'multiplier': 0.9}
                self._apply_block_style(block, block_styles)
                text_blocks.append(block)

        if self.small_text_layout:
            self.update_content_ratios()
            text_blocks = self._limit_text_blocks_for_small_sizes(text_blocks)

        self.graphic_elements = []
        qr_size_mm = None
        if data.get('barcode_data'):
            qr_size_mm = self._qr_size_mm()
            self.add_graphic_element('qr', size_mm=qr_size_mm, required=True, anchor=self.qr_position)
        symbol_size_mm = self._symbol_size_mm_limit(qr_size_mm)
        for symbol in self._collect_graphic_symbols(data):
            if self._resolve_symbol_asset_path(symbol) is None:
                continue
            symbol_anchor = self.symbol_positions.get(symbol, self.symbols_position)
            self.add_graphic_element(symbol, size_mm=symbol_size_mm, required=False, anchor=symbol_anchor)

        self.update_content_ratios()
        base_font_size = self.optimize_text_font_size(text_blocks)
        self._place_graphic_elements()
        final_font_size = self._fit_font_size_guaranteed(draw, text_blocks, base_font_size)

        _, text_lines = self._collect_text_layout(draw, text_blocks, final_font_size)
        return self._build_svg_document(text_lines, include_border=True)

    def render_blocks_direct(
        self,
        text_blocks_input: List[Dict],
        graphic_config: Dict,
        output_format: str = "svg",
    ):
        """
        Рендеринг с предварительно построенными блоками текста.

        Каждый блок в text_blocks_input содержит:
          type, text, bold, align, multiplier, priority

        graphic_config содержит:
          qr_position, symbols_position, barcode_data,
          graphic_symbols, symbol_positions
        """
        image = Image.new('RGB', (self.width_px, self.height_px), 'white')
        draw = ImageDraw.Draw(image)

        self.qr_position = self._normalize_qr_position(
            graphic_config.get('qr_position', 'auto'))
        self.symbols_position = self._normalize_symbols_position(
            graphic_config.get('symbols_position', 'auto'))
        self.symbol_positions = self._normalize_symbol_positions(
            graphic_config.get('symbol_positions') or {})

        text_blocks = [dict(b) for b in text_blocks_input]
        if self.small_text_layout:
            self.update_content_ratios()
            text_blocks = self._limit_text_blocks_for_small_sizes(text_blocks)

        self.graphic_elements = []
        qr_size_mm = None
        if graphic_config.get('barcode_data'):
            qr_size_mm = self._qr_size_mm()
            self.add_graphic_element('qr', size_mm=qr_size_mm, required=True,
                                     anchor=self.qr_position)
        symbol_size_mm = self._symbol_size_mm_limit(qr_size_mm)
        for symbol in self._collect_graphic_symbols(graphic_config):
            if self._resolve_symbol_asset_path(symbol) is None:
                continue
            sym_anchor = self.symbol_positions.get(symbol, self.symbols_position)
            self.add_graphic_element(symbol, size_mm=symbol_size_mm,
                                     required=False, anchor=sym_anchor)

        self.update_content_ratios()
        base_font_size = self.optimize_text_font_size(text_blocks)
        self._place_graphic_elements()
        final_font_size = self._fit_font_size_guaranteed(draw, text_blocks, base_font_size)

        # Enforce minimum body size so size-class differences remain visible
        _min_body = max(self._scale_font_px(12), int(self.work_height * 0.015))
        final_font_size = max(final_font_size, _min_body)

        if output_format == 'svg':
            _, text_lines = self._collect_text_layout(draw, text_blocks, final_font_size)
            return self._build_svg_document(text_lines, include_border=True)
        else:
            draw.rectangle([(0, 0), (self.width_px - 1, self.height_px - 1)],
                           outline='#000000', width=1)
            self._place_text_blocks(draw, text_blocks, final_font_size, dry_run=False)
            self._draw_graphic_elements(draw, image)
            return self._finalize_image(image)

    def _fit_font_size_guaranteed(self, draw, text_blocks: List[Dict], base_font_size: int) -> int:
        """Гарантированно подбирает кегль, чтобы текст полностью влезал."""
        candidates = []
        candidates.extend(self._font_try_order(base_font_size))
        # Добавляем аварийную шкалу уменьшения до очень мелкого кегля.
        candidates.extend([self._scale_font_px(v) for v in [6, 5, 4, 3, 2]])
        candidates = sorted(set(candidates), reverse=True)

        for size in candidates:
            if self._place_text_blocks(draw, text_blocks, size, dry_run=True):
                return size
        return candidates[-1] if candidates else max(1, base_font_size)

    def _collect_text_layout(self, draw, text_blocks: List[Dict], base_font_size: int) -> Tuple[bool, List[Dict]]:
        """Возвращает рассчитанные строки текста для SVG-рендера."""
        return self._layout_text_blocks_internal(draw, text_blocks, base_font_size, dry_run=True, collect_lines=True)

    def _build_svg_document(self, text_lines: List[Dict], include_border: bool = True) -> str:
        parts: List[str] = []
        parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width_px}" height="{self.height_px}" viewBox="0 0 {self.width_px} {self.height_px}">')
        parts.append(f'<rect x="0" y="0" width="{self.width_px}" height="{self.height_px}" fill="white"/>')
        if include_border:
            parts.append(f'<rect x="0.5" y="0.5" width="{self.width_px - 1}" height="{self.height_px - 1}" fill="none" stroke="#000000" stroke-width="1"/>')

        for line in text_lines:
            baseline_y = line['y'] + int(round(line['font_size'] * 0.82))
            font_weight = '700' if line['bold'] else '400'
            parts.append(
                f'<text x="{line["x"]}" y="{baseline_y}" '
                f'font-family="{self.font_family}, Liberation Sans, DejaVu Sans, sans-serif" '
                f'font-size="{line["font_size"]}" font-weight="{font_weight}" fill="#000000">'
                f'{escape(str(line["text"]))}</text>'
            )

        parts.extend(self._graphic_elements_svg())
        parts.append('</svg>')
        return ''.join(parts)

    def _graphic_elements_svg(self) -> List[str]:
        parts: List[str] = []
        for element in self.graphic_elements:
            if not element.placed:
                continue
            x, y, size = element.x, element.y, element.size_px
            if element.type == 'qr':
                parts.append(f'<rect x="{x}" y="{y}" width="{size}" height="{size}" fill="white" stroke="black" stroke-width="1"/>')
                cell_size = max(2, size // 10)
                for i in range(8):
                    for j in range(8):
                        if (i + j) % 2 == 0:
                            cx = x + i * cell_size + cell_size // 2
                            cy = y + j * cell_size + cell_size // 2
                            parts.append(
                                f'<rect x="{cx - cell_size//2}" y="{cy - cell_size//2}" '
                                f'width="{cell_size}" height="{cell_size}" fill="black"/>'
                            )
                parts.append(
                    f'<text x="{x + 2}" y="{y + size - max(3, size // 10)}" '
                    f'font-family="Arial, Liberation Sans, DejaVu Sans, sans-serif" '
                    f'font-size="{max(6, size // 6)}" fill="black">QR</text>'
                )
            elif self._resolve_symbol_asset_path(element.type):
                symbol_data = self._symbol_asset_data_uri(element.type)
                if symbol_data:
                    parts.append(
                        f'<image x="{x}" y="{y}" width="{size}" height="{size}" preserveAspectRatio="xMidYMid meet" '
                        f'href="{symbol_data}" />'
                    )
                else:
                    label = element.type.upper()
                    parts.append(f'<rect x="{x}" y="{y}" width="{size}" height="{size}" fill="none" stroke="#444" stroke-width="1"/>')
                    parts.append(
                        f'<text x="{x + 2}" y="{y + max(10, size // 2)}" '
                        f'font-family="Arial, Liberation Sans, DejaVu Sans, sans-serif" '
                        f'font-size="{max(8, size // 3)}" fill="#222">{escape(label)}</text>'
                    )
        return parts

    def _finalize_image(self, image: Image.Image) -> Image.Image:
        if self.supersample <= 1.0 or not self.keep_target_size:
            return image
        resampling = getattr(Image, 'Resampling', Image)
        downscaled = image.resize((self.target_width_px, self.target_height_px), resample=resampling.LANCZOS)
        return downscaled.filter(ImageFilter.UnsharpMask(radius=1.1, percent=120, threshold=2))

    def _place_graphic_elements(self):
        """Размещает графику: каждый символ — по указанной позиции, при одной позиции — по порядку добавления."""
        if not self.graphic_elements:
            self.text_bottom_limit = self.work_y + self.work_height - self.zone_gap
            return

        for element in self.graphic_elements:
            element.placed = False

        # Порядок не сортируем — сохраняем порядок добавления (graphic_symbols)
        qr_elements = [e for e in self.graphic_elements if e.type == 'qr']
        symbol_elements = [e for e in self.graphic_elements if e.type != 'qr']

        if qr_elements and self.qr_position != 'auto':
            self._place_element_by_anchor(qr_elements[0], self.qr_position)
            print(f"   Размещён qr ({self.qr_position})")

        anchored_symbols = [e for e in symbol_elements if e.anchor and e.anchor != 'auto']
        auto_symbols = [e for e in symbol_elements if not e.anchor or e.anchor == 'auto']

        if anchored_symbols:
            self._place_anchored_symbols_without_overlap(anchored_symbols, qr_elements[0] if qr_elements else None)
        if auto_symbols and self.symbols_position != 'auto':
            self._place_symbol_group_by_anchor(auto_symbols, self.symbols_position)
            print(f"   Размещены символы ({self.symbols_position})")

        # Если QR и символы выбраны в одном углу, разводим их без наложения.
        if (
            qr_elements
            and auto_symbols
            and self.qr_position != 'auto'
            and self.symbols_position != 'auto'
            and self.qr_position == self.symbols_position
        ):
            self._resolve_qr_symbol_overlap(qr_elements[0], auto_symbols, self.qr_position)

        # Если QR закреплен и все равно пересекается с символами, двигаем QR.
        if qr_elements and qr_elements[0].placed and self.qr_position != 'auto':
            occupied_rects: List[Tuple[int, int, int, int]] = []
            for element in symbol_elements:
                if element.placed:
                    occupied_rects.append(
                        (element.x, element.y, element.x + element.size_px, element.y + element.size_px)
                    )
            if occupied_rects:
                self._nudge_element_from_occupied(qr_elements[0], self.qr_position, occupied_rects)

        remaining = [e for e in self.graphic_elements if not e.placed]
        if remaining:
            self._place_remaining_elements_bottom_strip(remaining)
        else:
            self.text_bottom_limit = self.work_y + self.work_height - self.zone_gap
            self._recalculate_text_bottom_limit_from_graphics()

    def _normalize_qr_position(self, raw_position: Optional[str]) -> str:
        position = str(raw_position or 'auto').strip().lower()
        if position not in self.QR_POSITION_OPTIONS:
            return 'auto'
        return position

    def _normalize_symbols_position(self, raw_position: Optional[str]) -> str:
        position = str(raw_position or 'auto').strip().lower()
        if position not in self.SYMBOLS_POSITION_OPTIONS:
            return 'auto'
        return position

    def _normalize_text_align(self, raw_align: Optional[str]) -> str:
        align = str(raw_align or 'left').strip().lower()
        if align not in {'left', 'center', 'right'}:
            return 'left'
        return align

    def _normalize_symbol_positions(self, raw_positions) -> Dict[str, str]:
        if not isinstance(raw_positions, dict):
            return {}
        normalized: Dict[str, str] = {}
        for key, value in raw_positions.items():
            symbol = str(key).strip().lower()
            anchor = str(value).strip().lower()
            if symbol and anchor in self.SYMBOLS_POSITION_OPTIONS:
                normalized[symbol] = anchor
        return normalized

    def _collect_graphic_symbols(self, data: Dict) -> List[str]:
        symbols: List[str] = []
        # Если graphic_symbols передан, считаем его единственным источником (все id из папки symbols допустимы).
        if 'graphic_symbols' in data:
            raw_symbols = data.get('graphic_symbols')
            if isinstance(raw_symbols, str):
                raw_symbols = [x.strip() for x in raw_symbols.split(',')]
            if isinstance(raw_symbols, list):
                for symbol in raw_symbols:
                    normalized = str(symbol).strip().lower()
                    if normalized:
                        symbols.append(normalized)
            # unique preserving order
            unique: List[str] = []
            seen = set()
            for symbol in symbols:
                if symbol in seen:
                    continue
                seen.add(symbol)
                unique.append(symbol)
            return unique

        # Фолбэк для старых вызовов API без graphic_symbols.
        icons_text = str(data.get('icons', ''))
        if '♻' in icons_text:
            symbols.append('recycle')
        if 'ГОСТ' in icons_text.upper():
            symbols.append('gost')
        if 'EAC' in icons_text.upper():
            symbols.append('eac')

        unique = []
        seen = set()
        for symbol in symbols:
            if symbol in seen:
                continue
            seen.add(symbol)
            unique.append(symbol)
        return unique

    def _anchor_origin(self, anchor: str, object_w: int, object_h: int) -> Tuple[int, int]:
        safe_left = self.work_x + self.graphics_safe_padding
        safe_top = self.work_y + self.graphics_safe_padding
        safe_right = self.work_x + self.work_width - self.graphics_safe_padding
        safe_bottom = self.work_y + self.work_height - self.graphics_safe_padding

        if anchor == 'top_left':
            x = safe_left
            y = safe_top
        elif anchor == 'top_right':
            x = safe_right - object_w
            y = safe_top
        elif anchor == 'bottom_left':
            x = safe_left
            y = safe_bottom - object_h
        elif anchor == 'bottom_center':
            x = safe_left + (safe_right - safe_left - object_w) // 2
            y = safe_bottom - object_h
        else:  # bottom_right
            x = safe_right - object_w
            y = safe_bottom - object_h

        x = max(safe_left, min(x, safe_right - object_w))
        y = max(safe_top, min(y, safe_bottom - object_h))
        return x, y

    def _place_element_by_anchor(self, element: GraphicElement, anchor: str):
        x, y = self._anchor_origin(anchor, element.size_px, element.size_px)
        element.x = x
        element.y = y
        element.placed = True

    def _place_symbol_group_by_anchor(self, elements: List[GraphicElement], anchor: str):
        if not elements:
            return
        # Для правых якорей первый добавленный — у угла (справа), поэтому разворачиваем порядок
        if anchor in ('top_right', 'bottom_right'):
            elements = list(reversed(elements))
        gap = self.mm(0.9)
        group_w = sum(e.size_px for e in elements) + gap * (len(elements) - 1)
        group_h = max(e.size_px for e in elements)
        start_x, start_y = self._anchor_origin(anchor, group_w, group_h)
        self._place_symbol_group_at(elements, start_x, start_y)

    def _place_anchored_symbols_without_overlap(self, elements: List[GraphicElement], qr_element: Optional[GraphicElement]):
        by_anchor: Dict[str, List[GraphicElement]] = {}
        for element in elements:
            by_anchor.setdefault(element.anchor or 'auto', []).append(element)

        occupied_rects: List[Tuple[int, int, int, int]] = []
        if qr_element is not None and qr_element.placed:
            occupied_rects.append((qr_element.x, qr_element.y, qr_element.x + qr_element.size_px, qr_element.y + qr_element.size_px))

        anchor_order = ['top_left', 'top_right', 'bottom_left', 'bottom_center', 'bottom_right']
        for anchor in anchor_order:
            group = by_anchor.get(anchor, [])
            if not group:
                continue
            self._place_symbol_group_by_anchor(group, anchor)
            self._nudge_group_from_occupied(group, anchor, occupied_rects)
            bounds = self._elements_bounds(group)
            if bounds:
                occupied_rects.append(bounds)
            print(f"   Размещены символы ({anchor})")

    def _nudge_group_from_occupied(self, elements: List[GraphicElement], anchor: str, occupied_rects: List[Tuple[int, int, int, int]]):
        bounds = self._elements_bounds(elements)
        if not bounds:
            return
        if not any(self._rects_overlap(bounds, rect) for rect in occupied_rects):
            return

        group_w = bounds[2] - bounds[0]
        group_h = bounds[3] - bounds[1]
        base_x, base_y = self._anchor_origin(anchor, group_w, group_h)
        step = self.mm(1.0)
        safe_left = self.work_x + self.graphics_safe_padding
        safe_top = self.work_y + self.graphics_safe_padding
        safe_right = self.work_x + self.work_width - self.graphics_safe_padding
        safe_bottom = self.work_y + self.work_height - self.graphics_safe_padding

        def clamp(x, y):
            cx = max(safe_left, min(x, safe_right - group_w))
            cy = max(safe_top, min(y, safe_bottom - group_h))
            return cx, cy

        for i in range(1, 32):
            d = i * step
            if anchor == 'top_left':
                offsets = [(0, d), (d, 0), (d, d)]
            elif anchor == 'top_right':
                offsets = [(0, d), (-d, 0), (-d, d)]
            elif anchor == 'bottom_left':
                offsets = [(0, -d), (d, 0), (d, -d)]
            elif anchor == 'bottom_center':
                offsets = [(0, -d), (d, -d), (-d, -d), (d, 0), (-d, 0)]
            else:  # bottom_right
                offsets = [(0, -d), (-d, 0), (-d, -d)]

            for ox, oy in offsets:
                cx, cy = clamp(base_x + ox, base_y + oy)
                self._place_symbol_group_at(elements, cx, cy)
                test_bounds = self._elements_bounds(elements)
                if test_bounds and not any(self._rects_overlap(test_bounds, rect) for rect in occupied_rects):
                    return

    def _nudge_element_from_occupied(self, element: GraphicElement, anchor: str, occupied_rects: List[Tuple[int, int, int, int]]):
        bounds = (element.x, element.y, element.x + element.size_px, element.y + element.size_px)
        if not any(self._rects_overlap(bounds, rect) for rect in occupied_rects):
            return

        group_w = element.size_px
        group_h = element.size_px
        base_x, base_y = self._anchor_origin(anchor, group_w, group_h)
        step = self.mm(1.0)
        safe_left = self.work_x + self.graphics_safe_padding
        safe_top = self.work_y + self.graphics_safe_padding
        safe_right = self.work_x + self.work_width - self.graphics_safe_padding
        safe_bottom = self.work_y + self.work_height - self.graphics_safe_padding

        def clamp(x, y):
            cx = max(safe_left, min(x, safe_right - group_w))
            cy = max(safe_top, min(y, safe_bottom - group_h))
            return cx, cy

        for i in range(1, 32):
            d = i * step
            if anchor == 'top_left':
                offsets = [(0, d), (d, 0), (d, d)]
            elif anchor == 'top_right':
                offsets = [(0, d), (-d, 0), (-d, d)]
            elif anchor == 'bottom_left':
                offsets = [(0, -d), (d, 0), (d, -d)]
            elif anchor == 'bottom_center':
                offsets = [(0, -d), (d, -d), (-d, -d), (d, 0), (-d, 0)]
            else:  # bottom_right
                offsets = [(0, -d), (-d, 0), (-d, -d)]

            for ox, oy in offsets:
                cx, cy = clamp(base_x + ox, base_y + oy)
                candidate = (cx, cy, cx + group_w, cy + group_h)
                if not any(self._rects_overlap(candidate, rect) for rect in occupied_rects):
                    element.x = cx
                    element.y = cy
                    element.placed = True
                    return

    def _place_symbol_group_at(self, elements: List[GraphicElement], start_x: int, start_y: int):
        if not elements:
            return
        gap = self.mm(0.9)
        group_h = max(e.size_px for e in elements)
        safe_left = self.work_x + self.graphics_safe_padding
        safe_top = self.work_y + self.graphics_safe_padding
        safe_right = self.work_x + self.work_width - self.graphics_safe_padding
        safe_bottom = self.work_y + self.work_height - self.graphics_safe_padding

        cursor_x = max(safe_left, start_x)
        start_y = max(safe_top, min(start_y, safe_bottom - group_h))
        for element in elements:
            element.x = max(safe_left, min(cursor_x, safe_right - element.size_px))
            element.y = start_y + (group_h - element.size_px) // 2
            element.placed = True
            cursor_x += element.size_px + gap

    def _elements_bounds(self, elements: List[GraphicElement]) -> Optional[Tuple[int, int, int, int]]:
        placed = [e for e in elements if e.placed]
        if not placed:
            return None
        x1 = min(e.x for e in placed)
        y1 = min(e.y for e in placed)
        x2 = max(e.x + e.size_px for e in placed)
        y2 = max(e.y + e.size_px for e in placed)
        return (x1, y1, x2, y2)

    def _rects_overlap(self, a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    def _resolve_qr_symbol_overlap(self, qr_element: GraphicElement, symbol_elements: List[GraphicElement], anchor: str):
        group_bounds = self._elements_bounds(symbol_elements)
        if not group_bounds:
            return
        qr_rect = (qr_element.x, qr_element.y, qr_element.x + qr_element.size_px, qr_element.y + qr_element.size_px)
        if not self._rects_overlap(qr_rect, group_bounds):
            return

        gap = self.mm(0.9)
        group_w = group_bounds[2] - group_bounds[0]
        group_h = group_bounds[3] - group_bounds[1]
        base_x, _ = self._anchor_origin(anchor, group_w, group_h)
        safe_top = self.work_y + self.graphics_safe_padding
        safe_bottom = self.work_y + self.work_height - self.graphics_safe_padding

        # Сначала двигаем вдоль "угла" (вниз для верхних углов, вверх для нижних).
        if anchor in {'top_left', 'top_right'}:
            candidate_y = qr_element.y + qr_element.size_px + gap
        else:
            candidate_y = qr_element.y - group_h - gap
        candidate_y = max(safe_top, min(candidate_y, safe_bottom - group_h))
        self._place_symbol_group_at(symbol_elements, base_x, candidate_y)

        group_bounds = self._elements_bounds(symbol_elements)
        if group_bounds and not self._rects_overlap(qr_rect, group_bounds):
            return

        # Фолбэк: сдвиг по горизонтали от QR, сохраняя привязку к углу.
        if anchor in {'top_left', 'bottom_left'}:
            candidate_x = qr_element.x + qr_element.size_px + gap
        else:
            candidate_x = qr_element.x - group_w - gap
        self._place_symbol_group_at(symbol_elements, candidate_x, candidate_y)

    def _place_remaining_elements_bottom_strip(self, elements: List[GraphicElement]):
        max_graphic_size = max(e.size_px for e in elements)
        min_strip = max_graphic_size + (2 * self.graphics_safe_padding)
        ratio_strip = int(self.work_height * self.graphics_area_ratio)
        strip_height = max(min_strip, ratio_strip)
        max_strip_ratio = 0.1 if self.small_text_layout else 0.16
        strip_height = min(strip_height, int(self.work_height * max_strip_ratio))

        strip_top = self.work_y + self.work_height - strip_height
        min_text_bottom = self.work_y + (10 if self.small_text_layout else self.mm(8))
        self.text_bottom_limit = max(min_text_bottom, strip_top - self.zone_gap)

        left_edge = self.work_x + self.graphics_safe_padding
        right_edge = self.work_x + self.work_width - self.graphics_safe_padding
        gap = self.mm(0.6) if self.small_text_layout else self.mm(1.5)

        cursor_right = right_edge
        cursor_y = strip_top + self.graphics_safe_padding

        # Уже размещенные элементы не должны перекрываться с полосой.
        occupied_rects: List[Tuple[int, int, int, int]] = []
        for placed in self.graphic_elements:
            if placed.placed and placed not in elements:
                occupied_rects.append((placed.x, placed.y, placed.x + placed.size_px, placed.y + placed.size_px))

        for element in elements:
            placed_ok = False
            attempts = 0
            while not placed_ok and attempts < 128:
                x = cursor_right - element.size_px
                if x < left_edge:
                    cursor_right = right_edge
                    cursor_y += element.size_px + gap
                    x = cursor_right - element.size_px

                y = min(cursor_y, self.work_y + self.work_height - self.graphics_safe_padding - element.size_px)
                x = max(left_edge, x)
                y = max(strip_top + self.graphics_safe_padding, y)
                candidate = (x, y, x + element.size_px, y + element.size_px)

                if any(self._rects_overlap(candidate, rect) for rect in occupied_rects):
                    cursor_right = x - gap
                    attempts += 1
                    continue

                element.x = x
                element.y = y
                element.placed = True
                occupied_rects.append(candidate)
                cursor_right = element.x - gap
                placed_ok = True
                print(f"   Размещён {element.type}")

            if not placed_ok:
                element.x = max(left_edge, min(cursor_right - element.size_px, right_edge - element.size_px))
                element.y = max(strip_top + self.graphics_safe_padding, min(cursor_y, self.work_y + self.work_height - self.graphics_safe_padding - element.size_px))
                element.placed = True
                occupied_rects.append((element.x, element.y, element.x + element.size_px, element.y + element.size_px))
                cursor_right = element.x - gap
                print(f"   Размещён {element.type} (fallback)")

        self._recalculate_text_bottom_limit_from_graphics()

    def _symbol_size_mm_limit(self, qr_size_mm: Optional[float]) -> float:
        """
        Ограничивает размер символов: их площадь не больше площади QR.
        Для квадратных символов это эквивалентно ограничению стороны.
        """
        default_mm = 4.8
        if qr_size_mm is None:
            return default_mm
        return max(3.6, min(default_mm, float(qr_size_mm)))

    def _recalculate_text_bottom_limit_from_graphics(self):
        default_bottom = self.work_y + self.work_height - self.zone_gap
        limit = default_bottom
        cutoff_ratio = 0.75 if self.small_text_layout else 0.58
        cutoff = self.work_y + int(self.work_height * cutoff_ratio)
        for element in self.graphic_elements:
            if not element.placed:
                continue
            if element.y >= cutoff:
                limit = min(limit, element.y - self.zone_gap)
        min_text_bottom = self.work_y + (10 if self.small_text_layout else self.mm(8))
        self.text_bottom_limit = max(min_text_bottom, limit)

    def _place_text_blocks(self, draw, text_blocks: List[Dict], base_font_size: int, dry_run: bool = False) -> bool:
        """Размещает блоки текста по области, обходя графику."""
        success, _ = self._layout_text_blocks_internal(draw, text_blocks, base_font_size, dry_run=dry_run, collect_lines=False)
        return success

    def _layout_text_blocks_internal(
        self,
        draw,
        text_blocks: List[Dict],
        base_font_size: int,
        dry_run: bool = False,
        collect_lines: bool = False
    ) -> Tuple[bool, List[Dict]]:
        """Единый движок текста: построчное адаптивное обтекание графики."""
        region = self._text_flow_region()
        current_y = self.work_y + self.text_padding
        bottom_y = self.text_bottom_limit
        min_line_width = 20 if self.small_text_layout else self.mm(2.8)
        collected_lines: List[Dict] = []

        occupied_zones = self._occupied_zones()

        for block in text_blocks:
            probe_x, probe_width = self._get_text_line_region(current_y, region['x'], region['w'], occupied_zones)
            probe_width = max(min_line_width, probe_width)

            font_size = self._resolve_block_font_size(block, base_font_size, probe_width, draw)
            font = self.get_font(font_size, block.get('bold', False))
            line_height = self._line_height_for_block(font_size, block.get('type', 'description'))
            success, current_y, block_lines = self._layout_block_lines_dynamic(
                text=str(block.get('text', '')),
                font=font,
                draw=draw,
                current_y=current_y,
                bottom_y=bottom_y,
                line_height=line_height,
                base_x=region['x'],
                base_w=region['w'],
                occupied_zones=occupied_zones,
                min_line_width=min_line_width,
                align=str(block.get('align', 'left'))
            )
            if not success:
                self.last_text_end_y = current_y
                return False, collected_lines

            if not dry_run:
                for item in block_lines:
                    draw.text((item['x'], item['y']), item['text'], fill='black', font=font)

            if not dry_run:
                print(f"   Блок {block['type']}: {font_size}px")
            if collect_lines:
                for item in block_lines:
                    collected_lines.append({
                        'x': item['x'],
                        'y': item['y'],
                        'text': item['text'],
                        'font_size': font_size,
                        'bold': bool(block.get('bold', False)),
                        'block_type': block.get('type', 'description')
                    })
            compact_gap = max(1, int(round(line_height * 0.16)))
            current_y += min(self.block_gap, compact_gap)

        self.last_text_end_y = current_y
        return True, collected_lines

    def _occupied_zones(self) -> List[Dict]:
        zones = []
        for element in self.graphic_elements:
            if element.placed:
                zones.append({
                    'x': element.x - self.graphics_safe_padding,
                    'y': element.y - self.graphics_safe_padding,
                    'w': element.size_px + (2 * self.graphics_safe_padding),
                    'h': element.size_px + (2 * self.graphics_safe_padding)
                })
        return zones

    def _layout_block_lines_dynamic(
        self,
        text: str,
        font,
        draw,
        current_y: int,
        bottom_y: int,
        line_height: int,
        base_x: int,
        base_w: int,
        occupied_zones: List[Dict],
        min_line_width: int,
        align: str = 'left'
    ) -> Tuple[bool, int, List[Dict]]:
        lines_out: List[Dict] = []
        paragraphs = [p.strip() for p in str(text).split('\n') if p.strip()]

        for paragraph in paragraphs:
            words = paragraph.split()
            if not words:
                continue

            while words:
                if current_y + line_height > bottom_y:
                    return False, current_y, lines_out

                line_x, line_w = self._get_text_line_region(current_y, base_x, base_w, occupied_zones)
                # Если на этой высоте полезная ширина слишком мала - опускаемся ниже.
                if line_w < min_line_width:
                    current_y += line_height
                    continue

                used = 0
                while used < len(words):
                    candidate = ' '.join(words[:used + 1])
                    if self._get_text_width(candidate, font, draw) <= line_w:
                        used += 1
                        continue
                    break

                if used == 0:
                    # Очень длинное слово: режем по символам под текущую ширину.
                    word = words[0]
                    chunk = []
                    idx = 0
                    while idx < len(word):
                        test = ''.join(chunk + [word[idx]])
                        if self._get_text_width(test, font, draw) <= max(1, line_w - self.mm(0.25)):
                            chunk.append(word[idx])
                            idx += 1
                        else:
                            break
                    if not chunk:
                        return False, current_y, lines_out
                    head = ''.join(chunk)
                    tail = word[len(head):]
                    line_text = head + ('-' if tail else '')
                    words = ([tail] if tail else []) + words[1:]
                else:
                    line_text = ' '.join(words[:used])
                    words = words[used:]

                align = str(align or 'left').lower()
                aligned_x = line_x
                if align in {'center', 'right'}:
                    text_w = self._get_text_width(line_text, font, draw)
                    if align == 'center':
                        aligned_x = line_x + max(0, int(round((line_w - text_w) / 2)))
                    else:
                        aligned_x = line_x + max(0, int(round(line_w - text_w)))
                lines_out.append({'x': aligned_x, 'y': current_y, 'text': line_text})
                current_y += line_height

        return True, current_y, lines_out

    def _line_height_for_block(self, font_size: int, block_type: str) -> int:
        """Возвращает интерлиньяж для соответствующего блока."""
        is_title = block_type == 'title'
        font = self.get_font(font_size, is_title)
        try:
            bbox = font.getbbox("Ag")
            glyph_h = max(1, bbox[3] - bbox[1])
        except Exception:
            glyph_h = max(1, int(round(ArialMetrics.get_line_height(font_size))))

        leading = 1.04 if is_title else 1.06
        return max(1, int(math.ceil(glyph_h * leading)))

    def _resolve_block_font_size(self, block: Dict, body_font_size: int, max_width: int, draw) -> int:
        """Динамический подбор шрифта: пытаемся крупнее body по возможности."""
        body_scale = self._body_size_candidates()
        scale_mult = max(0.7, min(2.0, float(block.get('multiplier', 1.0))))

        if block.get('type') == 'title':
            # Пытаемся вписать заголовок покрупнее
            title_candidates = [self._scale_font_px(px) for px in ArialMetrics.title_px_scale_desc()]
            for size_px in title_candidates:
                font = self.get_font(size_px, True)
                lines = self._wrap_text_block(block.get('text', ''), font, max_width, draw)
                if len(lines) <= 2:
                    return max(1, int(round(size_px * scale_mult)))

            # Если не влезло при 2.5/3.0, пробуем, но уже по более пологой шкале body.
            for size_px in body_scale:
                if size_px <= body_font_size:
                    continue
                font = self.get_font(size_px, True)
                lines = self._wrap_text_block(block.get('text', ''), font, max_width, draw)
                if len(lines) <= 3:
                    return max(1, int(round(size_px * scale_mult)))

            return max(1, int(round(max(body_font_size + 1, body_scale[-1]) * scale_mult)))

        if block.get('type') in {'short_code', 'batch', 'expiry'}:
            if body_font_size in body_scale:
                idx = body_scale.index(body_font_size)
                return max(1, int(round(body_scale[min(idx + 1, len(body_scale) - 1)] * scale_mult)))
            return max(1, int(round(body_scale[min(1, len(body_scale) - 1)] * scale_mult)))

        return max(1, int(round(min(body_font_size, self._max_readable_body_font_px()) * scale_mult)))

    def _get_available_width(self, current_x: int, column_width: int, occupied_zones: List[Dict], current_y: int) -> int:
        max_width = column_width
        for zone in occupied_zones:
            zone_top = zone['y']
            zone_bottom = zone['y'] + zone['h']
            if current_y < zone_top or current_y > zone_bottom:
                continue
            if zone['x'] < current_x + max_width and zone['x'] + zone['w'] > current_x:
                max_width = min(max_width, zone['x'] - current_x - self.zone_gap)
        return max_width

    def _get_text_line_region(self, current_y: int, base_x: int, base_w: int, occupied_zones: List[Dict]) -> Tuple[int, int]:
        """Возвращает лучший горизонтальный сегмент для строки на текущем Y (эффект обтекания)."""
        line_left = base_x
        line_right = base_x + max(1, base_w)
        intervals = [(line_left, line_right)]

        for zone in occupied_zones:
            zone_top = zone['y']
            zone_bottom = zone['y'] + zone['h']
            if current_y < zone_top or current_y > zone_bottom:
                continue

            side_gap = max(1, self.zone_gap // 2)
            cut_left = max(line_left, zone['x'] - side_gap)
            cut_right = min(line_right, zone['x'] + zone['w'] + side_gap)
            if cut_left >= cut_right:
                continue

            next_intervals = []
            for a, b in intervals:
                if cut_right <= a or cut_left >= b:
                    next_intervals.append((a, b))
                    continue
                if cut_left > a:
                    next_intervals.append((a, cut_left))
                if cut_right < b:
                    next_intervals.append((cut_right, b))
            intervals = next_intervals if next_intervals else intervals

        if not intervals:
            return base_x, max(1, base_w)

        best = max(intervals, key=lambda p: p[1] - p[0])
        return best[0], max(1, best[1] - best[0])

    def _wrap_text_block(self, text: str, font, max_width: int, draw) -> List[str]:
        """Перенос текста с сохранением абзацев (переводов строк)."""
        lines: List[str] = []
        for paragraph in str(text).split('\n'):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            lines.extend(self._wrap_text(paragraph, font, max_width, draw))
        return lines

    def _apply_block_style(self, block: Dict, styles: Dict) -> None:
        if not block or not styles:
            return
        block_type = str(block.get('type') or '').strip().lower()
        style = styles.get(block_type)
        if not isinstance(style, dict):
            return
        if 'bold' in style:
            block['bold'] = bool(style.get('bold'))
        if 'align' in style:
            align = self._normalize_text_align(style.get('align'))
            block['align'] = align
        if 'multiplier' in style:
            try:
                mult = float(style.get('multiplier'))
                block['multiplier'] = max(0.6, min(2.0, mult))
            except Exception:
                pass
    
    def _draw_graphic_elements(self, draw, image=None):
        """Рисует графические элементы"""
        for element in self.graphic_elements:
            if not element.placed:
                continue
            
            x, y = element.x, element.y
            size = element.size_px
            
            if element.type == 'qr':
                # Рамка QR
                draw.rectangle([(x, y), (x + size, y + size)], fill='white', outline='black', width=1)
                
                # Узор DataMatrix
                cell_size = max(2, size // 10)
                for i in range(8):
                    for j in range(8):
                        if (i + j) % 2 == 0:
                            cx = x + i * cell_size + cell_size // 2
                            cy = y + j * cell_size + cell_size // 2
                            draw.rectangle(
                                [(cx - cell_size//2, cy - cell_size//2),
                                 (cx + cell_size//2, cy + cell_size//2)],
                                fill='black'
                            )
                
                font = self.get_font(max(6, size // 6))
                draw.text((x + 2, y + size - 16), "QR", fill='black', font=font)
            
            elif self._resolve_symbol_asset_path(element.type):
                symbol_img = self._load_symbol_image(element.type, size)
                if symbol_img is not None and image is not None:
                    image.paste(symbol_img, (x, y), symbol_img)
                else:
                    draw.rectangle([(x, y), (x + size, y + size)], outline='#444444', width=1)
                    font = self.get_font(max(8, size // 3), bold=True)
                    draw.text((x + 2, y + max(1, size // 3)), element.type.upper(), fill='#222222', font=font)

    def _resolve_symbol_asset_path(self, symbol_type: str) -> Optional[str]:
        symbol_lower = (symbol_type or "").strip().lower()
        if not symbol_lower:
            return None
        for name in SYMBOL_ASSET_CANDIDATES.get(symbol_lower, []):
            path = os.path.join(SYMBOL_ASSET_DIR, name)
            if os.path.isfile(path):
                return path
        return None

    def _load_symbol_image(self, symbol_type: str, target_size: int) -> Optional[Image.Image]:
        cache_key = f"{symbol_type}:{target_size}"
        if cache_key in self.symbol_image_cache:
            return self.symbol_image_cache[cache_key]

        asset_path = self._resolve_symbol_asset_path(symbol_type)
        if not asset_path:
            self.symbol_image_cache[cache_key] = None
            return None
        try:
            img = Image.open(asset_path).convert("RGBA")
            # Для ассетов с "черным фоном" автоматически делаем темные пиксели прозрачными.
            alpha = img.getchannel("A")
            if alpha.getextrema() == (255, 255):
                luminance = img.convert("RGB").convert("L")
                auto_alpha = luminance.point(lambda p: 0 if p < 16 else min(255, int((p - 16) * 1.4)))
                img.putalpha(auto_alpha)

            resampling = getattr(Image, 'Resampling', Image)
            resized = img.resize((target_size, target_size), resample=resampling.LANCZOS)
            self.symbol_image_cache[cache_key] = resized
            return resized
        except Exception:
            self.symbol_image_cache[cache_key] = None
            return None

    def _symbol_asset_data_uri(self, symbol_type: str) -> Optional[str]:
        asset_path = self._resolve_symbol_asset_path(symbol_type)
        if not asset_path:
            return None
        try:
            with open(asset_path, 'rb') as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode('ascii')
            return f"data:image/png;base64,{b64}"
        except Exception:
            return None
    
    def _wrap_text(self, text: str, font, max_width: int, draw) -> List[str]:
        """Перенос текста"""
        words = text.split()
        if not words:
            return []
        
        lines = []
        current_line = []
        
        for word in words:
            test_line = ' '.join(current_line + [word])
            if self._get_text_width(test_line, font, draw) <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                else:
                    # Длинное слово
                    chars = []
                    for char in word:
                        test_word = ''.join(chars + [char])
                        if self._get_text_width(test_word, font, draw) <= max_width - 5:
                            chars.append(char)
                        else:
                            if chars:
                                lines.append(''.join(chars) + '-')
                            chars = [char]
                    if chars:
                        lines.append(''.join(chars))
                    current_line = []
        
        if current_line:
            lines.append(' '.join(current_line))
        
        return lines
    
    def _get_text_width(self, text: str, font, draw) -> int:
        """Получает ширину текста"""
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            return bbox[2] - bbox[0]
        except:
            font_size = font.size
            is_bold = 'bold' in str(font).lower()
            return ArialMetrics.estimate_text_width(text, font_size, is_bold)


def extract_data_from_text(parsed_data: Dict) -> Dict:
    """Извлекает данные из распарсенного текста"""
    result = {}
    
    # Заголовок
    if parsed_data.get('product_full_name'):
        result['title'] = parsed_data['product_full_name']
    elif parsed_data.get('product_name'):
        result['title'] = parsed_data['product_name']
    
    # Основной текст
    description_parts = []
    
    if parsed_data.get('ingredients'):
        description_parts.append(f"Состав: {parsed_data['ingredients']}")
    
    if parsed_data.get('manufacturer'):
        description_parts.append(f"Производитель: {parsed_data['manufacturer']}")
    
    if parsed_data.get('importer'):
        description_parts.append(f"Импортер: {parsed_data['importer']}")
    
    if parsed_data.get('country_of_origin'):
        country_text = f"Страна: {parsed_data['country_of_origin']}"
        description_parts.append(country_text)
    
    result['description'] = "\n".join(description_parts)
    
    # Штрихкод
    if parsed_data.get('barcode'):
        result['barcode_data'] = parsed_data['barcode']
    else:
        result['barcode_data'] = "4600966003792"
    
    # Короткий код
    if parsed_data.get('batch_number'):
        result['short_code'] = parsed_data['batch_number']
    else:
        result['short_code'] = "5eBqWK"
    
    # Номер партии
    if parsed_data.get('batch_number'):
        result['batch_number'] = parsed_data['batch_number']
    
    # Срок годности
    if parsed_data.get('expiry_date'):
        result['expiry_date'] = parsed_data['expiry_date']
    
    # Иконки
    icons = []
    if parsed_data.get('requires_gost'):
        icons.append("ГОСТ")
    if parsed_data.get('is_recyclable'):
        icons.append("♻")
    
    if icons:
        result['icons'] = ' • '.join(icons)
    
    return result


# Mapping: Block.type → renderer type key used by _layout_text_blocks_internal
_BLOCK_TYPE_KEY_MAP: dict[str, str] = {
    "brand":             "title",
    "product_name_ru":   "title",
    "tagline":           "title",
    "product_name_orig": "description",
    "category":          "description",
    "description":       "description",
    "benefits_list":     "description",
    "custom":            "description",
    "usage":             "ingredients",
    "precautions":       "ingredients",
    "ingredients":       "ingredients",
    "shelf_life":        "batch",
    "batch_volume":      "batch",
    "manufacturer":      "short_code",
    "importer":          "short_code",
    "article":           "short_code",
}


def render_from_blocks(
    layout: "EtoileLabelLayout",
    blocks: list,
    variant,
    graphic_symbols: list | None = None,
    symbol_positions: dict | None = None,
    output_format: str = "svg",
):
    """
    Конвертирует list[Block] + LayoutVariant в рендер этикетки.

    Каждый блок сохраняет индивидуальный стиль (bold, align, size_multiplier).
    Если блоки используют шрифт, отличный от текущего layout.font_family,
    пересоздаёт layout с нужным семейством шрифта.

    Параметры
    ---------
    layout          : экземпляр EtoileLabelLayout
    blocks          : список Block из block_parser.py
    variant         : LayoutVariant из layout_composer.py
    graphic_symbols : список id символов (напр. ['eac', 'pet1'])
    symbol_positions: {symbol_id: position}
    output_format   : 'svg' | 'png'
    """
    # Filter by visibility and visible_types only — do NOT apply variant style overrides
    # so that user's manual edits (bold, align, size_class) are always respected.
    if variant:
        visible_types = getattr(variant, "visible_types", None)
        filtered = [
            b for b in blocks
            if b.visible
            and (visible_types is None or b.type in visible_types)
        ]
    else:
        filtered = [b for b in blocks if b.visible]

    # Determine dominant font from blocks
    dominant_font = None
    for b in filtered:
        if hasattr(b, "font") and b.font and b.font in FONT_PATHS:
            dominant_font = b.font
            break
    if dominant_font and dominant_font != layout.font_family:
        layout = EtoileLabelLayout(
            size_key=layout.size_key,
            supersample=layout.supersample,
            keep_target_size=layout.keep_target_size,
            font_family=dominant_font,
        )

    import re as _re

    def _render_text(block) -> str:
        """Apply uppercase and convert list markers (- / *) to bullet •."""
        text = block.text.upper() if block.uppercase else block.text
        lines = text.split('\n')
        out = []
        for line in lines:
            stripped = line.strip()
            if _re.match(r'^[-*]\s+', stripped):
                stripped = '\u2022 ' + stripped[2:].lstrip()
            out.append(stripped)
        return '\n'.join(out)

    # Build text_blocks directly — each block keeps its own bold/align/multiplier
    text_blocks_input: list[dict] = []
    for block in filtered:
        if not block.text:
            continue
        type_key = _BLOCK_TYPE_KEY_MAP.get(block.type, "description")
        text_blocks_input.append({
            "type":       type_key,
            "text":       _render_text(block),
            "priority":   1,
            "bold":       block.bold,
            "align":      block.align,
            "multiplier": block.size_multiplier,
        })

    graphic_config: dict = {
        "qr_position":      variant.qr_position if variant else "bottom_right",
        "symbols_position": variant.symbols_position if variant else "auto",
        "barcode_data":     "0000000000000",
        "graphic_symbols":  graphic_symbols or [],
        "symbol_positions": symbol_positions or {},
    }

    return layout.render_blocks_direct(text_blocks_input, graphic_config, output_format)


def get_available_sizes() -> List[Dict]:
    """Возвращает список доступных размеров"""
    sizes = []
    for key, info in PREDEFINED_SIZES.items():
        width_mm = info['width_mm']
        height_mm = info['height_mm']
        sizes.append({
            'id': key,
            'name': f"{width_mm}x{height_mm} мм",
            'width_mm': width_mm,
            'height_mm': height_mm,
            'width_px': info['width_px'],
            'height_px': info['height_px']
        })
    return sizes


