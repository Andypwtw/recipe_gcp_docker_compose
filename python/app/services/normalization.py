from __future__ import annotations

import re
from fractions import Fraction


# ------------------------------------------------------------
# Standard unit aliases
# ------------------------------------------------------------
UNIT_ALIASES = {
    # weight
    "公克": "g",
    "克": "g",
    "g": "g",
    "公斤": "kg",
    "kg": "kg",
    "斤": "jin",
    "兩": "liang",
    "钱": "qian",
    "錢": "qian",
    "盎司": "oz",
    "oz": "oz",
    "磅": "lb",
    "lb": "lb",

    # volume
    "毫升": "ml",
    "ml": "ml",
    "mL": "ml",
    "ML": "ml",
    "㏄": "ml",
    "cc": "ml",
    "CC": "ml",
    "c.c.": "ml",
    "C.C.": "ml",
    "c.c": "ml",
    "C.C": "ml",
    "公升": "l",
    "L": "l",
    "l": "l",
    "大匙": "tbsp",
    "湯匙": "tbsp",
    "小匙": "tsp",
    "茶匙": "tsp",

    # counting / package / serving
    "個": "個",
    "顆": "顆",
    "朵": "朵",
    "片": "片",
    "格": "格",
    "隻": "隻",
    "尾": "尾",
    "條": "條",
    "根": "根",
    "杯": "杯",
    "碗": "碗",
    "匙": "匙",
    "支": "支",
    "粒": "粒",
    "塊": "塊",
    "包": "包",
    "張": "張",
    "罐": "罐",
    "盒": "盒",
    "把": "把",
    "瓣": "瓣",
    "份": "份",
    "人份": "人份",
    "棵": "棵",
    "枝": "枝",
    "株": "株",
    "瓶": "瓶",
    "副": "副",
    "段": "段",
    "球": "球",
    "葉": "葉",
    "枚": "枚",
    "串": "串",
    "束": "束",
    "卷": "卷",
    "捲": "卷",
    "袋": "袋",
    "管": "管",
    "盤": "盤",
    "鍋": "鍋",
    "桶": "桶",
    "匙": "匙",
    "小塊": "小塊",
    "大塊": "大塊",
    "小片": "小片",
    "大片": "大片",
    "小段": "小段",
    "大段": "大段",
    "大碗": "大碗",
    "小碗": "小碗",
    "量杯": "量杯",
    "中匙": "中匙",

    # qualitative
    "少許": "少許",
    "適量": "適量",
    "酌量": "酌量",
    "少量": "少量",
    "些許": "些許",
}


MASS_TO_G = {
    "g": 1.0,
    "kg": 1000.0,
    "jin": 600.0,
    # Taiwan culinary traditional weights
    "liang": 37.5,
    "qian": 3.75,
    "oz": 28.349523125,
    "lb": 453.59237,
}

VOLUME_TO_ML = {
    "ml": 1.0,
    "l": 1000.0,
    "tbsp": 15.0,
    "tsp": 5.0,
}

COUNT_UNITS = {
    "個", "顆", "朵", "片", "格", "隻", "尾", "條", "根",
    "杯", "碗", "匙", "支", "粒", "塊", "包", "張", "罐",
    "盒", "把", "瓣", "份", "人份", "棵", "枝", "株", "瓶",
    "副", "段", "球", "葉", "枚", "串", "束", "卷", "袋",
    "管", "盤", "鍋", "桶", "小塊", "大塊", "小片", "大片",
    "小段", "大段", "大碗", "小碗", "量杯", "中匙",
}

QUALITATIVE_UNITS = {
    "少許", "適量", "酌量", "少量", "些許",
}


# ------------------------------------------------------------
# Number parsing
# Supports:
#   1
#   1.5
#   1/2
#   1又1/2
#   1 1/2
# ------------------------------------------------------------
MIXED_FRACTION_RE = re.compile(
    r"^(?P<whole>\d+)\s*(?:又|\s+)\s*(?P<num>\d+)\s*/\s*(?P<den>\d+)$"
)

FRACTION_RE = re.compile(
    r"^(?P<num>\d+)\s*/\s*(?P<den>\d+)$"
)


def clean_text(value: str) -> str:
    value = str(value or "")
    value = (
        value.replace("\u3000", " ")
        .replace("｜", "|")
        .replace("－", "-")
        .replace("—", "-")
        .replace("～", "~")
        .replace("〜", "~")
        .replace("∼", "~")
    )
    value = re.sub(r"\s+", " ", value).strip()
    return value


def parse_number(text: str):
    text = clean_text(text)

    if not text:
        return None

    mixed = MIXED_FRACTION_RE.fullmatch(text)
    if mixed:
        whole = int(mixed.group("whole"))
        num = int(mixed.group("num"))
        den = int(mixed.group("den"))
        if den == 0:
            return None
        return whole + (num / den)

    frac = FRACTION_RE.fullmatch(text)
    if frac:
        num = int(frac.group("num"))
        den = int(frac.group("den"))
        if den == 0:
            return None
        return num / den

    try:
        return float(text)
    except Exception:
        return None


def normalize_unit(unit: str) -> str:
    u = clean_text(unit)
    return UNIT_ALIASES.get(u, u)


def unit_type(unit: str) -> str:
    u = normalize_unit(unit)

    if u in MASS_TO_G:
        return "weight"

    if u in VOLUME_TO_ML:
        return "volume"

    if u in COUNT_UNITS:
        return "count"

    if u in QUALITATIVE_UNITS:
        return "qualitative"

    return "unknown"


def direct_weight_g(quantity, unit):
    if quantity is None:
        return None

    u = normalize_unit(unit)

    if u in MASS_TO_G:
        return float(quantity) * MASS_TO_G[u]

    return None
