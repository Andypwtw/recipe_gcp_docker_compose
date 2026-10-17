"""
今天吃啥咪！— 前端網頁（Streamlit + 直連 MySQL 版）

【這一版異動重點（2026-09-22）】
    1. 側邊欄「用一句話找分類」改為「食材剩餘找料理」：
       使用者輸入目前剩餘的食材（可用逗號、頓號分隔多個，或直接用口語描述），
       系統會直接查 MySQL 的 recipe_ingredients / ingredients，用「精準比對」
       （ingredients.canonical_name 完全相等，不是模糊 LIKE）找出「同時含有全部
       輸入食材」的食譜（AND 邏輯）。例如輸入「牛肉、高麗菜」，只有同時用到這兩種
       食材的食譜才會列入結果。
    2. 食材明細（含公克數 weight_g）與每項食材的價格，全部改回「直接查 MySQL」，
       不再呼叫外部 API：
         - 公克數：recipe_ingredients.weight_g
         - 食材價格：nutrition_source.price_per_100g
           （透過 ingredient_nutrition_map 對應到 recipe_ingredients.ingredient_id）
         - 每項食材估算價格 = weight_g / 100 * price_per_100g（無條件進位到整數元，
           呼應 ETL：09_calculate_recipe_nutrition.py 的定價政策）
         - 整道食譜的總熱量 / 總估算價格，沿用 recipe_nutrition_summary 裡已經算好
           的 energy_kcal / estimated_price，網頁端不重算。
    3. 篩選條件（course / cuisine / diet / ingredient_group / theme）畫面上的標題
       改成中文顯示（category_type 欄位本身仍是英文代碼，只有顯示文字轉換）。
    4. 料理圖片改用固定尺寸縮圖顯示（image_url 直接連結圖片，套用
       object-fit: cover 讓卡片版面整齊、不會因為原圖尺寸不一而跑版）；沒有圖片
       時顯示一個佔位色塊，避免版面空一塊。
    5.（2026-09-22 追加）「食材剩餘找料理」的輸入框改用 AI 解析：使用者可以直接用
       口語描述剩餘食材（例如「剩下一顆番茄及牛肉」），系統會呼叫 Google Gemini
       把這段話拆成乾淨的食材名稱清單（例如 ["番茄","牛肉"]），再拿這份清單去比對
       食譜，不需要使用者自己手動用逗號分隔。若沒有設定 GOOGLE_API_KEY，或呼叫 AI
       失敗，會自動退回一套規則式的中文斷詞（去除「剩下/還有/約」等語氣詞、
       「一顆/兩條/半斤」等數量單位，並用「及/和/跟/與/以及/還有」等連接詞斷句）。
    6.（2026-10-01 追加）
       a. 修正「料理詳情」彈窗偶爾在使用者操作側邊欄（搜尋／食材剩餘找料理／
          顯示全部料理）時又跳出來的問題：這些操作現在會在同一次重新執行裡
          先清除彈窗狀態，不會被殘留的彈窗狀態蓋過去。
       b. 「主廚今日精選推薦」自動輪播改為固定每 5 秒換下一張（原本是隨機
          5~10 秒），換哪一張仍然是隨機挑選。
       c. 分類篩選改為「精準比對」：同一個分類群組（例如都是「料理風格」）內
          勾選多個＝符合任一即可；但不同分類群組之間（例如「料理風格」＋
          「飲食類型」）必須同時符合，不會像以前一樣用「或」邏輯把其他不相關
          的料理也撈進來。
       d. 「食材剩餘找料理」的 AI 解析結果，現在會比對使用者原始輸入文字，
          過濾掉 AI 自己腦補、使用者沒打出來的衍生食材名稱（例如輸入
          「玉米和牛肉」只會留下「玉米」「牛肉」，不會被 AI 誤判成「玉米粉」）。
       e. 側邊欄「加入 LINE 好友」的連結與 QR Code 改成真實官方帳號連結。
    7.（2026-10-06 追加）側邊欄新增「🎲 幫我推一道料理」按鈕：點一下就從資料庫
       隨機抽一道料理，直接跳出跟點卡片「查看完整食譜內容」一樣的詳情彈窗，
       不用先在列表裡找。每次按下去都重新隨機抽，不會卡住抽到同一道。

【關於「食材重量換算」與「熱量／價格計算」】
    - recipe_ingredients.weight_g / is_estimated / needs_manual_review 等食材明細，
      本版直接查 MySQL 取得（不再透過外部 API）。
    - recipe_nutrition_summary.energy_kcal      → 整道食譜估算總熱量（MySQL 查詢，唯一保留用途）
    - recipe_nutrition_summary.estimated_price  → 整道食譜估算總價格（MySQL 查詢）
    - recipe_nutrition_summary.coverage_percent → 熱量資料涵蓋率（僅供內部判斷 badge 顏色用，畫面不顯示數字）
    本檔案「直接讀取」這些已算好的欄位並整理呈現，不在網頁端重新計算密度換算、
    熱量加總或食材定價 —— 避免跟原本的 ETL 邏輯打架、產生兩套不一致的數字。
    如果你要調整換算/熱量/價格的計算方式，應該去改 ETL 腳本，而不是這裡。

【分類篩選邏輯】
    使用者可勾選多個分類。「同一個分類群組」（例如「料理風格」裡勾了兩種風格）
    之間用「或」邏輯；但「不同分類群組」之間（例如「料理風格」＋「飲食類型」）
    必須「同時符合」（且邏輯）才會顯示 —— 這樣勾「其他料理」＋「素食」只會看到
    同時符合這兩項的食譜，不會把只符合其中一項的料理也撈進來。
    （SQL 作法：對每個有勾選的分類群組各建一個 EXISTS 子查詢，群組內用
    recipe_categories.category_id IN (...)，群組之間用 AND 串接。）

【連線設定】
    透過環境變數設定連線資訊，「不要」把密碼寫死在程式碼裡：
        DB_HOST      預設 127.0.0.1
        DB_PORT      預設 3317   （對應 docker-compose 對外映射的 port）
        DB_USER      預設 recipe_app
        DB_PASSWORD  無預設值，必須自行提供（例如用 .env 或系統環境變數）
        DB_NAME      預設 recipe_ai

    建議在專案根目錄建立 .env（不要提交進版控），內容例如：
        DB_HOST=127.0.0.1
        DB_PORT=3317
        DB_USER=recipe_app
        DB_PASSWORD=你的密碼
        DB_NAME=recipe_ai

    「食材剩餘找料理」若要用 AI 解析口語輸入（例如「剩下一顆番茄及牛肉」），
    還需要設定：
        GOOGLE_API_KEY=你的 Google AI (Gemini) API 金鑰
        GEMINI_MODEL=gemini-2.0-flash   （可省略，預設就是這個）
    金鑰可以到 https://aistudio.google.com/apikey 建立。
    沒有設定的話，會自動退回規則式中文斷詞，不會讓程式壞掉，只是解析得比較陽春。

執行方式：
    uv run streamlit run 網頁new.py
"""

import json
import math
import os
import random
import re
import time
from collections import defaultdict

import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 沒裝 python-dotenv 也沒關係，改用系統環境變數即可

try:
    import google.generativeai as genai
except ImportError:
    genai = None  # 沒裝 google-generativeai 套件時，自動退回規則式中文斷詞

try:
    # 「主廚今日精選推薦」自動輪播用：週期性觸發 Streamlit 重新執行，
    # 才能在沒有使用者互動的情況下也自動換下一張。
    # 沒裝這個套件時會自動退回「只能手動點 ◀▶ 切換」，不影響其他功能。
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None


# -------------------------------------------------------------
# 0. 頁面基本配置 + 全域樣式（UI/UX 改版重點區）
# -------------------------------------------------------------
st.set_page_config(page_title="今天吃啥咪！", layout="wide", page_icon="🍳")

PAGE_SIZE = 12
CAROUSEL_SIZE = 5
CAROUSEL_INTERVAL_SECONDS = 5  # 自動輪播固定每 5 秒換下一張（換哪一張仍隨機挑選）

# 分類 category_type（英文代碼，儲存於 MySQL）→ 畫面顯示用中文標題
CATEGORY_TYPE_LABELS = {
    "course": "餐點類型",
    "cuisine": "料理風格",
    "diet": "飲食類型",
    "ingredient_group": "主要食材",
    "theme": "特色主題",
}

CUSTOM_CSS = """
<style>
    /* 整體字體與背景微調 */
    .main .block-container {
        padding-top: 1.5rem;
        max-width: 1200px;
    }

    /* 標題區塊 */
    .app-hero {
        background: linear-gradient(135deg, #FF7A45 0%, #FFB88C 100%);
        border-radius: 18px;
        padding: 28px 32px;
        margin-bottom: 24px;
        color: white;
        box-shadow: 0 8px 24px rgba(255, 122, 69, 0.25);
    }
    .app-hero h1 {
        margin: 0;
        font-size: 2.1rem;
    }
    .app-hero p {
        margin: 6px 0 0 0;
        opacity: 0.95;
        font-size: 0.95rem;
    }

    /* 食譜卡片 */
    .recipe-card {
        background: #ffffff;
        border: 1px solid #F0F0F0;
        border-radius: 14px;
        padding: 18px 20px;
        margin-bottom: 14px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        transition: box-shadow 0.15s ease;
    }
    .recipe-card:hover {
        box-shadow: 0 6px 18px rgba(0,0,0,0.09);
    }
    .recipe-card h4 {
        margin: 0 0 8px 0;
        font-size: 1.08rem;
        color: #2B2B2B;
    }

    /* 分類標籤 */
    .cat-badge {
        display: inline-block;
        background: #FFF1E8;
        color: #D9531E;
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 0.75rem;
        margin: 2px 4px 2px 0;
        border: 1px solid #FFDCC5;
    }

    /* 熱量狀態徽章 */
    .kcal-badge-ok {
        display: inline-block;
        background: #E8F8EE;
        color: #1E8E4C;
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 0.75rem;
        border: 1px solid #C6ECD4;
        margin: 2px 4px 2px 0;
    }
    .kcal-badge-warn {
        display: inline-block;
        background: #FFF7E0;
        color: #B8860B;
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 0.75rem;
        border: 1px solid #F5E3A8;
        margin: 2px 4px 2px 0;
    }
    .kcal-badge-bad {
        display: inline-block;
        background: #FDECEC;
        color: #C0392B;
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 0.75rem;
        border: 1px solid #F5C6C2;
        margin: 2px 4px 2px 0;
    }

    /* 公克數徽章 */
    .weight-badge {
        display: inline-block;
        background: #EAF2FF;
        color: #2A5ADB;
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 0.75rem;
        border: 1px solid #CBDDFC;
        margin: 2px 4px 2px 0;
    }

    /* 價格徽章 */
    .price-badge {
        display: inline-block;
        background: #FFF1F6;
        color: #C2185B;
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 0.75rem;
        border: 1px solid #FAD1E0;
        margin: 2px 4px 2px 0;
    }

    .estimated-tag {
        color: #9B9B9B;
        font-size: 0.72rem;
        font-style: italic;
    }

    .review-tag {
        color: #C0392B;
        font-size: 0.72rem;
    }

    /* 分頁資訊文字置中 */
    .page-indicator {
        text-align: center;
        color: #666;
        font-size: 0.9rem;
        padding-top: 6px;
    }

    hr {
        margin: 1.2rem 0;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# -------------------------------------------------------------
# 1. MySQL 連線層
# -------------------------------------------------------------
@st.cache_resource
def get_engine() -> Engine:
    user = os.getenv("DB_USER", "recipe_app")
    password = os.getenv("DB_PASSWORD")
    db_name = os.getenv("DB_NAME", "recipe_ai")
    instance_connection_name = os.getenv("INSTANCE_CONNECTION_NAME")

    if not password:
        st.error(
            "找不到資料庫密碼：請設定環境變數 DB_PASSWORD"
            "（建議放在 .env 檔案裡，不要寫死在程式碼中）。"
        )
        st.stop()

    if instance_connection_name:
        # 在 Cloud Run 上執行時：透過 --add-cloudsql-instances 掛進來的
        # unix socket 連線，不需要對外開放的 IP、也不用處理 SSL 憑證。
        url = (
            f"mysql+pymysql://{user}:{password}@/{db_name}"
            f"?unix_socket=/cloudsql/{instance_connection_name}&charset=utf8mb4"
        )
    else:
        host = os.getenv("DB_HOST", "127.0.0.1")
        port = os.getenv("DB_PORT", "3317")
        url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}?charset=utf8mb4"

    return create_engine(url, pool_pre_ping=True, pool_recycle=1800)


def run_query(sql: str, params: dict | None = None) -> list[dict]:
    """執行 SELECT 並回傳 list[dict]。任何連線/SQL錯誤都攔截並顯示友善訊息。"""
    try:
        with get_engine().connect() as conn:
            result = conn.execute(text(sql), params or {})
            cols = result.keys()
            return [dict(zip(cols, row)) for row in result.fetchall()]
    except Exception as exc:  # noqa: BLE001 - 對使用者顯示，細節留在 log
        st.error(f"資料庫查詢失敗：{exc}")
        return []


def run_scalar(sql: str, params: dict | None = None):
    rows = run_query(sql, params)
    if not rows:
        return None
    return list(rows[0].values())[0]


# -------------------------------------------------------------
# 2. 資料存取函式
# -------------------------------------------------------------
@st.cache_data(ttl=300)
def fetch_categories() -> list[dict]:
    return run_query(
        """
        SELECT id AS category_id, category_name, category_type
        FROM categories
        WHERE is_active = 1
        ORDER BY category_type, category_name
        """
    )


def _attach_categories(recipes: list[dict]) -> list[dict]:
    """幫一批食譜補上 categories 欄位（list of {category_id, category_name}）。"""
    if not recipes:
        return recipes
    recipe_ids = [r["id"] for r in recipes]
    placeholders = ", ".join(f":id{i}" for i in range(len(recipe_ids)))
    params = {f"id{i}": rid for i, rid in enumerate(recipe_ids)}
    rows = run_query(
        f"""
        SELECT rc.recipe_id, c.id AS category_id, c.category_name, c.category_type
        FROM recipe_categories rc
        JOIN categories c ON c.id = rc.category_id
        WHERE rc.recipe_id IN ({placeholders})
        """,
        params,
    )
    by_recipe: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_recipe[row["recipe_id"]].append(
            {"category_id": row["category_id"], "category_name": row["category_name"],
             "category_type": row["category_type"]}
        )
    for r in recipes:
        r["categories"] = by_recipe.get(r["id"], [])
    return recipes


def _attach_nutrition(recipes: list[dict]) -> list[dict]:
    """熱量讀 recipe_nutrition_summary；價格改為即時加總，與 LINE API 同規則：
    單項無條件進位後加總，缺價格不計入；全部缺則顯示尚無資料。"""
    if not recipes:
        return recipes
    recipe_ids = [r["id"] for r in recipes]
    placeholders = ", ".join(f":id{i}" for i in range(len(recipe_ids)))
    params = {f"id{i}": rid for i, rid in enumerate(recipe_ids)}

    rows = run_query(
        f"""
        SELECT recipe_id, energy_kcal, coverage_percent, calorie_status, price_status
        FROM recipe_nutrition_summary
        WHERE recipe_id IN ({placeholders})
        """,
        params,
    )
    price_rows = run_query(
        f"""
        SELECT ri.recipe_id,
               SUM(CEIL(ri.weight_g / 100 * ns.price_per_100g)) AS item_price_total
        FROM recipe_ingredients ri
        LEFT JOIN ingredient_nutrition_map inm
          ON inm.ingredient_id = ri.ingredient_id AND inm.status = 'APPROVED'
        LEFT JOIN nutrition_source ns ON ns.id = inm.nutrition_source_id
        WHERE ri.recipe_id IN ({placeholders})
        GROUP BY ri.recipe_id
        """,
        params,
    )
    by_recipe = {row["recipe_id"]: row for row in rows}
    price_by_recipe = {row["recipe_id"]: row["item_price_total"] for row in price_rows}

    for r in recipes:
        n = by_recipe.get(r["id"])
        r["energy_kcal"] = float(n["energy_kcal"]) if n and n["energy_kcal"] is not None else None
        r["calorie_coverage_percent"] = float(n["coverage_percent"]) if n and n["coverage_percent"] is not None else None
        r["calorie_status"] = n["calorie_status"] if n else None
        p = price_by_recipe.get(r["id"])
        r["estimated_price"] = float(p) if p is not None else None
        r["price_status"] = n["price_status"] if n else None
    return recipes


def _attach_total_weight(recipes: list[dict]) -> list[dict]:
    """幫一批食譜補上 total_weight_g（直接對 recipe_ingredients 加總 weight_g）。"""
    if not recipes:
        return recipes
    recipe_ids = [r["id"] for r in recipes]
    placeholders = ", ".join(f":id{i}" for i in range(len(recipe_ids)))
    params = {f"id{i}": rid for i, rid in enumerate(recipe_ids)}
    rows = run_query(
        f"""
        SELECT recipe_id,
               SUM(weight_g) AS total_weight_g,
               COUNT(*) AS total_lines,
               SUM(weight_g IS NOT NULL) AS known_weight_lines
        FROM recipe_ingredients
        WHERE recipe_id IN ({placeholders})
        GROUP BY recipe_id
        """,
        params,
    )
    by_recipe = {row["recipe_id"]: row for row in rows}
    for r in recipes:
        n = by_recipe.get(r["id"])
        r["total_weight_g"] = float(n["total_weight_g"]) if n and n["total_weight_g"] is not None else None
    return recipes


def _enrich(rows: list[dict]) -> list[dict]:
    rows = _attach_categories(rows)
    rows = _attach_nutrition(rows)
    # 2026-09-30：畫面已不顯示總重量，不再查詢 total_weight_g。
    return rows


def _paginate_clause(page: int, limit: int) -> str:
    offset = max(page - 1, 0) * limit
    return f"LIMIT {int(limit)} OFFSET {int(offset)}"


@st.cache_data(ttl=60)
def fetch_recipes_page(page: int = 1, limit: int = PAGE_SIZE) -> dict:
    total = run_scalar("SELECT COUNT(*) FROM recipes") or 0
    rows = run_query(
        f"""
        SELECT id, seq, name, raw_keywords, source_url, image_url
        FROM recipes
        ORDER BY id DESC
        {_paginate_clause(page, limit)}
        """
    )
    rows = _enrich(rows)
    total_pages = (total + limit - 1) // limit if total else 0
    return {"items": rows, "page": page, "limit": limit, "total": total, "total_pages": total_pages}


@st.cache_data(ttl=60)
def fetch_random_recipes(limit: int = CAROUSEL_SIZE) -> list[dict]:
    rows = run_query(
        """
        SELECT id, seq, name, raw_keywords, source_url, image_url
        FROM recipes
        ORDER BY RAND()
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return _enrich(rows)


def fetch_one_random_recipe() -> dict | None:
    """隨機抽一道料理的 id／seq，給「幫我推一道料理」按鈕用。

    2026-10-06 新增：故意「不」加 @st.cache_data——這個功能的重點就是每次
    按下去都要重新隨機抽一道，如果快取住就會變成每次按都抽到同一道，
    跟使用者要的「推薦」體驗不符。"""
    rows = run_query(
        """
        SELECT id, seq
        FROM recipes
        ORDER BY RAND()
        LIMIT 1
        """
    )
    return rows[0] if rows else None


def _category_groups_where(
    categories_by_type: dict, params: dict, table_alias: str = "r"
) -> list[str]:
    """把「分類群組 → 勾選的分類ID清單」轉成一組 EXISTS 子句：
    同一群組內的分類ID用 IN(...)（或邏輯），不同群組之間彼此是 AND（且邏輯），
    這樣才會是「精準比對」——例如同時勾了「料理風格：其他料理」與
    「飲食類型：素食」，只有同時符合這兩個群組的食譜才會列入。
    直接把產生的 EXISTS 子句字串 list 回傳，呼叫端自行用 AND 接起來；
    對應的 bind 參數會直接寫進傳入的 params dict。"""
    clauses = []
    group_index = 0
    for ids in categories_by_type.values():
        ids = [cid for cid in ids if cid is not None]
        if not ids:
            continue
        placeholders = ", ".join(f":cg{group_index}_{i}" for i in range(len(ids)))
        for i, cid in enumerate(ids):
            params[f"cg{group_index}_{i}"] = cid
        clauses.append(
            f"EXISTS (SELECT 1 FROM recipe_categories rcg{group_index} "
            f"WHERE rcg{group_index}.recipe_id = {table_alias}.id "
            f"AND rcg{group_index}.category_id IN ({placeholders}))"
        )
        group_index += 1
    return clauses


@st.cache_data(ttl=60)
def search_recipes(q: str, categories_by_type: dict, page: int = 1, limit: int = PAGE_SIZE) -> dict:
    """關鍵字（比對 name / raw_keywords）+ 分類「精準比對」篩選：
    同一分類群組內用「或」，不同分類群組之間用「且」。"""
    where_clauses = ["(r.name LIKE :kw OR r.raw_keywords LIKE :kw)"]
    params = {"kw": f"%{q}%"}

    where_clauses.extend(_category_groups_where(categories_by_type, params))
    where_sql = " AND ".join(where_clauses)

    total = run_scalar(
        f"""
        SELECT COUNT(*)
        FROM recipes r
        WHERE {where_sql}
        """,
        params,
    ) or 0

    rows = run_query(
        f"""
        SELECT r.id, r.seq, r.name, r.raw_keywords, r.source_url, r.image_url
        FROM recipes r
        WHERE {where_sql}
        ORDER BY r.id DESC
        {_paginate_clause(page, limit)}
        """,
        params,
    )
    rows = _enrich(rows)
    total_pages = (total + limit - 1) // limit if total else 0
    return {"items": rows, "page": page, "limit": limit, "total": total, "total_pages": total_pages}


@st.cache_data(ttl=60)
def recommend_recipes_by_filters(categories_by_type: dict, page: int = 1, limit: int = PAGE_SIZE) -> dict:
    """只勾選分類、沒有關鍵字時使用。精準比對：同一分類群組（例如「料理風格」）
    內勾選多個＝符合任一即可；不同分類群組之間（例如「料理風格」＋「飲食類型」）
    必須同時符合才會列入（且邏輯），不會把只符合其中一個群組的料理也撈進來。"""
    params: dict = {}
    where_clauses = _category_groups_where(categories_by_type, params)
    if not where_clauses:
        return fetch_recipes_page(page=page, limit=limit)

    where_sql = " AND ".join(where_clauses)

    total = run_scalar(
        f"""
        SELECT COUNT(*)
        FROM recipes r
        WHERE {where_sql}
        """,
        params,
    ) or 0

    rows = run_query(
        f"""
        SELECT r.id, r.seq, r.name, r.raw_keywords, r.source_url, r.image_url
        FROM recipes r
        WHERE {where_sql}
        ORDER BY r.id DESC
        {_paginate_clause(page, limit)}
        """,
        params,
    )
    rows = _enrich(rows)
    total_pages = (total + limit - 1) // limit if total else 0
    return {"items": rows, "page": page, "limit": limit, "total": total, "total_pages": total_pages}


@st.cache_data(ttl=60)
def search_recipes_by_ingredients(ingredient_list: tuple, page: int = 1, limit: int = PAGE_SIZE) -> dict:
    """食材剩餘找料理（精準比對）：輸入的每一項食材，都用「完全等於」去比對
    ingredients.canonical_name（正規化食材名稱），不是模糊的 LIKE 比對；
    而且食譜必須「同時含有輸入的全部食材」才會列入候選（AND 邏輯，不是任一符合即可）。

    例如輸入「牛肉、高麗菜」，只有食材裡同時有「牛肉」跟「高麗菜」的食譜才會出現，
    只用到其中一種的食譜不會列入。
    """
    unique_ingredients = list(dict.fromkeys(x.strip() for x in ingredient_list if x.strip()))
    if not unique_ingredients:
        return {"items": [], "page": page, "limit": limit, "total": 0, "total_pages": 0}

    placeholders = ", ".join(f":ing{i}" for i in range(len(unique_ingredients)))
    params = {f"ing{i}": kw for i, kw in enumerate(unique_ingredients)}
    params["required_count"] = len(unique_ingredients)

    total = run_scalar(
        f"""
        SELECT COUNT(*) FROM (
            SELECT r.id
            FROM recipes r
            JOIN recipe_ingredients ri ON ri.recipe_id = r.id
            JOIN ingredients i ON i.id = ri.ingredient_id
            WHERE i.canonical_name IN ({placeholders})
            GROUP BY r.id
            HAVING COUNT(DISTINCT i.canonical_name) = :required_count
        ) t
        """,
        params,
    ) or 0

    rows = run_query(
        f"""
        SELECT
            r.id, r.seq, r.name, r.raw_keywords, r.source_url, r.image_url,
            COUNT(DISTINCT i.canonical_name) AS matched_ingredient_count
        FROM recipes r
        JOIN recipe_ingredients ri ON ri.recipe_id = r.id
        JOIN ingredients i ON i.id = ri.ingredient_id
        WHERE i.canonical_name IN ({placeholders})
        GROUP BY r.id
        HAVING COUNT(DISTINCT i.canonical_name) = :required_count
        ORDER BY r.id DESC
        {_paginate_clause(page, limit)}
        """,
        params,
    )
    rows = _enrich(rows)
    for row in rows:
        row["total_ingredient_count"] = len(unique_ingredients)
    total_pages = (total + limit - 1) // limit if total else 0
    return {"items": rows, "page": page, "limit": limit, "total": total, "total_pages": total_pages}


def _ceil_price(weight_g, price_per_100g) -> int | None:
    if weight_g is None or price_per_100g is None:
        return None
    return math.ceil(float(weight_g) / 100.0 * float(price_per_100g))


@st.cache_data(ttl=300)
def fetch_recipe_ingredients_from_db(recipe_id: int) -> list[dict]:
    """直接從 MySQL 撈取單一食譜的食材明細：
        - 公克數：recipe_ingredients.weight_g
        - 食材價格：nutrition_source.price_per_100g
          （透過 ingredient_nutrition_map 對應到 ingredient_id，取已核准的對應）
    不再透過外部 API。"""
    rows = run_query(
        """
        SELECT
            ri.line_no, ri.raw_text, ri.raw_name, ri.quantity_value,
            ri.weight_g, ri.is_estimated, ri.needs_manual_review, ri.review_reason,
            i.canonical_name,
            u.canonical_unit,
            ns.price_per_100g
        FROM recipe_ingredients ri
        JOIN ingredients i ON i.id = ri.ingredient_id
        LEFT JOIN units u ON u.id = ri.unit_id
        LEFT JOIN ingredient_nutrition_map inm
          ON inm.ingredient_id = ri.ingredient_id AND inm.status = 'APPROVED'
        LEFT JOIN nutrition_source ns ON ns.id = inm.nutrition_source_id
        WHERE ri.recipe_id = :recipe_id
        ORDER BY ri.line_no
        """,
        {"recipe_id": recipe_id},
    )
    for row in rows:
        row["estimated_price"] = _ceil_price(row.get("weight_g"), row.get("price_per_100g"))
    return rows


@st.cache_data(ttl=60)
def fetch_recipe_detail(recipe_id: int, seq: str) -> dict | None:
    """取得單一食譜完整資料：基本資訊 + 分類 + 熱量/價格摘要 + 圖片 + 食材明細
    （含公克數與每項食材價格），全部直接查 MySQL。"""
    base_rows = run_query(
        "SELECT id, seq, name, raw_keywords, steps, source_url, image_url, published_date FROM recipes WHERE id = :id",
        {"id": recipe_id},
    )
    if not base_rows:
        return None
    detail = base_rows[0]

    detail = _attach_categories([detail])[0]
    detail = _attach_nutrition([detail])[0]  # 總卡路里 + 總估算價格

    detail["ingredients"] = fetch_recipe_ingredients_from_db(recipe_id)
    return detail


# -------------------------------------------------------------
# 2b. 「食材剩餘找料理」的口語輸入解析（AI 優先，規則式斷詞為備援）
# -------------------------------------------------------------
_CN_NUMBER = r"(?:\d+(?:\.\d+)?|[一二兩三四五六七八九十百半]+)"
_QUANTITY_UNIT = (
    r"顆|個|條|片|塊|把|根|包|罐|瓶|盒|支|粒|份|斤|兩|公克|克|公斤|杯|碗|匙|大匙|小匙|g|kg"
)
_LEADING_FILLER_RE = re.compile(
    r"^(?:剩下|剩|還有|大約|大概|約|差不多|只剩|一些|些許|些|少許|一點點|少量)+"
)
_QUANTITY_PREFIX_RE = re.compile(
    rf"^(?:{_CN_NUMBER}\s*(?:{_QUANTITY_UNIT})?)+"
)
_INGREDIENT_SPLIT_RE = re.compile(r"以及|及|和|跟|與|還有|[,，、;；/\s]+")


def fallback_parse_ingredients(free_text: str) -> list[str]:
    """規則式中文斷詞（AI 解析不可用時的備援）：
    先用連接詞/標點把整句切開，再從每個片段開頭去除「剩下/還有/約」等語氣詞
    與「一顆/兩條/半斤」等數量單位，留下乾淨的食材名稱。"""
    text_value = (free_text or "").strip()
    if not text_value:
        return []

    parts = [p.strip() for p in _INGREDIENT_SPLIT_RE.split(text_value) if p.strip()]
    cleaned: list[str] = []
    for part in parts:
        part = _LEADING_FILLER_RE.sub("", part).strip()
        part = _QUANTITY_PREFIX_RE.sub("", part).strip()
        part = _LEADING_FILLER_RE.sub("", part).strip()
        if part and part not in cleaned:
            cleaned.append(part)
    return cleaned


@st.cache_data(ttl=600, show_spinner=False)
def ai_parse_ingredients(free_text: str) -> list[str] | None:
    """呼叫 Google Gemini 把口語描述的剩餘食材，解析成乾淨的食材名稱清單。
    沒有設定 GOOGLE_API_KEY、沒裝 google-generativeai 套件，或呼叫/解析失敗時
    回傳 None，交由呼叫端退回 fallback_parse_ingredients。

    精準比對保護：解析完後，會把每一項結果拿去跟使用者「原始輸入文字」核對，
    任何不是原始輸入裡逐字出現過的詞都會被丟掉——避免 AI 自己腦補、把使用者
    輸入的「玉米」改寫成「玉米粉」之類的其他衍生食材，造成搜尋結果跑掉。"""
    api_key = os.getenv("GOOGLE_API_KEY")
    text_value = (free_text or "").strip()
    if not genai or not api_key or not text_value:
        return None

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name,
            system_instruction=(
                "你是食材名稱擷取助手。使用者會用口語描述目前剩餘的食材，"
                "請擷取出乾淨的食材名稱清單：去除數量、單位、語氣詞與連接詞"
                "（例如「一顆」「兩條」「大概」「剩下」「及」「和」「跟」），"
                "只保留食材本身的名稱（例如「番茄」「牛肉」）。"
                "絕對不要把食材名稱改寫、替換、正規化或「補完」成其他相近但不同的"
                "食材或加工品——例如使用者說「玉米」就只能輸出「玉米」，不可以自己"
                "改成「玉米粉」「玉米醬」「玉米粒罐頭」等使用者沒有打出來的詞；"
                "每一項輸出都必須是使用者原始輸入裡逐字出現過的詞，不可臆測或擴寫。"
                "只能輸出一個 JSON 陣列，例如 [\"番茄\",\"牛肉\"]，"
                "不要輸出任何其他文字或說明。"
            ),
        )
        response = model.generate_content(
            text_value,
            generation_config={
                "temperature": 0,
                "max_output_tokens": 200,
                "response_mime_type": "application/json",
            },
        )
        raw_text = (response.text or "").strip()
        match = re.search(r"\[.*\]", raw_text, flags=re.DOTALL)
        if not match:
            return None
        parsed = json.loads(match.group(0))
        cleaned = [str(x).strip() for x in parsed if str(x).strip()]
        # 精準比對保護：只保留「使用者原始輸入文字裡真的有出現」的詞，
        # 過濾掉 AI 自己腦補／改寫出來的衍生食材名稱（例如 玉米 → 玉米粉）。
        cleaned = [term for term in cleaned if term in text_value]
        return cleaned or None
    except Exception:
        # 呼叫 AI 失敗（沒網路、金鑰錯誤、回傳格式異常等）一律安靜退回規則式斷詞，
        # 不讓「食材剩餘找料理」這個功能因為 AI 暫時不可用而整個壞掉。
        return None


def parse_leftover_ingredients(free_text: str) -> tuple[list[str], bool]:
    """回傳 (食材清單, 是否為 AI 解析)。"""
    ai_result = ai_parse_ingredients(free_text)
    if ai_result:
        return ai_result, True
    return fallback_parse_ingredients(free_text), False


# -------------------------------------------------------------
# 3. 側邊欄：快速搜尋 + 幫我推一道料理 + 食材剩餘找料理 + LINE 好友資訊
# -------------------------------------------------------------
def _surprise_me_callback():
    """「🎲 幫我推一道料理」按鈕的 on_click 回呼（2026-10-06 新增）：
    直接從資料庫隨機抽一道料理，馬上開啟它的「料理詳情」彈窗——不用先在
    列表裡找，點一下就直接看到完整資訊（圖片、分類、熱量、價格、食材、
    作法步驟），跟點卡片上「查看完整食譜內容」看到的是同一個彈窗。"""
    picked = fetch_one_random_recipe()
    if picked:
        st.session_state["pending_dialog_recipe"] = (picked["id"], picked["seq"])
        st.session_state["carousel_paused"] = True  # 跟一般開彈窗一樣，順便暫停輪播
        st.session_state["surprise_me_error"] = False
    else:
        # 資料庫目前沒有任何食譜可以抽（例如資料表是空的）時，不要讓彈窗
        # 開一個不存在的食譜，改成在側邊欄顯示提示訊息。
        st.session_state["surprise_me_error"] = True


def _reset_all_filters():
    st.session_state["search_keyword_input"] = ""
    st.session_state["leftover_ingredients_input"] = ""
    st.session_state["leftover_ingredient_list"] = []
    st.session_state["leftover_parsed_by_ai"] = False
    st.session_state["list_page"] = 1
    # 2026-10-01 修正：一併清掉「料理詳情」彈窗狀態，避免按下「顯示全部料理」
    # 之後，先前還殘留著的彈窗狀態又被重新渲染出來、擋住剛重置好的列表畫面。
    st.session_state["pending_dialog_recipe"] = None
    for key in list(st.session_state.keys()):
        if key.startswith("category_filter_"):
            st.session_state[key] = []


with st.sidebar:
    st.markdown("### 🔍 快速搜尋")
    search_keyword = st.text_input(
        "輸入食材、菜名或關鍵字搜尋美味料理：",
        "",
        placeholder="例如：雞肉、豆腐、義大利麵、蒜香",
        key="search_keyword_input",
    )

    # 2026-10-01 修正：快速搜尋的關鍵字只要真的「改變」了，就立刻清掉可能殘留的
    # 「料理詳情」彈窗狀態。彈窗開著時主畫面會被隱藏，使用者唯一還能操作的地方
    # 就是側邊欄；如果不在這裡清除，舊的彈窗會在使用者明明是想重新搜尋時又跳出來，
    # 蓋掉剛打好的搜尋結果。這段必須放在 dialog_open 判斷「之前」才會立即生效。
    if st.session_state.get("_last_search_keyword") != search_keyword:
        st.session_state["_last_search_keyword"] = search_keyword
        st.session_state["pending_dialog_recipe"] = None

    st.markdown("---")
    st.markdown("### 🎲 不知道吃什麼？")
    st.caption("按一下，隨機幫你推一道料理，直接跳出完整詳細資訊（圖片、分類、熱量、價格、食材、作法）。")
    st.button(
        "🎲 幫我推一道料理",
        use_container_width=True,
        on_click=_surprise_me_callback,
    )
    if st.session_state.get("surprise_me_error"):
        st.warning("目前資料庫裡沒有可以推薦的料理，請稍後再試。")

    st.markdown("---")
    st.markdown("### 🥕 食材剩餘找料理")
    st.caption(
        "直接用口語描述目前剩餘的食材就可以了，例如「剩下一顆番茄及牛肉」，"
        "AI 會自動幫你拆出食材名稱，採「精準比對」找出同時用到這些食材的食譜"
        "（食譜要同時含有你輸入的全部食材才會列出來）。"
    )
    leftover_text = st.text_input(
        "剩餘食材：",
        "",
        placeholder="例如：剩下一顆番茄及牛肉",
        key="leftover_ingredients_input",
    )
    if st.button("🍳 幫我找料理", use_container_width=True):
        with st.spinner("AI 解析食材中…"):
            ingredient_list, parsed_by_ai = parse_leftover_ingredients(leftover_text)
        st.session_state["leftover_ingredient_list"] = ingredient_list
        st.session_state["leftover_parsed_by_ai"] = parsed_by_ai
        st.session_state["list_page"] = 1
        # 2026-10-01 修正：按下「幫我找料理」就是明確要看新的搜尋結果，
        # 同樣要清掉可能殘留的彈窗狀態，理由同上。
        st.session_state["pending_dialog_recipe"] = None
        if not ingredient_list:
            st.info("看不出剩餘食材是什麼，可以換個講法，或用逗號分開輸入試試。")

    leftover_ingredient_list = st.session_state.get("leftover_ingredient_list", [])
    if leftover_ingredient_list:
        st.markdown(
            "".join(f'<span class="cat-badge">{x}</span>' for x in leftover_ingredient_list),
            unsafe_allow_html=True,
        )
        if st.session_state.get("leftover_parsed_by_ai"):
            st.caption("🤖 已用 AI 解析你輸入的食材")
        else:
            st.caption("🔤 用規則式斷詞解析（未設定 GOOGLE_API_KEY，或 AI 呼叫失敗）")

    st.button("🍽️ 顯示全部料理", use_container_width=True, on_click=_reset_all_filters)

    st.markdown("---")
    st.markdown("### 💬 加入 LINE 好友")
    st.caption("天天推播今日特選食譜與健康低卡熱量分析！")
    LINE_URL = "https://line.me/R/ti/p/@437objtq"
    qr_code_url = "https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=" + LINE_URL
    st.image(qr_code_url, caption="掃描 QR Code 加好友", width=160)
    st.link_button("👉 點我直接加入 LINE 好友", LINE_URL, use_container_width=True)


# -------------------------------------------------------------
# 3b.（2026-10-02 起不再用來隱藏背景畫面，僅保留給第 6 節輪播判斷是否要
#     暫停自動刷新用）side note：側邊欄裡每個會改變搜尋條件的動作，都已經
#     在「3. 側邊欄」裡清掉 pending_dialog_recipe，確保側邊欄操作優先生效。
# -------------------------------------------------------------


# -------------------------------------------------------------
# 4. 主頁面版面：
#    最上方是主標題（Hero 區塊，滿版寬度）。
#    平常（沒有在快速搜尋）：下面分成左右兩欄——左欄放「今日精選推薦」
#    （內容留到第 6 節才填入，但版位固定在左欄），右欄放「固定顯示、
#    不折疊」的分類篩選（只保留 3 種：餐點類型／料理風格／飲食類型）。
#    一旦使用者在「快速搜尋」輸入了關鍵字：精選推薦與分類篩選整個隱藏，
#    主畫面改成滿版寬度直接顯示搜尋結果（見下方 quick_search_active）。
#    2026-10-02：料理詳情彈窗開著與否，不再影響這裡顯不顯示（見下方說明）。
# -------------------------------------------------------------
# 2026-10-02 修正：不再依「彈窗是否開著」隱藏 Hero／篩選／列表等背景畫面。
# 原本想避免背景在彈窗開著時若隱若現，但 Streamlit 的 st.dialog 本來就是
# 浮層（overlay），不需要主動隱藏背景；而且背景一旦被隱藏，一旦使用者用
# Streamlit 原生右上角「×」關閉彈窗（不會經過我們自己的程式碼），我方的
# 隱藏狀態就不會被清除，整個背景會變成一片空白。改成背景隨時正常顯示，
# 不管用什麼方式關閉彈窗，底下畫面都已經在顯示，不會有空白的問題。
st.markdown(
    """
    <div class="app-hero">
        <h1>🍳 今天吃啥咪！</h1>
        <p>從食材到熱量，一次幫你算好好——挑一道，今天就這樣吃。</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# 篩選條件只保留這 3 種分類（依需求移除「主要食材」「特色主題」兩種）。
MAIN_FILTER_TYPES = ["course", "cuisine", "diet"]

# 快速搜尋有輸入關鍵字時，篩選條件／今日精選推薦整個隱藏，
# 主畫面滿版寬度只顯示搜尋結果，版面更乾淨。
quick_search_active = bool(search_keyword.strip())

# 2026-10-01：改為「依分類群組」記錄已勾選的分類ID（group -> [ids]），
# 才能做到「同一群組內或邏輯、群組之間且邏輯」的精準比對。
selected_category_ids_by_type: dict[str, list[int]] = {}
carousel_slot = None

# 2026-10-02：篩選區／輪播區同樣不再因為「彈窗開著」而隱藏，理由同上方 Hero
# 區塊的註解——避免 Streamlit 原生關閉彈窗的方式造成背景整個變空白。
if not quick_search_active:
    col_carousel, col_filter = st.columns([0.62, 0.38])

    carousel_slot = col_carousel.container()

    with col_filter:
        st.markdown("#### 🎛️ 篩選條件")
        st.caption("同一類型內勾選＝符合任一；不同類型之間需「同時」符合（精準比對）")

        all_categories = fetch_categories()
        categories_by_type: dict[str, list[dict]] = defaultdict(list)
        for c in all_categories:
            categories_by_type[c.get("category_type") or "其他"].append(c)

        if not categories_by_type:
            st.caption("目前無法從資料庫取得分類清單，請確認連線設定。")
        for category_type in MAIN_FILTER_TYPES:
            cats = categories_by_type.get(category_type, [])
            name_to_id = {c["category_name"]: c["category_id"] for c in cats}
            display_label = CATEGORY_TYPE_LABELS.get(category_type, category_type)
            chosen_names = st.multiselect(
                display_label,
                options=list(name_to_id.keys()),
                default=[],
                placeholder=f"不選代表顯示全部「{display_label}」",
                key=f"category_filter_{category_type}",
            )
            chosen_ids = [name_to_id[name] for name in chosen_names]
            if chosen_ids:
                selected_category_ids_by_type[category_type] = chosen_ids

    st.markdown("---")

any_category_selected = any(selected_category_ids_by_type.values())


# -------------------------------------------------------------
# 5. 卡片顯示輔助函式
# -------------------------------------------------------------
def _render_recipe_image(image_url: str | None, ratio: str = "4 / 3"):
    """用固定「長寬比」（aspect-ratio）顯示料理圖片並搭配 object-fit: cover，
    不管原圖尺寸、不管卡片欄寬多少，每張圖片裁切出來的比例都完全一致；
    沒有圖片連結時顯示同樣比例的佔位色塊，避免版面跑掉或忽大忽小。"""
    if image_url:
        st.markdown(
            f'<div style="width:100%;aspect-ratio:{ratio};overflow:hidden;'
            f'border-radius:12px;margin-bottom:10px;">'
            f'<img src="{image_url}" alt="料理圖片" loading="lazy" '
            f'style="width:100%;height:100%;object-fit:cover;display:block;" />'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="width:100%;aspect-ratio:{ratio};border-radius:12px;'
            f'margin-bottom:10px;background:linear-gradient(135deg,#FFE8D6,#FFF3E0);'
            f'display:flex;align-items:center;justify-content:center;font-size:2.2rem;">'
            f'🍽️</div>',
            unsafe_allow_html=True,
        )


def _calorie_badge_html(item: dict) -> str:
    if item.get("energy_kcal") is None:
        return ""
    status = (item.get("calorie_status") or "").upper()
    if status in ("OK", "SUFFICIENT", "COMPLETE", "CALCULATED"):
        cls = "kcal-badge-ok"
    elif status in ("INSUFFICIENT",):
        cls = "kcal-badge-bad"
    else:
        cls = "kcal-badge-warn"
    return f'<span class="{cls}">🔥 約 {item["energy_kcal"]:.0f} kcal</span>'


def _weight_badge_html(item: dict) -> str:
    if item.get("total_weight_g") is None:
        return ""
    return f'<span class="weight-badge">⚖️ 約 {item["total_weight_g"]:.0f} g</span>'


def _price_badge_html(item: dict) -> str:
    if item.get("estimated_price") is None:
        return ""
    return f'<span class="price-badge">💰 約 ${item["estimated_price"]:.0f}</span>'


def _match_badge_html(item: dict) -> str:
    if item.get("matched_ingredient_count") is None:
        return ""
    total = item.get("total_ingredient_count", item["matched_ingredient_count"])
    return f'<span class="cat-badge">🥕 精準符合全部 {total} 項剩餘食材</span>'


def _open_recipe_dialog_callback(recipe_id, seq):
    """按鈕的 on_click 回呼：在畫面重新整理「之前」就先把要開哪個食譜的資訊
    寫進 session_state，這樣輪播那邊的自動刷新（st_autorefresh）在同一次
    重新執行時就能立刻看到「有彈窗要開」而跳過刷新，不會跟彈窗開啟的過程
    互搶，才不會出現點下去閃退的問題。"""
    st.session_state["pending_dialog_recipe"] = (recipe_id, seq)
    st.session_state["carousel_paused"] = True  # 順便暫停輪播，看詳情時畫面不會一直跳動


def _close_recipe_dialog_callback():
    """關閉彈窗的 on_click 回呼。

    2026-10-02 修正「關閉後畫面變空白」的 bug：原本在 `st.dialog` 裡面的
    按鈕判斷式中直接呼叫 `st.rerun()`，這是 Streamlit 已知會出問題的寫法——
    彈窗收起來的時機跟手動 rerun 觸發的整頁重跑時機會卡在一起，導致畫面
    暫時整個空白，要再操作一次才會恢復。改成跟「開啟」彈窗一樣，用 on_click
    回呼只負責更新 session_state，讓按鈕點擊「自然」觸發的重新整理去關閉
    彈窗，不再額外手動呼叫 st.rerun()。"""
    st.session_state["pending_dialog_recipe"] = None
    st.session_state["carousel_paused"] = False  # 順便讓輪播恢復自動播放


def render_recipe_card(item: dict, detail_button_key: str):
    with st.container():
        st.markdown('<div class="recipe-card">', unsafe_allow_html=True)

        _render_recipe_image(item.get("image_url"))

        st.markdown(f"#### {item.get('name', '未命名食譜')}")

        categories = item.get("categories") or []
        if categories:
            badges = "".join(f'<span class="cat-badge">{c["category_name"]}</span>' for c in categories)
            st.markdown(badges, unsafe_allow_html=True)

        info_badges = "".join(
            [
                _match_badge_html(item),
                _calorie_badge_html(item),
                _price_badge_html(item),
            ]
        )
        if info_badges:
            st.markdown(info_badges, unsafe_allow_html=True)

        if item.get("raw_keywords"):
            st.caption(item["raw_keywords"])

        st.markdown("</div>", unsafe_allow_html=True)
        st.button(
            "👀 查看完整食譜內容",
            key=detail_button_key,
            use_container_width=True,
            on_click=_open_recipe_dialog_callback,
            args=(item.get("id"), item.get("seq")),
        )


@st.dialog("料理詳情", width="large")
def show_recipe_dialog(recipe_id: int, seq: str):
    st.button(
        "✖ 關閉",
        key="close_recipe_dialog",
        on_click=_close_recipe_dialog_callback,
    )

    detail = fetch_recipe_detail(recipe_id, seq)
    if not detail:
        st.error("找不到這道食譜的詳細資料。")
        return

    _render_recipe_image(detail.get("image_url"), ratio="16 / 9")

    st.subheader(detail.get("name", "未命名食譜"))
    categories = detail.get("categories") or []
    if categories:
        st.markdown(
            "".join(f'<span class="cat-badge">{c["category_name"]}</span>' for c in categories),
            unsafe_allow_html=True,
        )

    # 2026-09-30：依需求移除「估算總重量」，只保留總熱量與總價格。
    metric_cols = st.columns(2)
    with metric_cols[0]:
        if detail.get("energy_kcal") is not None:
            st.metric("估算總熱量", f"{detail['energy_kcal']:.0f} kcal")
        else:
            st.metric("估算總熱量", "尚無資料")
    with metric_cols[1]:
        if detail.get("estimated_price") is not None:
            st.metric("估算總價格", f"${detail['estimated_price']:.0f}")
        else:
            st.metric("估算總價格", "尚無資料")

    kcal_html = _calorie_badge_html(detail)
    if kcal_html:
        st.markdown(kcal_html, unsafe_allow_html=True)
    if detail.get("energy_kcal") is None:
        st.caption("⚠ 這道食譜在 MySQL（recipe_nutrition_summary）裡還沒有算好的總熱量資料。")

    estimated_notes = []
    if (detail.get("calorie_status") or "").upper() == "ESTIMATED":
        estimated_notes.append("熱量")
    if (detail.get("price_status") or "").upper() == "ESTIMATED":
        estimated_notes.append("價格")
    if estimated_notes:
        st.caption(
            f"ℹ️ 部分食材（例如「適量」「少許」，或查無營養／價格資料的食材）以概略值估算，"
            f"{'與'.join(estimated_notes)}僅供參考。"
        )

    st.markdown("---")
    st.markdown("**🥕 食材清單**")
    ingredients = detail.get("ingredients") or []
    if ingredients:
        for ing in ingredients:
            label = ing.get("raw_name") or ing.get("canonical_name") or "（未命名食材）"
            raw_text = ing.get("raw_text")

            line = f"- **{label}**"
            # 只顯示爬蟲抓下來的原始份量文字（例如「300公克」「1大匙」），
            # 不再額外附加「→ 約 X g｜價格：無資料」這種換算/估價資訊，
            # 版面跟原始食譜網站的「材料」清單一致。
            if raw_text and raw_text.strip() and raw_text.strip() != label:
                line += f"　{raw_text.strip()}"

            # 「（重量為推估值）」「⚠ QUALITATIVE_NO_HIGH_CONFIDENCE_RULE」等資料品質
            # 標籤依需求隱藏，不在畫面上顯示（is_estimated / needs_manual_review
            # 這兩個欄位依然保留在資料裡，只是不渲染成畫面上的文字）。
            st.markdown(line, unsafe_allow_html=True)
    else:
        st.caption("（無食材資料）")

    if detail.get("steps"):
        st.markdown("---")
        st.markdown("**📖 作法步驟**")
        steps_text = str(detail["steps"]).strip()
        # 原始做法步驟是一整段連續文字（1.xxx 2.xxx 3.xxx...），
        # 依「數字.」把每一個步驟切開，改成條列式、每個步驟各佔一行。
        step_parts = [
            p.strip() for p in re.split(r"(?<=[。.!?])\s*(?=\d+[.、])", steps_text) if p.strip()
        ]
        if len(step_parts) > 1:
            for part in step_parts:
                st.markdown(f"- {part}")
        else:
            st.write(steps_text)

    if detail.get("source_url"):
        st.markdown("---")
        st.link_button("🔗 查看原始食譜來源", detail["source_url"], use_container_width=True)


# 只要 session_state 裡還記著「有一個食譜詳情要顯示」，就每次重新執行都呼叫
# show_recipe_dialog（不管是哪個按鈕、哪次重新整理觸發的）；這樣彈窗才會穩定
# 保持開啟，不會因為輪播的自動刷新（下面第 6 節）而被中途打斷、閃退關閉。
#
# 2026-10-02：背景內容（Hero／篩選／輪播／列表）已經改成不管彈窗開不開都
# 正常渲染，所以這裡不再需要額外維護一個 dialog_open 旗標去控制背景顯示；
# 單純依 session_state 目前的值決定要不要呼叫 show_recipe_dialog 就好。
# 使用者用 Streamlit 原生右上角「×」關閉彈窗時，Streamlit 會自動避免同一個
# 彈窗在下一次重新執行時又跳出來，但我們自己的 pending_dialog_recipe 不會
# 被清除——這對畫面沒有影響了（背景本來就一直顯示），只是下次使用者點開
# 「別的」食譜卡片時一樣能正常換成新的彈窗內容。
if st.session_state.get("pending_dialog_recipe"):
    _pending_recipe_id, _pending_seq = st.session_state["pending_dialog_recipe"]
    show_recipe_dialog(_pending_recipe_id, _pending_seq)


# -------------------------------------------------------------
# 6. 今日精選推薦（填入第 4 節在頁面最上方保留的版位 carousel_slot）
#    用原生 Streamlit 元件呈現（跟下面「🍽️ 料理列表」同一種卡片），
#    這樣「查看完整食譜內容」才能像列表卡片一樣，真的跳出完整詳情彈窗
#    （食材、熱量、價格），不是只連到外部網址。
#
#    自動輪播：
#      - 固定 5 筆隨機料理
#      - 每張停留「固定 5 秒」後自動換下一張（換哪一張仍是隨機挑，不是依序）
#        → 用 streamlit_autorefresh 每秒觸發一次重新執行，時間到了才真的換張
#      - 因為 Streamlit 收不到瀏覽器的滑鼠 hover 事件，「移到就暫停」改成
#        用「⏸ 暫停 / ▶ 繼續」按鈕手動控制（點一下就會暫停/繼續自動輪播）
# -------------------------------------------------------------
if "carousel_pool" not in st.session_state:
    st.session_state.carousel_pool = fetch_random_recipes(limit=CAROUSEL_SIZE)
carousel_pool = st.session_state.carousel_pool

is_default_view = (
    not any_category_selected
    and not search_keyword.strip()
    and not leftover_ingredient_list
)

if carousel_slot is not None and is_default_view and carousel_pool:
    n = len(carousel_pool)

    if "carousel_index" not in st.session_state:
        st.session_state.carousel_index = random.randrange(n)
    if "carousel_paused" not in st.session_state:
        st.session_state.carousel_paused = False
    if "carousel_next_switch_at" not in st.session_state:
        st.session_state.carousel_next_switch_at = time.time() + CAROUSEL_INTERVAL_SECONDS

    with carousel_slot:
        st.markdown("### 🌟 主廚今日精選推薦")

        # 有食譜詳情彈窗正要顯示（pending_dialog_recipe 有值）時，完全不啟用
        # 自動刷新計時器：如果讓它繼續在背景每秒觸發重新執行，會跟彈窗開啟
        # 的過程互搶，就是先前「點擊查看完整食譜內容會閃退」的原因。
        dialog_is_pending = bool(st.session_state.get("pending_dialog_recipe"))

        if st_autorefresh is not None and not dialog_is_pending:
            # 每 1 秒觸發一次重新執行，讓下面的「時間到了才換下一張」邏輯
            # 有機會被檢查到；不會每秒都換圖，只是每秒「檢查一次是否該換了」。
            st_autorefresh(interval=1000, key="carousel_autorefresh")

            if not st.session_state.carousel_paused and time.time() >= st.session_state.carousel_next_switch_at:
                if n > 1:
                    next_idx = st.session_state.carousel_index
                    while next_idx == st.session_state.carousel_index:
                        next_idx = random.randrange(n)  # 隨機挑下一張，不是依序
                    st.session_state.carousel_index = next_idx
                st.session_state.carousel_next_switch_at = time.time() + CAROUSEL_INTERVAL_SECONDS

        c_btn1, c_card, c_btn2 = st.columns([0.08, 0.84, 0.08])

        with c_btn1:
            st.write("")
            st.write("")
            if st.button("◀", key="prev_carousel", use_container_width=True):
                st.session_state.carousel_index = (st.session_state.carousel_index - 1) % n
                st.session_state.carousel_next_switch_at = time.time() + CAROUSEL_INTERVAL_SECONDS

        current_item = carousel_pool[st.session_state.carousel_index]
        with c_card:
            render_recipe_card(current_item, detail_button_key="carousel_detail_btn")

        with c_btn2:
            st.write("")
            st.write("")
            if st.button("▶", key="next_carousel", use_container_width=True):
                st.session_state.carousel_index = (st.session_state.carousel_index + 1) % n
                st.session_state.carousel_next_switch_at = time.time() + CAROUSEL_INTERVAL_SECONDS

        toggle_col, _ = st.columns([0.3, 0.7])
        with toggle_col:
            toggle_label = "▶ 繼續自動輪播" if st.session_state.carousel_paused else "⏸ 暫停自動輪播"
            if st.button(toggle_label, key="carousel_pause_toggle", use_container_width=True):
                st.session_state.carousel_paused = not st.session_state.carousel_paused
                st.session_state.carousel_next_switch_at = time.time() + CAROUSEL_INTERVAL_SECONDS

        if st_autorefresh is None:
            st.caption(
                "要開啟自動輪播（每 5 秒自動換下一張），請先安裝套件："
                "`uv add streamlit-autorefresh`。目前可以用左右箭頭手動切換。"
            )

        st.markdown("---")


# -------------------------------------------------------------
# 7. 料理清單（依情境呼叫不同查詢函式，皆使用資料庫端分頁）
#    2026-10-02：改成不管彈窗開不開都正常渲染（理由同第 4 節開頭的註解），
#    料理詳情彈窗用 Streamlit 自己的浮層蓋在上面，底下列表維持正常顯示。
# -------------------------------------------------------------
current_filter_signature = (
    tuple(
        sorted(
            (k, tuple(sorted(v)))
            for k, v in selected_category_ids_by_type.items()
            if v
        )
    ),
    search_keyword.strip().lower(),
    tuple(leftover_ingredient_list),
)
if st.session_state.get("last_filter_signature") != current_filter_signature:
    st.session_state.last_filter_signature = current_filter_signature
    st.session_state.list_page = 1
    # 篩選條件（分類/關鍵字/剩餘食材）真的改變時，順便清掉可能殘留的彈窗狀態。
    st.session_state["pending_dialog_recipe"] = None
if "list_page" not in st.session_state:
    st.session_state.list_page = 1

if leftover_ingredient_list:
    result = search_recipes_by_ingredients(
        tuple(leftover_ingredient_list), page=st.session_state.list_page, limit=PAGE_SIZE,
    )
elif search_keyword.strip():
    result = search_recipes(
        search_keyword.strip(), selected_category_ids_by_type,
        page=st.session_state.list_page, limit=PAGE_SIZE,
    )
elif any_category_selected:
    result = recommend_recipes_by_filters(
        selected_category_ids_by_type, page=st.session_state.list_page, limit=PAGE_SIZE,
    )
else:
    result = fetch_recipes_page(page=st.session_state.list_page, limit=PAGE_SIZE)

items = result.get("items", [])
total = result.get("total", 0)
total_pages = result.get("total_pages", 0)

list_title = "🍽️ 料理列表"
if leftover_ingredient_list:
    list_title = "🥕 用剩餘食材找到的料理"

st.markdown(f"### {list_title}　<span style='color:#999; font-size:0.85rem;'>共 {total} 筆</span>", unsafe_allow_html=True)

if not items:
    if leftover_ingredient_list:
        st.warning("找不到用得到這些食材的料理，試試看少輸入幾個食材，或換個食材名稱。")
    else:
        st.warning("查無符合條件的料理，請更換關鍵字或調整篩選條件重試！")
else:
    for i in range(0, len(items), 2):
        row_cols = st.columns(2)
        batch = items[i:i + 2]
        for col_idx, item in enumerate(batch):
            with row_cols[col_idx]:
                card_key = f"list_{st.session_state.list_page}_{i}_{col_idx}_detail_btn"
                render_recipe_card(item, detail_button_key=card_key)

    if total_pages > 1:
        st.markdown("---")
        p_prev, p_mid, p_next = st.columns([1, 2, 1])
        with p_prev:
            if st.button("⬅️ 上一頁", disabled=st.session_state.list_page <= 1, use_container_width=True):
                st.session_state.list_page -= 1
                st.session_state["pending_dialog_recipe"] = None
                st.rerun()
        with p_mid:
            st.markdown(
                f"<div class='page-indicator'>第 {st.session_state.list_page} / {total_pages} 頁</div>",
                unsafe_allow_html=True,
            )
        with p_next:
            if st.button("下一頁 ➡️", disabled=st.session_state.list_page >= total_pages, use_container_width=True):
                st.session_state.list_page += 1
                st.session_state["pending_dialog_recipe"] = None
                st.rerun()

# 執行方式：uv run streamlit run web_app.py