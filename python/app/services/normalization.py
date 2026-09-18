from __future__ import annotations

import re
import unicodedata


# ------------------------------------------------------------
# Standard unit aliases
# ------------------------------------------------------------
UNIT_ALIASES = {
    # weight
    "公克": "g", "克": "g", "g": "g",
    "公斤": "kg", "kg": "kg",
    "斤": "jin", "兩": "liang", "钱": "qian", "錢": "qian",
    "盎司": "oz", "oz": "oz", "磅": "lb", "lb": "lb",

    # volume
    "毫升": "ml", "ml": "ml", "mL": "ml", "ML": "ml",
    "㏄": "ml", "cc": "ml", "CC": "ml", "c.c.": "ml",
    "C.C.": "ml", "c.c": "ml", "C.C": "ml",
    "公升": "l", "L": "l", "l": "l",
    "大匙": "tbsp", "湯匙": "tbsp",
    "小匙": "tsp", "茶匙": "tsp",

    # counting / package / serving
    "個": "個", "顆": "顆", "朵": "朵", "片": "片", "格": "格",
    "隻": "隻", "尾": "尾", "條": "條", "根": "根", "杯": "杯",
    "碗": "碗", "匙": "匙", "支": "支", "粒": "粒", "塊": "塊",
    "包": "包", "張": "張", "罐": "罐", "盒": "盒", "把": "把",
    "瓣": "瓣", "份": "份", "人份": "人份", "棵": "棵", "枝": "枝",
    "株": "株", "瓶": "瓶", "副": "副", "段": "段", "球": "球",
    "葉": "葉", "枚": "枚", "串": "串", "束": "束", "卷": "卷",
    "捲": "卷", "袋": "袋", "管": "管", "盤": "盤", "鍋": "鍋",
    "桶": "桶", "小塊": "小塊", "大塊": "大塊", "小片": "小片",
    "大片": "大片", "小段": "小段", "大段": "大段", "大碗": "大碗",
    "小碗": "小碗", "量杯": "量杯", "中匙": "中匙",

    # qualitative
    "少許": "少許", "適量": "適量", "酌量": "酌量",
    "少量": "少量", "些許": "些許",
}

MASS_TO_G = {
    "g": 1.0, "kg": 1000.0, "jin": 600.0,
    "liang": 37.5, "qian": 3.75,
    "oz": 28.349523125, "lb": 453.59237,
}

VOLUME_TO_ML = {
    "ml": 1.0, "l": 1000.0, "tbsp": 15.0, "tsp": 5.0,
}

COUNT_UNITS = {
    "個", "顆", "朵", "片", "格", "隻", "尾", "條", "根", "杯", "碗",
    "匙", "支", "粒", "塊", "包", "張", "罐", "盒", "把", "瓣", "份",
    "人份", "棵", "枝", "株", "瓶", "副", "段", "球", "葉", "枚", "串",
    "束", "卷", "袋", "管", "盤", "鍋", "桶", "小塊", "大塊", "小片",
    "大片", "小段", "大段", "大碗", "小碗", "量杯", "中匙",
}

QUALITATIVE_UNITS = {"少許", "適量", "酌量", "少量", "些許"}

# Only strip a one-letter group marker when a CJK ingredient follows it.
# This avoids corrupting real names such as X.O醬.
MATERIAL_GROUP_PREFIX_RE = re.compile(
    # YTower groups are normally A-I / 甲乙... / numeric markers.  Restricting
    # Latin markers to A-I avoids corrupting legitimate ingredient names such
    # as X.O醬, while still removing prefixes such as A.XO醬 / B.OREO餅乾.
    r"^\s*(?:\d{1,3}|[A-Ia-i]|[甲乙丙丁戊己庚辛壬癸])"
    r"\s*(?:[\.．、\):：\-]\s*)+(?=\S)"
)

SOFT_SUFFIXES = (
    "厚片", "薄片", "切片", "切絲", "切丁",
    "丁", "塊", "條", "片", "絲", "末", "碎",
    "粒", "葉", "梗", "蒂", "圈", "段", "泥", "茸", "蓉",
)

MATCH_PREP_PREFIXES = (
    "去皮去骨", "去皮", "去骨", "帶皮", "帶骨", "新鮮", "冷凍",
    "汆燙", "川燙", "燙熟", "煮熟", "炸熟", "熟", "生",
)

MATCH_ALIAS_VARIANTS = {
    "蛋": {"雞蛋"},
    "全蛋": {"雞蛋"},
    "蛋液": {"雞蛋"},
    "鮮奶": {"牛奶"},
    "鹽巴": {"鹽"},
    "蒜": {"大蒜", "蒜頭"},
    "薑": {"生薑", "薑仔"},
    "青蔥": {"蔥"},
    "蔥花": {"蔥"},
    "麻油": {"芝麻油"},
    "雞胸": {"雞胸肉", "去皮雞胸肉"},
    "雞胸肉": {"去皮雞胸肉"},
}

MIXED_FRACTION_RE = re.compile(
    r"^(?P<whole>\d+)\s*(?:又|\s+)\s*(?P<num>\d+)\s*/\s*(?P<den>\d+)$"
)
FRACTION_RE = re.compile(r"^(?P<num>\d+)\s*/\s*(?P<den>\d+)$")


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
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


def strip_material_group_prefix(value: str) -> str:
    return MATERIAL_GROUP_PREFIX_RE.sub("", clean_text(value), count=1).strip()


def parse_number(text: str):
    text = clean_text(text)
    if not text:
        return None

    mixed = MIXED_FRACTION_RE.fullmatch(text)
    if mixed:
        den = int(mixed.group("den"))
        if den == 0:
            return None
        return int(mixed.group("whole")) + int(mixed.group("num")) / den

    frac = FRACTION_RE.fullmatch(text)
    if frac:
        den = int(frac.group("den"))
        if den == 0:
            return None
        return int(frac.group("num")) / den

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


def canonicalize_ingredient_name(name: str) -> str:
    """Conservative canonicalization used for the DB ingredient key.

    It removes source formatting / preparation suffixes, but avoids broad food-family
    substitution so the original ingredient meaning is not silently changed.
    """
    s = strip_material_group_prefix(name)
    s = s.replace("蕃", "番")
    s = re.sub(r"^\s*約\s*", "", s)
    s = re.sub(r"\s*約\s*$", "", s).strip()

    changed = True
    while changed:
        changed = False
        for suffix in SOFT_SUFFIXES:
            if len(s) > len(suffix) + 1 and s.endswith(suffix):
                s = s[:-len(suffix)].strip()
                changed = True
                break

    return s


def nutrition_match_base(value: str) -> str:
    """Stronger normalization used only for nutrition candidate matching."""
    s = strip_material_group_prefix(value).replace("蕃", "番")
    s = re.sub(r"20\d{2}年取樣", "", s)
    s = re.sub(r"平均值", "", s)
    # Parenthetical content in the nutrition workbook is usually sample/state detail.
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"[\[\]【】,，、\s_\-\.]+", "", s)

    changed = True
    while changed:
        changed = False
        for suffix in SOFT_SUFFIXES:
            if len(s) > len(suffix) + 1 and s.endswith(suffix):
                s = s[:-len(suffix)]
                changed = True
                break
    return s


def nutrition_match_variants(value: str) -> set[str]:
    """Return conservative equivalent names for nutrition exact matching."""
    base = nutrition_match_base(value)
    if not base:
        return set()

    result = {base}
    current = base

    # Preparation state can block an otherwise exact food match.
    while True:
        stripped = None
        for prefix in MATCH_PREP_PREFIXES:
            if current.startswith(prefix) and len(current) > len(prefix) + 1:
                stripped = current[len(prefix):]
                break
        if not stripped:
            break
        current = stripped
        result.add(current)

    # Add only safe, high-frequency semantic aliases.
    for key in list(result):
        result.update(MATCH_ALIAS_VARIANTS.get(key, set()))

    return {nutrition_match_base(x) for x in result if x}
