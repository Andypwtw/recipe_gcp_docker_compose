#!/usr/bin/env python3
"""Match nutrition ingredients to Wan Jia Fu and update per-100g prices.

Catalog crawling, product matching, and unit conversion are embedded here so
this Airflow entry point runs without a separate wanjia_crawler.py module.

Docker/Airflow example:
  python /workspace/python/Price/wanjia_ytower_nutrition_comparison.py \
    --workbook /workspace/python/Price/ytower_nutrition_comparison.xlsx \
    --output-dir /workspace/data/processed/wanjia_ytower_comparison
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from collections import deque
from difflib import SequenceMatcher
import unicodedata

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from types import SimpleNamespace

from openpyxl import load_workbook
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

# All crawler helpers are embedded above for standalone Airflow execution.
BASE_URL = "https://online.uni-prosperity.com.tw"

CATEGORY_URLS = {
    "生鮮冷凍": f"{BASE_URL}/zh/%E7%94%9F%E9%AE%AE%E5%86%B7%E5%87%8D",
    "米油沖泡": f"{BASE_URL}/zh/%E7%B1%B3%E6%B2%B9%E6%B2%96%E6%B3%A1",
    "飲料零食": f"{BASE_URL}/zh/%E9%A3%B2%E6%96%99%E9%9B%B6%E9%A3%9F",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

ALIASES = {
    "大麥片": ("大燕麥片",),
    "即食燕麥片": ("即食大燕麥片", "燕麥片"),
    "即時燕麥片": ("即食大燕麥片", "燕麥片"),
    "甘扁桃仁片": ("杏仁片",),
    "小米": ("譽方媽媽小米",),
    "秈米": ("三好米",),
    "山藥": ("日本山藥",),
    "糯玉米": ("玉米",),
    "黑芝麻": ("黑芝麻粉",),
    "大西洋鮭魚": ("鮭魚輪切", "大西洋鮭魚輪切", "鮭魚"),
    "米": ("白米", "蓬萊米", "稻米", "米粒"),
    "無子檸檬": ("檸檬", "萊姆"),
}

FRUIT_SUFFIXES = (
    "檸檬", "萊姆", "柿子", "柿", "蓮霧", "西瓜", "蘋果", "芒果", "鳳梨",
    "香蕉", "橘子", "柑", "柳橙", "葡萄", "梨", "釋迦", "荔枝", "龍眼",
    "木瓜", "番石榴", "芭樂", "山竹", "草莓", "桃", "李", "柚子", "哈密瓜",
)

PROCESSED_FRUIT_RE = re.compile(r"果醬|果汁|果乾|蜜餞|果凍|水果醋|果醋|愛玉|茶飲|飲料|果泥")

NONFOOD_RE = re.compile(
    r"拖鞋|鞋(?!粉)|手機|平板|保護貼|手機殼|手環|錶帶|耳機|充電器|玩具|"
    r"服飾|清潔劑|洗衣精|收納盒|鍋具|餐具|盆栽|種子|飾品|飾物|首飾|"
    r"高凳|凳子|鍋墊|杯墊|濕紙巾|溼紙巾|紙巾|酒精抗菌|驅蚊|防蚊|噴霧|殺蟲|"
    r"佛像|摩尼佛|佛具|化妝品|保養品|香氛|寢具|衛生棉|清潔刷|垃圾袋|"
    r"油漆|漆筆|油漆筆|文具|電錘|撲克牌|濾心|濾芯|刮皮刀|純銀|s925|"
    r"麻將蓆|涼蓆|竹蓆|透氣雙人|玩偶|娃娃|濕巾|溼巾|去味|冰箱專用|"
    r"貓睡床|收納貓|睡床|蜂巢紙|紙收納|合金|牙刷|"
    r"保健食品|萃取物|膠囊|錠劑",
    re.IGNORECASE,
)

DRINK_PRODUCT_RE = re.compile(
    r"果汁|蔬果汁|四物飲|牛乳|鮮乳|調味乳|乳飲|奶茶|豆漿|米漿|飲料|飲品|"
    r"茶飲|汽水|沖泡飲|機能飲|水果醋|濃縮飲|酒類|啤酒|果醋",
    re.IGNORECASE,
)

BAKERY_RE = re.compile(
    r"麵包|吐司|餐包|可頌|貝果|蛋糕|餅乾|披薩|漢堡|烘焙|裸焙坊|橄欖形",
    re.IGNORECASE,
)

MEASURE_RE = re.compile(
    r"(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>公斤|千克|公克|毫升|公升|台斤|台兩|斤|兩|kg|克|g|ml|l)"
    r"(?=$|[^a-z]|[x×*]\s*\d)",
    re.IGNORECASE,
)

PRICE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

REJECTED_PRODUCT_PATHS = {"B303026187101.html"}

def create_session() -> requests.Session:
    """Retry transient DNS/connectivity and server errors before recording a miss."""
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=2,
        status=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session

def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)

def base_name(value: str) -> str:
    original = clean(value)
    result = re.sub(r"[（(].*?[）)]", "", original)
    result = re.sub(r"(平均值|平均|樣品|取樣)$", "", result).strip()
    return result or original

def split_names(value: str) -> list[str]:
    return [base_name(token) for token in re.split(r"[,，、;；/|\s]+", clean(value)) if token]

def sample_search_terms(sample: dict) -> list[str]:
    name = base_name(sample.get("樣品名稱", ""))
    terms = [name, *ALIASES.get(name, ())]
    for field in ("俗名", "建議搜尋詞"):
        for term in split_names(sample.get(field, "")):
            terms.extend((term, *ALIASES.get(term, ())))
    # Add a shorter fruit-specific search term for cultivar/condition names.
    # Never shorten arbitrary ingredients: only known fruit suffixes qualify.
    fruit_inputs = list(terms)
    for term in fruit_inputs:
        compact = normalize(term)
        for suffix in FRUIT_SUFFIXES:
            suffix_key = normalize(suffix)
            if compact.endswith(suffix_key) and len(compact) > len(suffix_key):
                terms.append(suffix)
                if suffix in {"柿", "柿子"}:
                    terms.append("柿子")
                break
    if any("柿" in normalize(term) for term in fruit_inputs):
        terms.append("柿子")
    result, seen = [], set()
    for term in terms:
        key = normalize(term)
        if len(key) >= 2 and key not in seen:
            result.append(clean(term))
            seen.add(key)
    return result

def product_name(element) -> str:
    for selector in (
        "a.gtm-product-alink[data-name]", "[data-name]", ".pdp-link a",
        ".product-name a", ".product-name", "a[title]",
    ):
        node = element if getattr(element, "name", None) == "a" and selector.startswith("a.") else element.select_one(selector)
        if node:
            name = clean(node.get("data-name") or node.get("title") or node.get_text(" ", strip=True))
            if name:
                return name
    return ""

def numeric_price(value: object) -> float | None:
    match = PRICE_RE.search(clean(value).replace("$", ""))
    if not match:
        return None
    try:
        amount = float(match.group(0).replace(",", ""))
        return amount if amount > 0 else None
    except ValueError:
        return None

def extract_products(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products: dict[str, dict] = {}
    elements = list(soup.select(
        "#productgrid .hot-recommend-item, .hot-recommend-item, #productgrid .product, "
        "#productgrid .product-tile, #productgrid .grid-tile, .product-tile, [data-pid], [data-product-id]"
    )) + list(soup.select("a.gtm-product-alink[data-name], a[data-name]"))
    for element in elements:
        link = element if getattr(element, "name", None) == "a" else element.select_one("a[href]")
        name = product_name(element)
        if not name and link:
            name = clean(link.get("data-name") or link.get("title") or link.get_text(" ", strip=True))
        if not name or NONFOOD_RE.search(name):
            continue
        href = clean(link.get("href", "")) if link else ""
        price_value = (link or element).get("data-price", "")
        if not price_value:
            price_node = element.select_one(".sales .value, .sales, .price .value, .price, [itemprop='price'], [class*='price']")
            if price_node:
                price_value = price_node.get("content") or price_node.get_text(" ", strip=True)
        key = normalize(name)
        if key:
            products.setdefault(key, {
                "商品名稱": name,
                "商品價格": numeric_price(price_value),
                "商品網址": urljoin(page_url, href),
            })

    for node in soup.select('script[type="application/ld+json"]'):
        try:
            parsed = json.loads(node.string or node.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        stack = parsed if isinstance(parsed, list) else [parsed]
        while stack:
            obj = stack.pop()
            if not isinstance(obj, dict):
                continue
            if isinstance(obj.get("@graph"), list):
                stack.extend(obj["@graph"])
            if obj.get("@type") != "Product" or not obj.get("name"):
                continue
            name = clean(obj["name"])
            if NONFOOD_RE.search(name):
                continue
            offers = obj.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            key = normalize(name)
            products.setdefault(key, {
                "商品名稱": name,
                "商品價格": numeric_price(offers.get("price")) if isinstance(offers, dict) else None,
                "商品網址": urljoin(page_url, clean(obj.get("url", ""))),
            })
    return list(products.values())

def page_links(soup: BeautifulSoup, current_url: str, root_path: str) -> list[str]:
    host = urlparse(BASE_URL).netloc
    links = []
    for anchor in soup.select("a[href]"):
        href = clean(anchor.get("href"))
        onclick = clean(anchor.get("onclick"))
        match = re.search(r"p_page\(['\"]([^'\"]+)['\"]\)", onclick)
        if match:
            href = match.group(1)
        elif not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(current_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc != host:
            continue
        query = parse_qs(parsed.query)
        cls = " ".join(anchor.get("class", [])).lower()
        label = clean(anchor.get_text(" ", strip=True))
        same_root = parsed.path.rstrip("/") == root_path.rstrip("/")
        ajax_page = parsed.path.endswith("/Search-UpdateGrid") and "start" in query
        category_child = parsed.path.startswith(root_path.rstrip("/") + "/") and not parsed.path.lower().endswith(".html")
        paging = parsed.path.endswith("/Search-UpdateGrid") or "start" in query or "page" in query or "pagination" in cls or label in {"下一頁", "下頁", "Next", "›", "»"}
        if (paging and (same_root or ajax_page)) or category_child:
            links.append(absolute)
    return links

def get_html(session: requests.Session, url: str, timeout: float) -> tuple[str, str]:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text, response.url

def crawl_catalog(session: requests.Session, max_pages: int, timeout: float, delay: float,
                  network_failures: list[dict]) -> list[dict]:
    queue = deque((url, urlparse(url).path, category) for category, url in CATEGORY_URLS.items())
    visited: set[str] = set()
    products: dict[str, dict] = {}
    while queue and (max_pages <= 0 or len(visited) < max_pages):
        url, root_path, root_category = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        page_limit = str(max_pages) if max_pages > 0 else "不限"
        print(f"型錄 {len(visited)}/{page_limit}：{url}", flush=True)
        try:
            html, final_url = get_html(session, url, timeout)
        except requests.RequestException as exc:
            print(f"警告：型錄頁讀取失敗：{exc}", file=sys.stderr, flush=True)
            network_failures.append({"階段": "型錄", "網址": url, "錯誤": str(exc)})
            continue
        for product in extract_products(html, final_url):
            product.setdefault("網站大類", root_category)
            products.setdefault(normalize(product["商品名稱"]), product)
        for link in page_links(BeautifulSoup(html, "html.parser"), final_url, root_path):
            if link not in visited and all(link != queued[0] for queued in queue):
                queue.append((link, root_path, root_category))
        if queue:
            time.sleep(max(0.0, delay))
    if queue and max_pages > 0:
        print(f"警告：型錄頁數達上限 {max_pages}，尚有 {len(queue)} 個分類/分頁待讀取。請提高 --max-pages 後重跑。", file=sys.stderr, flush=True)
    print(f"型錄商品讀取：{len(products)} 筆；瀏覽頁數：{len(visited)}", flush=True)
    return list(products.values())

def plausible(sample: dict, product_name_value: str) -> bool:
    if NONFOOD_RE.search(product_name_value):
        return False
    category = clean(sample.get("食品分類", ""))
    name = clean(sample.get("樣品名稱", ""))
    if ("穀物" in category or "小麥胚芽" in name) and BAKERY_RE.search(product_name_value):
        return False
    if "果" in category and not re.search(r"果汁|果乾|果醬|蜜餞|果凍", name):
        if PROCESSED_FRUIT_RE.search(product_name_value):
            return False
    # A raw ingredient should not match a beverage/juice that merely contains
    # its name (e.g. 山竹 -> 山竹果汁, 黑芝麻 -> 黑芝麻牛乳, 青木瓜 -> 四物飲).
    terms = sample_search_terms(sample)
    if DRINK_PRODUCT_RE.search(product_name_value) and not DRINK_PRODUCT_RE.search(" ".join(terms)):
        return False
    return True

def score_terms(terms: list[str], product_name_value: str) -> tuple[float, str]:
    product = normalize(product_name_value)
    best = (0.0, "")
    for term in terms:
        key = normalize(term)
        if not key:
            continue
        score = 1.0 if len(key) >= 2 and key in product else SequenceMatcher(None, key, product).ratio()
        if score > best[0]:
            best = (score, term)
    return best

def matching_candidates(sample: dict, products: list[dict], threshold: float) -> list[dict]:
    terms = sample_search_terms(sample)
    candidates = []
    for product in products:
        if urlparse(product.get("商品網址", "")).path.rsplit("/", 1)[-1] in REJECTED_PRODUCT_PATHS:
            continue
        if not plausible(sample, product["商品名稱"]):
            continue
        score, term = score_terms(terms, product["商品名稱"])
        direct = any(normalize(term) in normalize(product["商品名稱"]) for term in terms)
        if score >= threshold and (direct or score >= max(0.84, threshold)):
            candidates.append({**product, "相似度": round(score, 4), "命中搜尋詞": term})
    candidates.sort(key=lambda candidate: candidate["相似度"], reverse=True)
    return candidates

def unit_measure(amount: float, unit: str, following: str) -> dict:
    unit = unit.lower()
    multiplier_match = re.match(r"\s*(?:克)?\s*[x×*]\s*(\d+(?:\.\d+)?)", following, re.IGNORECASE)
    multiplier = float(multiplier_match.group(1)) if multiplier_match else 1.0
    amount *= multiplier
    if unit in {"台斤", "斤"}:
        grams = amount * 600
        return {"重量g": grams, "容量ml": None, "規格": f"{amount:g}{unit}", "密度假設": ""}
    if unit in {"台兩", "兩"}:
        grams = amount * 37.5
        return {"重量g": grams, "容量ml": None, "規格": f"{amount:g}{unit}", "密度假設": ""}
    if unit in {"公斤", "千克", "kg"}:
        return {"重量g": amount * 1000, "容量ml": None, "規格": f"{amount:g}kg", "密度假設": ""}
    if unit in {"公克", "克", "g"}:
        return {"重量g": amount, "容量ml": None, "規格": f"{amount:g}g", "密度假設": ""}
    if unit in {"公升", "l"}:
        volume_ml = amount * 1000
        return {"重量g": volume_ml, "容量ml": volume_ml, "規格": f"{amount:g}L", "密度假設": "1 ml = 1 g"}
    return {"重量g": amount, "容量ml": amount, "規格": f"{amount:g}ml", "密度假設": "1 ml = 1 g"}

def extract_measure(value: str) -> dict:
    text = clean(value)
    match = MEASURE_RE.search(text)
    if not match:
        return {"重量g": None, "容量ml": None, "規格": "", "密度假設": ""}
    return unit_measure(float(match.group("amount")), match.group("unit"), text[match.end():])

COUNT_UNIT_RE = re.compile(r"(?P<count>\d+(?:\.\d+)?)\s*(?P<unit>片|排|顆|粒|個|尾|隻|根|條|瓣|朵|枚)")

def estimate_count_measure(sample: dict, text: str) -> dict:
    """Conservative item-weight estimates for explicit count packaging only.

    Estimates are deliberately limited to common named foods with a reasonably
    recognizable unit. They are marked as estimates in the output.
    """
    match = COUNT_UNIT_RE.search(clean(text))
    if not match:
        return {"重量g": None, "容量ml": None, "規格": "", "密度假設": "", "重量估算說明": ""}
    count = float(match.group("count"))
    unit = match.group("unit")
    food = normalize(" ".join((sample.get("樣品名稱", ""), sample.get("俗名", ""), text)))
    grams_each = None
    label = ""
    if unit in {"片", "排"} and re.search(r"豬排|大排|里肌排|豬扒", food):
        grams_each, label = 150, "豬排按每片150g估算"
    elif unit in {"片", "排"} and re.search(r"鮭魚|魚排|魚片", food):
        grams_each, label = 120, "魚排/魚片按每片120g估算"
    elif unit in {"顆", "個"} and re.search(r"雞蛋|鴨蛋|鵪鶉蛋", food):
        grams_each, label = (55 if "鵪鶉" not in food else 10), "蛋按單顆平均重量估算"
    elif unit in {"顆", "個"} and re.search(r"蘋果", food):
        grams_each, label = 180, "蘋果按每顆180g估算"
    elif unit in {"顆", "個"} and re.search(r"蓮霧", food):
        grams_each, label = 120, "蓮霧按每顆120g估算"
    elif unit in {"顆", "個"} and re.search(r"柿", food):
        grams_each, label = 150, "柿子按每顆150g估算"
    elif unit in {"顆", "個"} and re.search(r"檸檬|萊姆", food):
        grams_each, label = 100, "檸檬/萊姆按每顆100g估算"
    elif unit in {"根", "條"} and re.search(r"香蕉", food):
        grams_each, label = 120, "香蕉按每根120g估算"
    elif unit in {"尾", "隻"} and re.search(r"蝦", food):
        grams_each, label = 20, "蝦按每尾20g估算"
    elif unit == "尾" and re.search(r"魚|鯛|鯖|鱸|鮭|鱈", food):
        grams_each, label = 400, "魚按每尾400g估算"
    elif unit == "隻" and re.search(r"雞", food):
        grams_each, label = 1500, "全雞按每隻1500g估算"
    elif unit in {"顆", "個"} and re.search(r"番茄|蕃茄", food):
        grams_each, label = 120, "番茄按每顆120g估算"
    elif unit in {"顆", "個"} and re.search(r"高麗菜|甘藍|白菜", food):
        grams_each, label = 1000, "球葉蔬菜按每顆1000g估算"
    elif unit in {"根", "條"} and re.search(r"竹筍|筍", food):
        grams_each, label = 300, "竹筍按每條300g估算"
    elif unit == "瓣" and re.search(r"蒜", food):
        grams_each, label = 5, "蒜瓣按每瓣5g估算"
    elif unit == "朵" and re.search(r"香菇|蘑菇|菇", food):
        grams_each, label = 15, "單朵菇類按每朵15g估算"
    elif unit == "枚" and re.search(r"蛋", food):
        grams_each, label = 55, "蛋按每枚55g估算"
    if grams_each is None:
        return {"重量g": None, "容量ml": None, "規格": "", "密度假設": "", "重量估算說明": ""}
    grams = count * grams_each
    return {
        "重量g": grams, "容量ml": None,
        "規格": f"{count:g}{unit}（估算{grams_each:g}g/{unit}）",
        "密度假設": "", "重量估算說明": label,
    }

def price_per_100g(product: dict, measure: dict) -> tuple[float | None, str]:
    price = product.get("商品價格")
    grams = measure.get("重量g")
    if price is None:
        return None, "網站未提供可辨識的商品價格"
    if grams is None:
        return None, "未讀到商品重量規格"
    if grams <= 0:
        return None, "商品重量不是正數"
    return round(price / grams * 100, 4), ""

def fetch_spec(session: requests.Session, url: str, timeout: float) -> tuple[str, dict]:
    if not url:
        return "", {"重量g": None, "容量ml": None, "規格": "", "密度假設": ""}
    try:
        html, _ = get_html(session, url, timeout)
    except requests.RequestException as exc:
        return f"商品規格頁讀取失敗：{exc}", {"重量g": None, "容量ml": None, "規格": "", "密度假設": ""}
    soup = BeautifulSoup(html, "html.parser")
    spec_text = ""
    for text in soup.stripped_strings:
        if "規格" in text:
            part = re.split(r"規格\s*[:：]", clean(text), maxsplit=1)
            if len(part) == 2 and (MEASURE_RE.search(part[1]) or COUNT_UNIT_RE.search(part[1])):
                spec_text = part[1]
                break
    if not spec_text:
        for node in soup.find_all(string=re.compile(r"規格\s*[:：]")):
            parent = node.parent.parent if node.parent and node.parent.parent else node.parent
            if parent:
                match = re.search(r"規格\s*[:：]\s*(.*)", clean(parent.get_text(" ", strip=True)))
                if match and (MEASURE_RE.search(match.group(1)) or COUNT_UNIT_RE.search(match.group(1))):
                    spec_text = match.group(1)
                    break
    return spec_text, extract_measure(spec_text)

def measure_for_candidate(sample: dict, session: requests.Session, candidate: dict, timeout: float, cache: dict) -> tuple[str, dict]:
    measure = extract_measure(candidate["商品名稱"])
    measure.setdefault("重量估算說明", "")
    spec_text = measure["規格"]
    if not measure["規格"]:
        url = candidate.get("商品網址", "")
        if url not in cache:
            cache[url] = fetch_spec(session, url, timeout)
        spec_text, measure = cache[url]
    if not measure.get("重量g"):
        estimate = estimate_count_measure(sample, spec_text or candidate.get("商品名稱", ""))
        if estimate.get("重量g"):
            measure = estimate
            spec_text = estimate["規格"]
    return spec_text, measure

def choose_measurable_candidate(sample: dict, candidates: list[dict], session: requests.Session, timeout: float,
                                spec_cache: dict, max_detail_checks: int = 40) -> tuple[dict, str, dict] | None:
    """Prefer the highest-ranked candidate with price and a readable mass/volume."""
    fallback = None
    detail_checks = 0
    for candidate in candidates:
        has_title_measure = bool(extract_measure(candidate.get("商品名稱", "")).get("規格"))
        if not has_title_measure and detail_checks >= max_detail_checks:
            continue
        if not has_title_measure:
            detail_checks += 1
        spec, measure = measure_for_candidate(sample, session, candidate, timeout, spec_cache)
        if fallback is None:
            fallback = (candidate, spec, measure)
        if measure.get("重量g") and candidate.get("商品價格") is not None:
            return candidate, spec, measure
    return fallback

def match_from_pool(sample: dict, products: list[dict], threshold: float, session: requests.Session,
                    timeout: float, spec_cache: dict) -> dict | None:
    selected = choose_measurable_candidate(
        sample, matching_candidates(sample, products, threshold), session, timeout, spec_cache,
    )
    if not selected:
        return None
    candidate, spec, measure = selected
    per_100g, reason = price_per_100g(candidate, measure)
    return {
        **{key: sample.get(key, "") for key in ("來源列號", "食品分類", "樣品名稱", "俗名")},
        "狀態": "成功" if per_100g is not None else "找到商品但未換算",
        "補查基礎食材": base_name(sample.get("樣品名稱", "")),
        "商品名稱": candidate["商品名稱"],
        "商品網址": candidate.get("商品網址", ""),
        "網站大類": candidate.get("網站大類") or "站內搜尋",
        "商品價格": candidate.get("商品價格"),
        "規格": spec or measure.get("規格", ""),
        "重量g": measure.get("重量g"),
        "密度假設": measure.get("密度假設", ""),
        "重量估算說明": measure.get("重量估算說明", ""),
        "每100g的價格": per_100g,
        "相似度": candidate.get("相似度"),
        "命中搜尋詞": candidate.get("命中搜尋詞", ""),
        "未換算原因": reason,
        "Excel列": sample.get("Excel列"),
    }

crawler = SimpleNamespace(
    ALIASES=ALIASES,
    BASE_URL=BASE_URL,
    CATEGORY_URLS=CATEGORY_URLS,
    BeautifulSoup=BeautifulSoup,
    base_name=base_name,
    clean=clean,
    crawl_catalog=crawl_catalog,
    create_session=create_session,
    extract_measure=extract_measure,
    extract_products=extract_products,
    get_html=get_html,
    match_from_pool=match_from_pool,
    normalize=normalize,
    price_per_100g=price_per_100g,
    sample_search_terms=sample_search_terms,
)

# A's 「米飯」 and B's 「白飯」 refer to cooked rice here. Search the store's
# shelf-stable cooked-rice term as well, and prefer this verified product page
# whenever it appears in a result set.
RICE_PRODUCT_URL = f"{crawler.BASE_URL}/zh/2020109100101.html"
crawler.ALIASES.update({
    "米飯": ("常溫熟飯", "白飯"),
    "白飯": ("常溫熟飯", "米飯"),
    "常溫熟飯": ("白飯", "米飯"),
})

HEADER_ROW = 2
DEFAULT_SHEET = "食材對照"
NUTRITION_SHEET = "台灣食品成分表"
NUTRITION_PRICE_HEADER = "每100g的價格"
BASE_INGREDIENT_PREFIXES = (
    "日本", "台灣", "臺灣", "本產", "進口", "國產", "有機", "新鮮", "冷凍", "冷藏",
    "去皮", "帶皮", "去骨", "帶骨", "切片", "切丁", "切絲", "切末", "切塊",
)
NONFOOD_RESULT_RE = re.compile(r"烤麵包機|烤吐司機|吐司機|麵包機|烤箱")
OUTPUT_HEADERS = (
    "萬家福狀態",
    "萬家福商品名稱",
    "萬家福商品網址",
    "萬家福網站大類",
    "萬家福商品價格",
    "萬家福規格",
    "萬家福重量g",
    "萬家福每100g價格",
    "萬家福相似度",
    "萬家福命中搜尋詞",
    "萬家福搜尋詞",
    "萬家福未換算原因",
)
CHECKPOINT_VERSION = 1


def default_workbook() -> Path:
    configured = os.environ.get("YTOWER_COMPARISON_XLSX")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().with_name("ytower_nutrition_comparison.xlsx")


def default_nutrition_workbook() -> Path:
    configured = os.environ.get("FOOD_NUTRITION_XLSX")
    if configured:
        return Path(configured).expanduser()
    # In Docker, __file__ is /workspace/python/Price/...; locally it is
    # <repository>/python/Price/.... Both resolve to the project data folder.
    return Path(__file__).resolve().parents[2] / "data" / "reference" / "food_nutrition_2025.xlsx"


def clean(value: Any) -> str:
    return crawler.clean(value)


def search_base_name(value: str) -> str:
    """Remove common origin/state descriptors so price searches use the food itself."""
    name = crawler.base_name(value)
    previous = None
    while name != previous:
        previous = name
        for prefix in BASE_INGREDIENT_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix):].strip()
                break
    return name or crawler.base_name(value)


def group_key(group: list[dict[str, Any]]) -> str:
    return crawler.normalize(search_base_name(group[0]["樣品名稱"]))


def checkpoint_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    state_path = args.checkpoint or (args.output_dir / "wanjia_ytower_comparison.checkpoint.json")
    return state_path, state_path.with_suffix(".results.jsonl"), state_path.with_suffix(".catalog.json")


def checkpoint_signature(samples: list[dict[str, Any]], args: argparse.Namespace) -> str:
    source = {
        "samples": [{key: sample.get(key) for key in
                     ("Excel列", "食品分類", "樣品名稱", "俗名", "建議搜尋詞", "營養樣品名稱空白")}
                    for sample in samples],
        "sheet": args.sheet,
        "nutrition_sheet": args.nutrition_sheet,
        "threshold": args.threshold,
        "max_pages": args.max_pages,
        "search_limit": args.search_limit,
    }
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def load_checkpoint(args: argparse.Namespace, samples: list[dict[str, Any]]) -> dict[str, Any]:
    state_path, journal_path, catalog_path = checkpoint_paths(args)
    signature = checkpoint_signature(samples, args)
    if args.restart:
        for path in (state_path, journal_path, catalog_path):
            if path.exists():
                path.unlink()
    state = {"version": CHECKPOINT_VERSION, "signature": signature, "searched_count": 0,
             "active": None, "network_failures": [], "resumed": False}
    if state_path.exists():
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            if saved.get("version") == CHECKPOINT_VERSION and saved.get("signature") == signature:
                state.update(saved)
                state["resumed"] = True
            else:
                print("檢查點與目前食材清單或搜尋設定不同，將從頭開始。", flush=True)
                journal_path.write_text("", encoding="utf-8")
                if catalog_path.exists():
                    catalog_path.unlink()
        except (OSError, json.JSONDecodeError):
            print("檢查點無法讀取，將從頭開始。", flush=True)
            journal_path.write_text("", encoding="utf-8")
    if not state["resumed"] and journal_path.exists():
        journal_path.write_text("", encoding="utf-8")
    state["results"] = {}
    state["completed_groups"] = set()
    if state["resumed"] and journal_path.exists():
        try:
            for line in journal_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                key = entry["group_key"]
                for row in entry.get("rows", []):
                    state["results"][int(row["Excel列"])] = row
                if entry.get("complete"):
                    state["completed_groups"].add(key)
                else:
                    state["completed_groups"].discard(key)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            state["results"] = {}
            state["completed_groups"] = set()
            state["active"] = None
            state["resumed"] = False
            journal_path.write_text("", encoding="utf-8")
            print("結果記錄不完整，將從頭開始。", flush=True)
    state["state_path"], state["journal_path"], state["catalog_path"] = state_path, journal_path, catalog_path
    state["signature"] = signature
    save_checkpoint_state(state)
    if state["resumed"]:
        print(f"續跑檢查點：已完成 {len(state['completed_groups'])} 組；搜尋請求累計 {state['searched_count']} 次。", flush=True)
    return state


def save_checkpoint_state(state: dict[str, Any]) -> None:
    persisted = {key: value for key, value in state.items()
                 if key not in {"results", "completed_groups", "state_path", "journal_path", "catalog_path"}}
    write_json_atomic(state["state_path"], persisted)


def save_group_checkpoint(state: dict[str, Any], key: str,
                          rows: list[dict[str, Any]], complete: bool) -> None:
    with state["journal_path"].open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"group_key": key, "rows": rows, "complete": complete}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    for row in rows:
        state["results"][int(row["Excel列"])] = row
    if complete:
        state["completed_groups"].add(key)
    else:
        state["completed_groups"].discard(key)
    save_checkpoint_state(state)


def read_ingredients(workbook_path: Path, sheet_name: str) -> list[dict[str, Any]]:
    """Read A-column ingredient rows and capture B/C/E as extra search context."""
    book = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        if sheet_name not in book.sheetnames:
            raise ValueError(f"工作簿找不到工作表「{sheet_name}」")
        sheet = book[sheet_name]
        ingredients: list[dict[str, Any]] = []
        for excel_row, values in enumerate(
            sheet.iter_rows(min_row=HEADER_ROW + 1, values_only=True),
            start=HEADER_ROW + 1,
        ):
            row = list(values)
            name = clean(row[0] if row else "")
            if not name:
                continue
            # Category divider rows have only a label in A; actual ingredient
            # rows carry their nutrition category in E, even when B-D are blank.
            has_other_content = any(
                clean(row[index])
                for index in (1, 2, 3, 4)
                if index < len(row) and row[index] is not None
            )
            if not has_other_content:
                continue
            ingredients.append({
                "Excel列": excel_row,
                "來源列號": str(excel_row),
                "食品分類": clean(row[4] if len(row) > 4 else ""),
                "樣品名稱": name,
                "基礎食材": search_base_name(name),
                "營養樣品名稱空白": not clean(row[1] if len(row) > 1 else ""),
                # Existing sample name and alias columns are helpful additional
                # search terms; column A remains the term being priced.
                "俗名": clean(row[2] if len(row) > 2 else ""),
                "建議搜尋詞": clean(row[1] if len(row) > 1 else ""),
            })
        return ingredients
    finally:
        book.close()


def ingredient_groups(samples: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        key = crawler.normalize(search_base_name(sample["樣品名稱"]))
        if key:
            grouped.setdefault(key, []).append(sample)
    return list(grouped.values())


def representative(group: list[dict[str, Any]]) -> dict[str, Any]:
    result = group[0].copy()
    aliases = []
    searches = []
    for sample in group:
        for value, destination in ((sample.get("俗名"), aliases), (sample.get("建議搜尋詞"), searches)):
            value = clean(value)
            if value and value not in destination:
                destination.append(value)
    result["俗名"] = "、".join(aliases)
    result["建議搜尋詞"] = "、".join(searches)
    result["樣品名稱"] = search_base_name(result["樣品名稱"])
    return result


def prioritize_rice_product(products: list[dict[str, Any]], sample: dict[str, Any]) -> list[dict[str, Any]]:
    """Put the requested cooked-rice product first for rice ingredient groups."""
    values = " ".join(clean(sample.get(field)) for field in ("樣品名稱", "俗名", "建議搜尋詞"))
    is_rice = any(crawler.normalize(term) in crawler.normalize(values)
                  for term in ("米飯", "白飯", "常溫熟飯"))
    if not is_rice:
        return products
    preferred = [p for p in products if clean(p.get("商品網址", p.get("網址", ""))).rstrip("/") == RICE_PRODUCT_URL.rstrip("/")]
    if not preferred:
        return products
    preferred_ids = {id(p) for p in preferred}
    return preferred + [p for p in products if id(p) not in preferred_ids]


def fan_out(match: dict[str, Any], group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for sample in group:
        row = dict(match)
        row.update({
            key: sample.get(key, "")
            for key in ("來源列號", "食品分類", "樣品名稱", "俗名")
        })
        row["Excel列"] = sample["Excel列"]
        row["補查基礎食材"] = search_base_name(sample["樣品名稱"])
        row["站內搜尋詞"] = row.get("站內搜尋詞", [])
        rows.append(row)
    return rows


def no_match_row(sample: dict[str, Any], attempted: list[str], failed_terms: list[str],
                 network_failures: list[dict[str, Any]]) -> dict[str, Any]:
    catalog_failed = any(error.get("階段") == "型錄" for error in network_failures)
    if attempted and len(failed_terms) == len(attempted):
        reason = "所有已嘗試的站內搜尋都因網路/DNS解析失敗，未能確認是否有商品；請恢復網路後重跑"
    elif failed_terms:
        reason = "型錄與成功完成的站內搜尋未找到符合候選；部分搜尋遇到網路/DNS解析失敗，建議重跑確認"
    elif catalog_failed:
        reason = "未找到符合候選；本次有型錄頁因網路/DNS失敗未讀取，請恢復網路後重跑確認"
    else:
        reason = "分類型錄及已嘗試的站內搜尋詞均未找到符合候選"
    return {
        **{key: sample.get(key, "") for key in ("來源列號", "食品分類", "樣品名稱", "俗名")},
        "狀態": "未找到",
        "補查基礎食材": search_base_name(sample["樣品名稱"]),
        "商品名稱": "", "商品網址": "", "網站大類": "", "商品價格": None,
        "規格": "", "重量g": None, "重量估算說明": "", "每100g的價格": None,
        "密度假設": "", "相似度": None, "命中搜尋詞": "",
        "站內搜尋詞": attempted, "未換算原因": reason,
        "Excel列": sample["Excel列"],
    }


def external_search_results(session, query: str, timeout: float) -> list[dict[str, str]]:
    """Read ordinary web-search titles/snippets for conservative measure/price fallbacks."""
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    response = session.get(url, timeout=timeout, headers={"Accept-Language": "zh-TW,zh;q=0.9"})
    response.raise_for_status()
    soup = crawler.BeautifulSoup(response.text, "html.parser")
    results = []
    for result in soup.select(".result"):
        title_node = result.select_one(".result__title")
        snippet_node = result.select_one(".result__snippet")
        title = clean(title_node.get_text(" ", strip=True)) if title_node else ""
        snippet = clean(snippet_node.get_text(" ", strip=True)) if snippet_node else ""
        if title or snippet:
            results.append({"標題": title, "摘要": snippet})
    return results


def web_measure_fallback(session, ingredient: str, product_name: str, timeout: float,
                         delay: float, failures: list[dict[str, Any]]) -> dict[str, Any] | None:
    queries = (
        f'"{product_name}" 內容量',
        f'"{ingredient}" "{product_name}" 重量 體積',
    )
    for query in queries:
        try:
            results = external_search_results(session, query, timeout)
        except Exception as exc:
            failures.append({"階段": "網路重量補查", "食材": ingredient, "搜尋詞": query, "錯誤": str(exc)})
            time.sleep(max(0.0, delay))
            continue
        for item in results:
            text = f"{item['標題']} {item['摘要']}"
            measure = crawler.extract_measure(text)
            key = crawler.normalize(ingredient)
            if key and key in crawler.normalize(text) and measure.get("重量g"):
                return {**measure, "來源": "網路搜尋摘要", "搜尋詞": query, "摘要": text}
        time.sleep(max(0.0, delay))
    return None


def web_price_fallback(session, ingredient: str, timeout: float, delay: float,
                       failures: list[dict[str, Any]]) -> dict[str, Any] | None:
    queries = (f'"{ingredient}" 價格 內容量', f'"{ingredient}" 重量 體積 價格')
    for query in queries:
        try:
            results = external_search_results(session, query, timeout)
        except Exception as exc:
            failures.append({"階段": "網路價格補查", "食材": ingredient, "搜尋詞": query, "錯誤": str(exc)})
            time.sleep(max(0.0, delay))
            continue
        for item in results:
            text = f"{item['標題']} {item['摘要']}"
            if not ingredient or crawler.normalize(ingredient) not in crawler.normalize(text):
                continue
            measure = crawler.extract_measure(text)
            price_match = re.search(r"(?:NT\s*\$?|TWD\s*|(?:售價|價格|特價|市價)\s*[:：]?\s*|\$\s*)([\d,]+(?:\.\d+)?)\s*(?:元|NTD)?", text, re.I)
            if not price_match or not measure.get("重量g"):
                continue
            price = float(price_match.group(1).replace(",", ""))
            if price <= 0:
                continue
            per_100g = round(price / measure["重量g"] * 100, 4)
            return {
                "商品名稱": item["標題"], "商品網址": "", "商品價格": price,
                "規格": measure.get("規格", ""), "重量g": measure["重量g"],
                "每100g的價格": per_100g, "補查來源": "網路搜尋摘要",
                "補查搜尋詞": query, "補查摘要": text,
            }
        time.sleep(max(0.0, delay))
    return None


def complete_missing_weight(match: dict[str, Any], sample: dict[str, Any], session,
                            args: argparse.Namespace, network_failures: list[dict[str, Any]]) -> None:
    if match.get("重量g") or NONFOOD_RESULT_RE.search(clean(match.get("商品名稱"))):
        return
    measure = web_measure_fallback(
        session, search_base_name(sample["樣品名稱"]), clean(match.get("商品名稱")),
        args.timeout, args.delay, network_failures,
    )
    if not measure:
        return
    match["重量g"] = measure["重量g"]
    match["規格"] = measure.get("規格", "")
    match["每100g的價格"] = crawler.price_per_100g(
        {"商品價格": match.get("商品價格")}, measure,
    )[0]
    match["狀態"] = "成功" if match["每100g的價格"] is not None else "找到商品但未換算"
    match["未換算原因"] = "" if match["每100g的價格"] is not None else "網路補查重量後仍缺少萬家福商品價格"
    match["重量估算說明"] = "網路搜尋摘要補查；請核對來源"
    match["密度假設"] = measure.get("密度假設", "")
    match["重量補查來源"] = f"{measure['來源']}：{measure['搜尋詞']}"


def write_back_nutrition_workbook(nutrition_path: Path, samples: list[dict[str, Any]],
                                  results: list[dict[str, Any]], sheet_name: str) -> tuple[int, int, Path]:
    if not nutrition_path.is_file():
        raise FileNotFoundError(f"找不到原始營養資料工作簿：{nutrition_path}")
    backup_path = nutrition_path.with_name(nutrition_path.stem + ".before_wanjia_price.xlsx")
    if not backup_path.exists():
        shutil.copy2(nutrition_path, backup_path)
    book = load_workbook(nutrition_path)
    try:
        if sheet_name not in book.sheetnames:
            raise ValueError(f"原始營養資料找不到工作表「{sheet_name}」")
        sheet = book[sheet_name]
        headers = {clean(sheet.cell(HEADER_ROW, col).value): col for col in range(1, sheet.max_column + 1)}
        name_col = headers.get("樣品名稱")
        category_col = headers.get("食品分類")
        alias_col = headers.get("俗名")
        if not name_col:
            raise ValueError("原始營養資料缺少「樣品名稱」欄")
        price_col = headers.get(NUTRITION_PRICE_HEADER)
        if not price_col:
            price_col = sheet.max_column + 1
            sheet.cell(HEADER_ROW, price_col).value = NUTRITION_PRICE_HEADER
        existing: dict[str, list[int]] = defaultdict(list)
        existing_exact: set[str] = set()
        for row_number in range(HEADER_ROW + 1, sheet.max_row + 1):
            name = clean(sheet.cell(row_number, name_col).value)
            if name:
                existing[crawler.normalize(search_base_name(name))].append(row_number)
                existing_exact.add(crawler.normalize(name))
        result_by_base: dict[str, dict[str, Any]] = {}
        for result in results:
            base = search_base_name(clean(result.get("補查基礎食材") or result.get("樣品名稱")))
            if base and result.get("每100g的價格") is not None:
                result_by_base[crawler.normalize(base)] = result
        updated = 0
        for key, rows in existing.items():
            result = result_by_base.get(key)
            if not result:
                continue
            for row_number in rows:
                sheet.cell(row_number, price_col).value = result["每100g的價格"]
                sheet.cell(row_number, price_col).number_format = "#,##0.0000"
                updated += 1
        added = 0
        seen = set(existing_exact)
        for sample in samples:
            if not sample.get("營養樣品名稱空白"):
                continue
            name = clean(sample.get("樣品名稱"))
            key = crawler.normalize(name)
            if not key or key in seen:
                continue
            seen.add(key)
            row_number = sheet.max_row + 1
            if category_col:
                sheet.cell(row_number, category_col).value = sample.get("食品分類") or "待補營養資料"
            sheet.cell(row_number, name_col).value = name
            if alias_col and sample.get("俗名"):
                sheet.cell(row_number, alias_col).value = sample["俗名"]
            result = result_by_base.get(crawler.normalize(search_base_name(name)))
            if result:
                sheet.cell(row_number, price_col).value = result["每100g的價格"]
                sheet.cell(row_number, price_col).number_format = "#,##0.0000"
            added += 1
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix=f"{nutrition_path.stem}_", suffix=".xlsx",
                                             dir=nutrition_path.parent, delete=False) as temp:
                temporary_path = Path(temp.name)
            book.save(temporary_path)
            os.replace(temporary_path, nutrition_path)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()
        return updated, added, backup_path
    finally:
        book.close()


def crawl_and_match(samples: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state = load_checkpoint(args, samples)
    session = crawler.create_session()
    network_failures: list[dict[str, Any]] = list(state.get("network_failures", []))
    products = None
    if state["resumed"] and state["catalog_path"].exists():
        try:
            cached = json.loads(state["catalog_path"].read_text(encoding="utf-8"))
            if cached.get("signature") == state["signature"]:
                products = cached.get("products", [])
                print(f"使用檢查點型錄：{len(products)} 筆商品。", flush=True)
        except (OSError, json.JSONDecodeError):
            products = None
    if products is None:
        # Catalog data is cached only when every category page was read; an
        # incomplete crawl is retried after a later restart with network access.
        before_catalog_failures = len(network_failures)
        products = crawler.crawl_catalog(
            session, args.max_pages, args.timeout, args.delay, network_failures,
        )
        if len(network_failures) == before_catalog_failures:
            write_json_atomic(state["catalog_path"], {"signature": state["signature"], "products": products})
    products = [product for product in products if not NONFOOD_RESULT_RE.search(clean(product.get("商品名稱")))]
    spec_cache: dict[str, tuple[str, dict[str, Any]]] = {}
    groups = ingredient_groups(samples)
    results: dict[int, dict[str, Any]] = dict(state["results"])
    unmatched: list[list[dict[str, Any]]] = []

    # Preserve the source crawler's first pass: match against crawled category
    # listings before falling back to individual site searches.
    for group in groups:
        key = group_key(group)
        if key in state["completed_groups"]:
            print(f"跳過已完成食材群：{search_base_name(group[0]['樣品名稱'])}", flush=True)
            continue
        search_sample = representative(group)
        match = crawler.match_from_pool(
            search_sample, prioritize_rice_product(products, search_sample), args.threshold, session, args.timeout, spec_cache,
        )
        if match:
            complete_missing_weight(match, search_sample, session, args, network_failures)
            match["站內搜尋詞"] = []
            rows = fan_out(match, group)
            for row in rows:
                results[row["Excel列"]] = row
            state["active"] = None
            state["network_failures"] = network_failures
            save_group_checkpoint(state, key, rows, True)
        else:
            unmatched.append(group)

    print(f"型錄未匹配食材群：{len(unmatched)} 組；開始逐組站內搜尋。", flush=True)
    searched = int(state.get("searched_count", 0))
    for group in unmatched:
        key = group_key(group)
        if key in state["completed_groups"]:
            continue
        search_group = [{**sample, "樣品名稱": search_base_name(sample["樣品名稱"])} for sample in group]
        active_state = state.get("active") or {}
        active = active_state if active_state.get("group_key") == key else None

        def save_search_progress(next_index: int, pending_indices: list[int], attempted_terms: list[str],
                                 failed_terms: list[str], request_count: int) -> None:
            state["active"] = {"group_key": key, "next_index": next_index,
                               "pending_indices": pending_indices, "attempted": attempted_terms,
                               "failed_terms": failed_terms}
            state["searched_count"] = request_count
            state["network_failures"] = network_failures
            save_checkpoint_state(state)

        found, searched, attempted, failed_terms = site_search_food_group(
            session, search_group, args, searched, spec_cache, network_failures,
            active, save_search_progress,
        )
        if found:
            complete_missing_weight(found[0], search_group[0], session, args, network_failures)
            for row in found:
                original = next(sample for sample in group if sample["Excel列"] == row["Excel列"])
                row.update({key: original.get(key, "") for key in ("食品分類", "樣品名稱", "俗名")})
                row["補查基礎食材"] = search_base_name(original["樣品名稱"])
                for key in ("重量g", "規格", "每100g的價格", "狀態", "未換算原因", "重量估算說明", "重量補查來源"):
                    if key in found[0]:
                        row[key] = found[0][key]
                results[row["Excel列"]] = row
            state["active"] = None
            state["network_failures"] = network_failures
            save_group_checkpoint(state, group_key(group), found, True)
        else:
            adjective_group = any(search_base_name(sample["樣品名稱"]) != crawler.base_name(sample["樣品名稱"]) for sample in group)
            web_match = None
            if adjective_group:
                web_match = web_price_fallback(
                    session, search_base_name(group[0]["樣品名稱"]), args.timeout, args.delay, network_failures,
                )
            for sample in group:
                row = no_match_row(sample, attempted, failed_terms, network_failures)
                if web_match:
                    row.update(web_match)
                    row["狀態"] = "網路搜尋估價"
                    row["補查基礎食材"] = search_base_name(sample["樣品名稱"])
                    row["未換算原因"] = f"萬家福未找到；依基礎食材網路搜尋估價，請核對：{web_match['補查搜尋詞']}"
                results[row["Excel列"]] = row
            active_after_search = state.get("active") or {}
            term_count = len(crawler.sample_search_terms(representative(search_group)))
            retryable = bool(failed_terms) or (
                active_after_search.get("group_key") == key
                and (active_after_search.get("pending_indices") or active_after_search.get("next_index", 0) < term_count)
            )
            if not retryable:
                state["active"] = None
            state["network_failures"] = network_failures
            state["searched_count"] = searched
            save_group_checkpoint(state, key, [results[sample["Excel列"]] for sample in group], not retryable)

    ordered = [results[sample["Excel列"]] for sample in samples]
    summary = {
        "型錄商品數": len(products),
        "型錄與站內搜尋連線失敗筆數": len(network_failures),
        "連線失敗紀錄": network_failures,
        "站內搜尋請求數": searched,
        "已由檢查點續跑": state["resumed"],
        "檢查點已完成食材群": len(state["completed_groups"]),
        "食材列數": len(samples),
        "不同食材群數": len(groups),
        "成功換算每100g筆數": sum(row.get("每100g的價格") is not None for row in ordered),
        "找到商品但未換算筆數": sum(row.get("狀態") == "找到商品但未換算" for row in ordered),
        "未找到筆數": sum(row.get("狀態") == "未找到" for row in ordered),
        "網路補查重量筆數": sum(bool(row.get("重量補查來源")) for row in ordered),
        "網路搜尋估價筆數": sum(row.get("狀態") == "網路搜尋估價" for row in ordered),
    }
    return ordered, summary


def site_search_food_group(session, group: list[dict[str, Any]], args: argparse.Namespace,
                           searched_count: int, spec_cache: dict[str, Any],
                           network_failures: list[dict[str, Any]], active: dict[str, Any] | None,
                           progress_callback) -> tuple[list[dict[str, Any]] | None, int, list[str], list[str]]:
    terms, seen = [], set()
    for sample in group:
        for term in crawler.sample_search_terms(sample):
            key = crawler.normalize(term)
            if key and key not in seen:
                terms.append(term)
                seen.add(key)
    attempted = list((active or {}).get("attempted", []))
    failed_terms = list((active or {}).get("failed_terms", []))
    representative_sample = representative(group)
    next_index = int((active or {}).get("next_index", 0))
    pending_indices = sorted(set(int(index) for index in (active or {}).get("pending_indices", [])))

    def run_term(index: int) -> dict[str, Any] | None:
        nonlocal searched_count
        term = terms[index]
        if args.search_limit and searched_count >= args.search_limit:
            return None
        if term not in attempted:
            attempted.append(term)
        url = f"{crawler.BASE_URL}/zh/search/?{urlencode({'q': term})}"
        print(f"站內搜尋 {searched_count + 1}{'/' + str(args.search_limit) if args.search_limit else ''}：{representative_sample['樣品名稱']}｜{term}", flush=True)
        searched_count += 1
        try:
            html, final_url = crawler.get_html(session, url, args.timeout)
            products = [product for product in crawler.extract_products(html, final_url)
                        if not NONFOOD_RESULT_RE.search(clean(product.get("商品名稱")))]
            match = crawler.match_from_pool(representative_sample,
                                            prioritize_rice_product(products, representative_sample), args.threshold,
                                            session, args.timeout, spec_cache)
        except Exception as exc:
            if term not in failed_terms:
                failed_terms.append(term)
            network_failures.append({"階段": "站內搜尋", "食材": representative_sample["樣品名稱"],
                                     "搜尋詞": term, "網址": url, "錯誤": str(exc)})
            if index not in pending_indices:
                pending_indices.append(index)
                pending_indices.sort()
            progress_callback(next_index, pending_indices, attempted, failed_terms, searched_count)
            time.sleep(max(0.0, args.delay))
            return None
        if term in failed_terms:
            failed_terms.remove(term)
        if index in pending_indices:
            pending_indices.remove(index)
        return match

    for index in pending_indices.copy():
        if args.search_limit and searched_count >= args.search_limit:
            progress_callback(next_index, pending_indices, attempted, failed_terms, searched_count)
            return None, searched_count, attempted, failed_terms
        match = run_term(index)
        if match:
            rows = []
            for sample in group:
                row = {**match, **{key: sample.get(key, "") for key in ("來源列號", "食品分類", "樣品名稱", "俗名")}}
                row["Excel列"] = sample["Excel列"]
                row["補查基礎食材"] = search_base_name(sample["樣品名稱"])
                row["站內搜尋詞"] = attempted.copy()
                rows.append(row)
            return rows, searched_count, attempted, failed_terms
        progress_callback(next_index, pending_indices, attempted, failed_terms, searched_count)
        time.sleep(max(0.0, args.delay))
    for index in range(next_index, len(terms)):
        if args.search_limit and searched_count >= args.search_limit:
            progress_callback(index, pending_indices, attempted, failed_terms, searched_count)
            return None, searched_count, attempted, failed_terms
        match = run_term(index)
        if match:
            rows = []
            for sample in group:
                row = {**match, **{key: sample.get(key, "") for key in ("來源列號", "食品分類", "樣品名稱", "俗名")}}
                row["Excel列"] = sample["Excel列"]
                row["補查基礎食材"] = search_base_name(sample["樣品名稱"])
                row["站內搜尋詞"] = attempted.copy()
                rows.append(row)
            return rows, searched_count, attempted, failed_terms
        next_index = index + 1
        progress_callback(next_index, pending_indices, attempted, failed_terms, searched_count)
        time.sleep(max(0.0, args.delay))
    return None, searched_count, attempted, failed_terms


def output_column_start(sheet) -> int:
    headers = [clean(sheet.cell(HEADER_ROW, col).value) for col in range(1, max(sheet.max_column, 5) + 1)]
    expected = list(OUTPUT_HEADERS)
    if headers[5:5 + len(expected)] == expected:
        return 6
    if not any(headers[5:]):
        return 6
    # Idempotently update an already appended block even if unrelated columns
    # were added later, otherwise never overwrite user-owned columns.
    for start in range(6, len(headers) - len(expected) + 2):
        if headers[start - 1:start - 1 + len(expected)] == expected:
            return start
    return max(6, sheet.max_column + 1)


def write_results(workbook_path: Path, sheet_name: str,
                  results: list[dict[str, Any]], samples: list[dict[str, Any]]) -> tuple[int, Path]:
    backup_path = workbook_path.with_name(workbook_path.stem + ".before_wanjia_comparison.xlsx")
    if not backup_path.exists():
        shutil.copy2(workbook_path, backup_path)
        print(f"已建立首次更新備份：{backup_path}", flush=True)
    book = load_workbook(workbook_path)
    if sheet_name not in book.sheetnames:
        book.close()
        raise ValueError(f"工作簿找不到工作表「{sheet_name}」")
    sheet = book[sheet_name]
    start_col = output_column_start(sheet)
    header_template = sheet.cell(HEADER_ROW, 5)
    for offset, title in enumerate(OUTPUT_HEADERS):
        cell = sheet.cell(HEADER_ROW, start_col + offset)
        if header_template.has_style:
            cell._style = header_template._style
        cell.value = title
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    results_by_row = {result["Excel列"]: result for result in results}
    sample_rows = {sample["Excel列"] for sample in samples}
    for row_number in sample_rows:
        result = results_by_row[row_number]
        if result.get("每100g的價格") is not None:
            sheet.cell(row_number, 4).value = result["每100g的價格"]
            sheet.cell(row_number, 4).number_format = "#,##0.0000"
        values = (
            result.get("狀態", ""), result.get("商品名稱", ""), result.get("商品網址", ""),
            result.get("網站大類", ""), result.get("商品價格"), result.get("規格", ""),
            result.get("重量g"), result.get("每100g的價格"), result.get("相似度"),
            result.get("命中搜尋詞", ""), "、".join(result.get("站內搜尋詞", [])),
            result.get("未換算原因", ""),
        )
        for offset, value in enumerate(values):
            cell = sheet.cell(row_number, start_col + offset)
            template = sheet.cell(row_number, 5)
            if template.has_style:
                cell._style = template._style
            cell.value = value
            cell.alignment = Alignment(vertical="center", wrap_text=offset in {1, 2, 5, 9, 10, 11})
        sheet.cell(row_number, start_col + 4).number_format = "#,##0.00"
        sheet.cell(row_number, start_col + 6).number_format = "#,##0.00"
        sheet.cell(row_number, start_col + 7).number_format = "0.0000"
        sheet.cell(row_number, start_col + 8).number_format = "0.00%"

    widths = (18, 32, 48, 16, 16, 24, 14, 18, 14, 20, 48, 52)
    for offset, width in enumerate(widths):
        sheet.column_dimensions[get_column_letter(start_col + offset)].width = width

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f"{workbook_path.stem}_", suffix=".xlsx",
            dir=workbook_path.parent, delete=False,
        ) as temp:
            temporary_path = Path(temp.name)
        book.save(temporary_path)
        book.close()
        os.replace(temporary_path, workbook_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
    print(f"已將萬家福比對欄位寫入 {workbook_path}（起始欄：{get_column_letter(start_col)}）", flush=True)
    return start_col, backup_path


def write_reports(output_dir: Path, workbook_path: Path, results: list[dict[str, Any]],
                  summary: dict[str, Any], args: argparse.Namespace) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "wanjia_ytower_comparison.json"
    csv_path = output_dir / "wanjia_ytower_comparison.csv"
    report = {
        "工作簿": str(workbook_path), "工作表": args.sheet,
        "比對門檻": args.threshold,
        "分類網址": crawler.CATEGORY_URLS,
        **summary,
        "結果": [{key: value for key, value in row.items() if key != "Excel列"} for row in results],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "來源列號", "食品分類", "樣品名稱", "補查基礎食材", "俗名", "狀態", "商品名稱", "商品網址",
        "網站大類", "商品價格", "規格", "重量g", "每100g的價格", "相似度",
        "命中搜尋詞", "站內搜尋詞", "重量補查來源", "補查來源", "補查搜尋詞", "未換算原因",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow({**row, "站內搜尋詞": "、".join(row.get("站內搜尋詞", []))})
    return report_path, csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="比對食材營養對照表A欄與萬家福商品，並將結果寫入F欄之後",
    )
    parser.add_argument("--workbook", type=Path, default=default_workbook(),
                        help="ytower_nutrition_comparison.xlsx 路徑")
    parser.add_argument("--nutrition-workbook", type=Path, default=default_nutrition_workbook(),
                        help="food_nutrition_2025.xlsx 原始營養資料路徑")
    parser.add_argument("--nutrition-sheet", default=os.environ.get("FOOD_NUTRITION_SHEET", NUTRITION_SHEET))
    parser.add_argument("--sheet", default=os.environ.get("YTOWER_COMPARISON_SHEET", DEFAULT_SHEET))
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(os.environ.get(
            "WANJIA_COMPARISON_OUTPUT_DIR",
            str(Path(__file__).resolve().parents[2] / "data" / "processed" / "wanjia_ytower_comparison"),
        )),
    )
    parser.add_argument("--threshold", type=float, default=float(os.environ.get("WANJIA_THRESHOLD", "0.60")))
    parser.add_argument("--delay", type=float, default=float(os.environ.get("WANJIA_DELAY", "1.0")))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("WANJIA_TIMEOUT", "25")))
    parser.add_argument("--max-pages", type=int, default=int(os.environ.get("WANJIA_MAX_PAGES", "0")))
    parser.add_argument("--search-limit", type=int, default=int(os.environ.get("WANJIA_SEARCH_LIMIT", "0")))
    parser.add_argument("--checkpoint", type=Path, help="續跑檢查點 JSON 路徑（預設存於 --output-dir）")
    parser.add_argument("--restart", action="store_true", help="忽略並清除上一輪檢查點，從頭搜尋")
    parser.add_argument("--no-update-workbook", action="store_true", help="只輸出 JSON/CSV 報告，不修改活頁簿")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 <= args.threshold <= 1:
        raise SystemExit("--threshold 必須介於 0 到 1")
    if args.timeout <= 0 or args.delay < 0 or args.max_pages < 0 or args.search_limit < 0:
        raise SystemExit("timeout 必須大於0，delay/max-pages/search-limit 不可小於0")
    if not args.workbook.is_file():
        raise SystemExit(f"找不到食材對照工作簿：{args.workbook}")
    if not args.no_update_workbook and not args.nutrition_workbook.is_file():
        raise SystemExit(f"找不到原始營養資料工作簿：{args.nutrition_workbook}")
    samples = read_ingredients(args.workbook, args.sheet)
    if not samples:
        raise SystemExit("A欄沒有找到可比對的食材資料")
    print(f"讀取A欄食材：{len(samples)} 列、{len(ingredient_groups(samples))} 組；工作簿：{args.workbook}", flush=True)

    results, summary = crawl_and_match(samples, args)
    if not args.no_update_workbook:
        nutrition_updated, nutrition_added, nutrition_backup = write_back_nutrition_workbook(
            args.nutrition_workbook, samples, results, args.nutrition_sheet,
        )
        summary["原始營養資料更新價格列數"] = nutrition_updated
        summary["原始營養資料新增食材列數"] = nutrition_added
        summary["原始營養資料首次更新備份"] = str(nutrition_backup)
        start_col, backup_path = write_results(args.workbook, args.sheet, results, samples)
        summary["回填欄位起始欄"] = get_column_letter(start_col)
        summary["首次更新備份"] = str(backup_path)
    else:
        summary["回填欄位起始欄"] = "未寫入"

    report_path, csv_path = write_reports(args.output_dir, args.workbook, results, summary, args)
    print(f"JSON報告：{report_path}", flush=True)
    print(f"CSV報告：{csv_path}", flush=True)
    print(
        f"每100g成功換算：{summary['成功換算每100g筆數']}；"
        f"商品未換算：{summary['找到商品但未換算筆數']}；未找到：{summary['未找到筆數']}；"
        f"營養資料更新價格：{summary.get('原始營養資料更新價格列數', 0)}；"
        f"新增食材：{summary.get('原始營養資料新增食材列數', 0)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
