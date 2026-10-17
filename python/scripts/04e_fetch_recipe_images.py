from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from app.db import get_connection

# --------------------------------------------------------------------------
# 這支腳本補齊 04_import_recipes_mysql.py 之後缺的一步：
# 逐一打開每道食譜的 recipes.source_url（原始食譜網頁連結），
# 從網頁的 <meta property="og:image" ...>（找不到再退回 twitter:image）
# 抓出真正的「圖片連結」，寫回 recipes.image_url。
#
# 網頁端（網頁new.py）本來就已經會直接讀 recipes.image_url 顯示圖片，
# 只是這欄位如果是 NULL，畫面就會顯示佔位圖示 —— 跑完這支腳本、
# image_url 有值之後，主畫面就會出現真正的料理圖片。
#
# 執行方式（跟其他 scripts/ 底下的腳本一樣）：
#     uv run python scripts/04e_fetch_recipe_images.py
#
# 可重複執行：預設只處理「image_url 還是 NULL」的食譜，不會重抓已經有
# 圖片的食譜（如果想強制全部重抓，把下面 ONLY_MISSING 改成 False）。
# --------------------------------------------------------------------------

ONLY_MISSING = True
REQUEST_TIMEOUT_SECONDS = 10
SLEEP_BETWEEN_REQUESTS_SECONDS = 0.6
COMMIT_EVERY = 20

REPORT = Path("/workspace/data/processed/image_fetch_report.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# og:image 的 property/content 順序在不同網站可能不同，兩種都比對。
OG_IMAGE_RE_1 = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE_2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)
TWITTER_IMAGE_RE_1 = re.compile(
    r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
TWITTER_IMAGE_RE_2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
    re.IGNORECASE,
)


def ensure_image_url_column(cur):
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'recipes'
          AND column_name = 'image_url'
        """
    )
    if cur.fetchone()["n"] == 0:
        cur.execute("ALTER TABLE recipes ADD COLUMN image_url VARCHAR(1000) NULL")


def extract_image_url(html: str, page_url: str) -> str | None:
    for pattern in (OG_IMAGE_RE_1, OG_IMAGE_RE_2, TWITTER_IMAGE_RE_1, TWITTER_IMAGE_RE_2):
        match = pattern.search(html)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                # og:image 偶爾是相對路徑，補成絕對網址。
                return urljoin(page_url, candidate)
    return None


def fetch_image_url(source_url: str) -> tuple[str | None, str | None]:
    """回傳 (image_url, error_message)。成功時 error_message 為 None。"""
    try:
        resp = requests.get(source_url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - 對外部網站的任何錯誤都要能跳過繼續
        return None, f"REQUEST_FAILED:{exc}"

    image_url = extract_image_url(resp.text, source_url)
    if not image_url:
        return None, "NO_OG_IMAGE_FOUND"
    return image_url, None


def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    success_count = 0
    failure_count = 0
    failures = []

    with get_connection() as conn, conn.cursor() as cur:
        ensure_image_url_column(cur)

        where_clause = "WHERE source_url IS NOT NULL AND source_url <> ''"
        if ONLY_MISSING:
            where_clause += " AND (image_url IS NULL OR image_url = '')"

        cur.execute(
            f"""
            SELECT id, seq, name, source_url
            FROM recipes
            {where_clause}
            ORDER BY id
            """
        )
        targets = cur.fetchall()
        print(f"待抓取圖片的食譜數量：{len(targets)}")

        for i, recipe in enumerate(targets, start=1):
            image_url, error = fetch_image_url(recipe["source_url"])

            if image_url:
                cur.execute(
                    "UPDATE recipes SET image_url = %s WHERE id = %s",
                    (image_url, recipe["id"]),
                )
                success_count += 1
            else:
                failure_count += 1
                failures.append(
                    {
                        "id": recipe["id"],
                        "seq": recipe["seq"],
                        "name": recipe["name"],
                        "source_url": recipe["source_url"],
                        "error": error,
                    }
                )

            if i % COMMIT_EVERY == 0:
                conn.commit()
                print(f"進度 {i}/{len(targets)}　成功={success_count}　失敗={failure_count}")

            time.sleep(SLEEP_BETWEEN_REQUESTS_SECONDS)

        conn.commit()

    REPORT.write_text(
        json.dumps(
            {
                "only_missing": ONLY_MISSING,
                "total_targets": success_count + failure_count,
                "success_count": success_count,
                "failure_count": failure_count,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("圖片抓取完成。")
    print(f"成功寫入 image_url：{success_count} 筆")
    print(f"失敗（維持 NULL，之後可重跑再試一次）：{failure_count} 筆")
    print(f"失敗清單 -> {REPORT}")


if __name__ == "__main__":
    main()