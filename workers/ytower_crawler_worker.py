import json
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import urljoin
import re

import requests
from bs4 import BeautifulSoup
from kafka import KafkaConsumer, KafkaProducer
from requests import exceptions as req_exc
from proxy_pool import (
    build_proxy_session,
    get_proxy_collection,
    lease_proxy,
    mark_proxy_failure,
    mark_proxy_success,
    release_proxy,
)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_JOB_TOPIC = os.getenv("KAFKA_JOB_TOPIC", "crawler_jobs")
KAFKA_RESULT_TOPIC = os.getenv("KAFKA_RESULT_TOPIC", "ytower_recipe_results")
KAFKA_INCREMENTAL_STATUS_TOPIC = os.getenv("KAFKA_INCREMENTAL_STATUS_TOPIC", "crawler_incremental_status")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "ytower-crawler-group")
WORKER_NAME = os.getenv("WORKER_NAME", "crawler-worker")
NETWORK_MODE = os.getenv("CRAWLER_NETWORK_MODE", "proxy").strip().lower()

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "20"))
REQUEST_RETRIES = int(os.getenv("REQUEST_RETRIES", "3"))
MAX_PROXY_SWITCHES_PER_SEQ = int(os.getenv("MAX_PROXY_SWITCHES_PER_SEQ", "5"))
PROXY_WAIT_SECONDS = int(os.getenv("PROXY_WAIT_SECONDS", "15"))
MAX_NOT_FOUND_LIMIT = int(os.getenv("MAX_NOT_FOUND_LIMIT", "50"))
FULL_CRAWL_CHUNK_SIZE = int(os.getenv("FULL_CRAWL_CHUNK_SIZE", "50"))
COOLDOWN_SUCCESS_COUNT = int(os.getenv("COOLDOWN_SUCCESS_COUNT", "50"))
CRAWL_SLEEP_MIN = float(os.getenv("CRAWL_SLEEP_MIN", "2.5"))
CRAWL_SLEEP_MAX = float(os.getenv("CRAWL_SLEEP_MAX", "5.0"))
DIRECT_CRAWL_SLEEP_MIN = float(os.getenv("DIRECT_CRAWL_SLEEP_MIN", "4.0"))
DIRECT_CRAWL_SLEEP_MAX = float(os.getenv("DIRECT_CRAWL_SLEEP_MAX", "8.0"))
NOT_FOUND_SLEEP_MIN = float(os.getenv("NOT_FOUND_SLEEP_MIN", "0.5"))
NOT_FOUND_SLEEP_MAX = float(os.getenv("NOT_FOUND_SLEEP_MAX", "1.2"))
COOLDOWN_SLEEP_MIN = float(os.getenv("COOLDOWN_SLEEP_MIN", "10"))
COOLDOWN_SLEEP_MAX = float(os.getenv("COOLDOWN_SLEEP_MAX", "20"))
DIRECT_BLOCK_COOLDOWN_SECONDS = int(os.getenv("DIRECT_BLOCK_COOLDOWN_SECONDS", "900"))
DIRECT_BLOCK_MAX_RETRIES = int(os.getenv("DIRECT_BLOCK_MAX_RETRIES", "2"))
MAX_POLL_INTERVAL_MS = int(os.getenv("MAX_POLL_INTERVAL_MS", str(16 * 60 * 60 * 1000)))
YTOWER_SEARCH_URL = os.getenv("YTOWER_SEARCH_URL", "https://www.ytower.com.tw/recipe/recipe-search.asp")
YTOWER_SEARCH_PAGE_PARAM = os.getenv("YTOWER_SEARCH_PAGE_PARAM", "page")
YTOWER_SEARCH_CHUNK_SIZE = int(os.getenv("YTOWER_SEARCH_CHUNK_SIZE", "50"))
YTOWER_SEARCH_OLD_PAGE_STOP = int(os.getenv("YTOWER_SEARCH_OLD_PAGE_STOP", "2"))
YTOWER_INCREMENTAL_OLD_STREAK_LIMIT = int(os.getenv("YTOWER_INCREMENTAL_OLD_STREAK_LIMIT", "20"))
TEST_MODE = os.getenv("YTOWER_TEST_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
TEST_LIMIT = max(1, int(os.getenv("YTOWER_TEST_LIMIT", "20")))
SEQ_RE = re.compile(r"(?:[?&]seq=|\b)([A-I]\d{2}-\d{3,4})", re.I)
DATE_RE = re.compile(r"(20\d{2})[./\-年](\d{1,2})[./\-月](\d{1,2})")

CHALLENGE_MARKERS = (
    "captcha",
    "g-recaptcha",
    "hcaptcha",
    "cf-chl",
    "challenge-platform",
    "人機驗證",
    "驗證碼",
    "請點選",
    "請選取",
    "機器人驗證",
)

_stop_requested = False


class BlockedPageError(RuntimeError):
    pass


class RetryableRequestError(RuntimeError):
    pass


class ServerResponseError(RuntimeError):
    """伺服器 5xx 重試耗盡；記錄後跳過該 numeric SEQ，不算 missing。"""
    pass


def _signal_handler(signum, frame):
    global _stop_requested
    _stop_requested = True
    print(f"[{WORKER_NAME}] received signal {signum}; stopping after current operation", flush=True)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def candidate_seqs(prefix: str, seq_num: int):
    """同一個 numeric SEQ 同時保留網站的 4 位數與 3 位數格式。"""
    values = [f"{prefix}-{seq_num:04d}"]
    if seq_num < 1000:
        values.append(f"{prefix}-{seq_num:03d}")
    # 1000 以上兩種格式相同；小於 1000 時避免任何意外重複。
    return list(dict.fromkeys(values))


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.ytower.com.tw/",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
    }


def build_direct_session() -> requests.Session:
    session = requests.Session()
    # Direct worker 明確不沿用主機或容器的 HTTP(S)_PROXY 環境變數。
    session.trust_env = False
    session.headers.update(_browser_headers())
    return session


def wait_for_proxy(proxy_collection):
    while not _stop_requested:
        proxy = lease_proxy(proxy_collection, WORKER_NAME)
        if proxy:
            session = build_proxy_session(proxy["url"])
            print(
                f"[{WORKER_NAME}] leased proxy={proxy['url']} exit={proxy.get('exit_ip')} "
                f"latency={proxy.get('latency_ms')}ms",
                flush=True,
            )
            return proxy, session
        print(
            f"[{WORKER_NAME}] no verified TW proxy available; staying out of Kafka group; "
            f"wait {PROXY_WAIT_SECONDS}s",
            flush=True,
        )
        time.sleep(PROXY_WAIT_SECONDS)
    raise RuntimeError("shutdown requested")


def looks_like_challenge(response: requests.Response) -> bool:
    if response.status_code in (403, 429):
        return True
    text = (response.text or "").lower()
    return any(marker.lower() in text for marker in CHALLENGE_MARKERS)


def request_recipe_page(
    seq: str, session: requests.Session
) -> Tuple[str, Optional[requests.Response], Optional[str], Optional[int]]:
    """
    回傳 (status, response, error, latency_ms)
      ok              -> HTTP 200 且未偵測到 challenge，交給 parser
      not_found       -> 明確 404/410
      blocked         -> CAPTCHA / challenge / 403 / 429；不能算 missing
      retryable_error -> timeout / connect / proxy / 5xx / 其他異常 HTTP；不能算 missing
    """
    url = f"https://www.ytower.com.tw/recipe/iframe-recipe.asp?seq={seq}"
    last_error = None
    last_http_status = None

    for attempt in range(1, REQUEST_RETRIES + 1):
        started = time.monotonic()
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            latency_ms = int((time.monotonic() - started) * 1000)

            if response.status_code in (404, 410):
                return "not_found", response, None, latency_ms

            if looks_like_challenge(response):
                return "blocked", response, f"challenge/http {response.status_code}", latency_ms

            if 500 <= response.status_code < 600:
                last_http_status = response.status_code
                raise req_exc.HTTPError(f"HTTP {response.status_code}", response=response)

            if response.status_code != 200:
                return (
                    "retryable_error",
                    response,
                    f"unexpected HTTP {response.status_code}",
                    latency_ms,
                )

            return "ok", response, None, latency_ms

        except (req_exc.Timeout, req_exc.ConnectionError, req_exc.ProxyError, req_exc.HTTPError) as exc:
            last_error = str(exc)
            if attempt < REQUEST_RETRIES:
                sleep_seconds = min(2 ** attempt, 8) + random.uniform(0, 1)
                print(
                    f"[{WORKER_NAME}] request retry {attempt}/{REQUEST_RETRIES} {seq}: "
                    f"{exc}; sleep={sleep_seconds:.1f}s",
                    flush=True,
                )
                time.sleep(sleep_seconds)

    if last_http_status is not None and 500 <= last_http_status < 600:
        return "server_error", None, last_error or f"HTTP {last_http_status}", None
    return "retryable_error", None, last_error or "request failed", None


def parse_recipe_response(seq: str, seq_num: int, prefix: str, response: requests.Response) -> Optional[dict]:
    response.encoding = "big5"
    soup = BeautifulSoup(response.text, "html.parser")
    title_el = soup.select_one("#recipe_name h2 a")
    if not title_el or not title_el.text.strip():
        return None

    title = title_el.text.strip()
    time_el = soup.select_one("#recipe_info time")
    publish_date = (
        (time_el.get("datetime", "").strip() or time_el.text.strip()) if time_el else ""
    )
    keywords = [a.text.strip() for a in soup.select("div.recie_tag a") if a.text.strip()]

    ingredients = []
    for li in soup.select("#recipe_item ul.ingredient li"):
        name_a = li.select_one(".ingredient_name a")
        amount_span = li.select_one(".ingredient_amount")
        if name_a and amount_span:
            ingredients.append(f"{name_a.text.strip()} {amount_span.text.strip()}")
        elif name_a:
            ingredients.append(name_a.text.strip())

    steps = [li.text.strip() for li in soup.select("#recipe_info li.step") if li.text.strip()]

    return {
        "SEQ": seq,
        "seq_num": seq_num,
        "prefix": prefix,
        "食譜名稱": title,
        "上線日期": publish_date,
        "關鍵字": ", ".join(keywords),
        "食譜網址": f"https://www.ytower.com.tw/recipe/iframe-recipe.asp?seq={seq}",
        "材料": " | ".join(ingredients),
        "做法步驟": "\n".join(steps),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "crawler_worker": WORKER_NAME,
        "network_mode": NETWORK_MODE,
    }


def fetch_numeric_seq(
    prefix: str,
    seq_num: int,
    session: requests.Session,
    proxy_collection=None,
    proxy_doc=None,
):
    """
    一個 numeric SEQ 的 4 位數/3 位數候選要全部判斷完。
    只有兩種格式都確定沒有食譜時，才回 numeric_not_found。
    """
    for seq in candidate_seqs(prefix, seq_num):
        status, response, error, latency_ms = request_recipe_page(seq, session)

        if NETWORK_MODE == "proxy" and proxy_doc is not None and status in ("ok", "not_found"):
            mark_proxy_success(
                proxy_collection,
                proxy_doc["_id"],
                WORKER_NAME,
                latency_ms=latency_ms,
            )

        if status == "blocked":
            raise BlockedPageError(f"{seq}: {error or 'challenge detected'}")
        if status == "server_error":
            raise ServerResponseError(f"{seq}: {error or 'server 5xx'}")
        if status == "retryable_error":
            raise RetryableRequestError(f"{seq}: {error or 'request failed'}")
        if status == "not_found":
            continue

        if response is not None:
            try:
                data = parse_recipe_response(seq, seq_num, prefix, response)
            except Exception as exc:
                raise RetryableRequestError(f"parse error {seq}: {exc}") from exc
            if data is not None:
                return "found", data

            # YTower 對不存在的 SEQ 可能仍回 HTTP 200，但沒有 recipe DOM。
            # 已知 CAPTCHA/challenge 已在 request_recipe_page 先攔截，因此這裡視為候選格式不存在。
            continue

    return "numeric_not_found", None


def crawl_job(
    job: dict,
    producer: KafkaProducer,
    proxy_collection=None,
    initial_proxy=None,
    initial_session=None,
) -> dict:
    """
    處理一個 prefix chunk。

    FULL crawl 採 sequential chunk relay：
      1-50 成功後才 enqueue 51-100。
    因此不會預先把後續 chunk 全部排入 Kafka。

    consecutive_not_found 會由 job 帶到下一個 chunk，保留跨 chunk 的
    「連續 50 個 numeric SEQ 不存在就停止」語意。
    """
    prefix = str(job["prefix"])
    start_num = max(1, int(job.get("start_num", 1)))
    requested_end = int(job.get("end_num", start_num + FULL_CRAWL_CHUNK_SIZE - 1))
    max_seq_number = int(job.get("max_seq_number", requested_end))
    # 強制以目前設定的 chunk size 為準。
    # Kafka 裡即使還有舊的 250-range job，也只處理前 50 筆，
    # 成功後再 relay 下一個 50，避免為了改設定而清空 Kafka。
    chunk_size = max(1, FULL_CRAWL_CHUNK_SIZE)
    end_num = min(start_num + chunk_size - 1, requested_end, max_seq_number)
    max_not_found = int(job.get("max_not_found_limit", MAX_NOT_FOUND_LIMIT))
    consecutive_not_found = int(job.get("consecutive_not_found", 0))

    success_count = 0
    produced = 0
    stopped = False
    last_seq_num = start_num - 1
    proxy_doc = initial_proxy
    session = initial_session
    owns_session = initial_session is None

    if NETWORK_MODE == "direct":
        session = session or build_direct_session()
        print(
            f"[{WORKER_NAME}] start chunk prefix={prefix} range={start_num}-{end_num} "
            f"missing_in={consecutive_not_found} mode=direct",
            flush=True,
        )
    else:
        if proxy_doc is None or session is None:
            proxy_doc, session = wait_for_proxy(proxy_collection)
            owns_session = True
        print(
            f"[{WORKER_NAME}] start chunk prefix={prefix} range={start_num}-{end_num} "
            f"missing_in={consecutive_not_found} mode=proxy",
            flush=True,
        )

    try:
        for seq_num in range(start_num, end_num + 1):
            if _stop_requested:
                raise RuntimeError("shutdown requested")

            last_seq_num = seq_num
            direct_block_retries = 0
            proxy_switches = 0

            while True:
                try:
                    numeric_status, data = fetch_numeric_seq(
                        prefix,
                        seq_num,
                        session,
                        proxy_collection=proxy_collection,
                        proxy_doc=proxy_doc,
                    )
                    break

                except BlockedPageError as exc:
                    if NETWORK_MODE == "direct":
                        direct_block_retries += 1
                        print(
                            f"[{WORKER_NAME}] BLOCKED/challenge at {prefix}-{seq_num}: {exc}; "
                            f"not counted as missing; cooldown {DIRECT_BLOCK_COOLDOWN_SECONDS}s "
                            f"({direct_block_retries}/{DIRECT_BLOCK_MAX_RETRIES})",
                            file=sys.stderr,
                            flush=True,
                        )
                        if direct_block_retries > DIRECT_BLOCK_MAX_RETRIES:
                            raise RuntimeError(
                                f"direct IP remains blocked at {prefix}-{seq_num}; "
                                "chunk offset not committed"
                            ) from exc
                        time.sleep(DIRECT_BLOCK_COOLDOWN_SECONDS)
                        session.close()
                        session = build_direct_session()
                        owns_session = True
                        continue

                    proxy_switches += 1
                    mark_proxy_failure(
                        proxy_collection, proxy_doc["_id"], WORKER_NAME, str(exc)
                    )
                    session.close()
                    try:
                        release_proxy(
                            proxy_collection, proxy_doc["_id"], WORKER_NAME
                        )
                    except Exception:
                        pass
                    print(
                        f"[{WORKER_NAME}] proxy challenge at {prefix}-{seq_num}; "
                        f"switching proxy ({proxy_switches}/{MAX_PROXY_SWITCHES_PER_SEQ})",
                        file=sys.stderr,
                        flush=True,
                    )
                    if proxy_switches >= MAX_PROXY_SWITCHES_PER_SEQ:
                        raise RuntimeError(
                            f"no usable proxy after {proxy_switches} challenge switches "
                            f"at {prefix}-{seq_num}"
                        ) from exc
                    proxy_doc, session = wait_for_proxy(proxy_collection)
                    owns_session = True
                    continue

                except ServerResponseError as exc:
                    # 單一 numeric SEQ 在 REQUEST_RETRIES 次後仍為 HTTP 5xx：
                    # 記錄後跳過，不算 not_found，也不把網站 5xx 當成 proxy failure。
                    print(
                        f"[{WORKER_NAME}] SKIP server 5xx at {prefix}-{seq_num}: {exc}; "
                        "not counted as missing; continue next numeric SEQ",
                        file=sys.stderr,
                        flush=True,
                    )
                    numeric_status, data = "server_error_skipped", None
                    break

                except RetryableRequestError as exc:
                    if NETWORK_MODE == "direct":
                        raise RuntimeError(
                            f"direct retryable error at {prefix}-{seq_num}; "
                            f"chunk offset not committed: {exc}"
                        ) from exc

                    proxy_switches += 1
                    mark_proxy_failure(
                        proxy_collection, proxy_doc["_id"], WORKER_NAME, str(exc)
                    )
                    session.close()
                    try:
                        release_proxy(
                            proxy_collection, proxy_doc["_id"], WORKER_NAME
                        )
                    except Exception:
                        pass
                    print(
                        f"[{WORKER_NAME}] proxy/network error at {prefix}-{seq_num}; "
                        f"switching proxy ({proxy_switches}/{MAX_PROXY_SWITCHES_PER_SEQ})",
                        file=sys.stderr,
                        flush=True,
                    )
                    if proxy_switches >= MAX_PROXY_SWITCHES_PER_SEQ:
                        raise RuntimeError(
                            f"no usable proxy after {proxy_switches} switches "
                            f"at {prefix}-{seq_num}"
                        ) from exc
                    proxy_doc, session = wait_for_proxy(proxy_collection)
                    owns_session = True
                    continue

            if numeric_status == "server_error_skipped":
                # 5xx 既不是 found 也不是 missing；保留目前 consecutive_not_found。
                continue

            if numeric_status == "numeric_not_found":
                consecutive_not_found += 1
                if consecutive_not_found >= max_not_found:
                    stopped = True
                    print(
                        f"[{WORKER_NAME}] {prefix}: STOP at numeric seq={seq_num}; "
                        f"{consecutive_not_found} consecutive numeric SEQs missing",
                        flush=True,
                    )
                    break
                time.sleep(
                    random.uniform(NOT_FOUND_SLEEP_MIN, NOT_FOUND_SLEEP_MAX)
                )
                continue

            consecutive_not_found = 0
            success_count += 1

            if NETWORK_MODE == "proxy" and proxy_doc is not None:
                data["proxy_exit_ip"] = proxy_doc.get("exit_ip")
                data["proxy_country_code"] = proxy_doc.get("country_code")
            else:
                data["proxy_exit_ip"] = None
                data["proxy_country_code"] = None

            producer.send(
                KAFKA_RESULT_TOPIC,
                key=data["SEQ"].encode("utf-8"),
                value=data,
            ).get(timeout=30)
            produced += 1

            if NETWORK_MODE == "direct":
                time.sleep(
                    random.uniform(
                        DIRECT_CRAWL_SLEEP_MIN, DIRECT_CRAWL_SLEEP_MAX
                    )
                )
            else:
                time.sleep(random.uniform(CRAWL_SLEEP_MIN, CRAWL_SLEEP_MAX))

            if (
                COOLDOWN_SUCCESS_COUNT > 0
                and success_count % COOLDOWN_SUCCESS_COUNT == 0
            ):
                time.sleep(
                    random.uniform(COOLDOWN_SLEEP_MIN, COOLDOWN_SLEEP_MAX)
                )

        producer.flush()

        # 只有目前 chunk 完整成功且 prefix 尚未停止，才 relay 下一個 chunk。
        next_start = end_num + 1
        if not stopped and next_start <= max_seq_number:
            next_end = min(
                next_start + chunk_size - 1,
                max_seq_number,
            )
            next_job = {
                "job_type": "prefix_chunk",
                "prefix": prefix,
                "start_num": next_start,
                "end_num": next_end,
                "max_seq_number": max_seq_number,
                "chunk_size": chunk_size,
                "max_not_found_limit": max_not_found,
                "consecutive_not_found": consecutive_not_found,
            }
            producer.send(
                KAFKA_JOB_TOPIC,
                key=prefix.encode("utf-8"),
                value=next_job,
            ).get(timeout=30)
            producer.flush()
            print(
                f"[{WORKER_NAME}] relay next chunk {prefix} "
                f"{next_start}-{next_end} missing_out={consecutive_not_found}",
                flush=True,
            )

        print(
            f"[{WORKER_NAME}] finish chunk {prefix} {start_num}-{end_num}: "
            f"produced={produced} missing_out={consecutive_not_found} "
            f"stopped={stopped}",
            flush=True,
        )
        return {
            "produced": produced,
            "stopped": stopped,
            "last_seq_num": last_seq_num,
            "consecutive_not_found": consecutive_not_found,
        }

    finally:
        # main() 傳入的 session/proxy 由 main() 持有，成功後繼續使用，
        # 不在每個 chunk 結束時 release/close，避免每個 job 都離開 group。
        if owns_session:
            if NETWORK_MODE == "proxy" and proxy_doc is not None:
                try:
                    release_proxy(
                        proxy_collection, proxy_doc["_id"], WORKER_NAME
                    )
                except Exception:
                    pass
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass


def _parse_cutoff(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _extract_search_entries(html: str):
    """依搜尋頁 DOM 順序回傳唯一 SEQ。搜尋頁本身沒有上線日期。"""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        m = SEQ_RE.search(a.get("href", ""))
        if not m:
            continue
        seq = m.group(1).upper()
        if seq not in seen:
            seen.add(seq)
            found.append(seq)
    return found


def _get_search_page(session, page: int):
    params = {} if page == 1 else {YTOWER_SEARCH_PAGE_PARAM: page}
    response = session.get(YTOWER_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
    if looks_like_challenge(response):
        raise BlockedPageError(f"search page={page}: challenge/http {response.status_code}")
    if response.status_code >= 500:
        raise RetryableRequestError(f"search page={page}: HTTP {response.status_code}")
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "big5"
    return response


def _parse_publish_date(value: str):
    """把詳細頁的上線日期轉成 UTC datetime；無法解析時回傳 None。"""
    if not value:
        return None
    m = DATE_RE.search(str(value))
    if not m:
        return None
    try:
        return datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def incremental_discovery_job(job, producer, session):
    """
    搜尋頁只取得 SEQ；逐筆進入詳細頁取得上線日期。
    依搜尋頁順序由新到舊處理，連續 N 筆早於 cutoff 後立即停止，
    不再先把全部 SEQ 拆成 seq_list jobs。
    """
    cutoff = _parse_cutoff(job["cutoff_iso"])
    max_pages = int(job.get("search_max_pages", 100))
    old_streak_limit = max(
        1,
        int(job.get("old_streak_limit", YTOWER_INCREMENTAL_OLD_STREAK_LIMIT)),
    )

    seen = set()
    old_streak = 0
    checked = 0
    produced = 0
    stop_for_old_streak = False
    stop_for_test_limit = False

    for page in range(1, max_pages + 1):
        response = _get_search_page(session, page)
        entries = [seq for seq in _extract_search_entries(response.text) if seq not in seen]

        if not entries:
            print(
                f"[{WORKER_NAME}] incremental discovery stop page={page}: no new SEQ",
                flush=True,
            )
            break

        print(
            f"[{WORKER_NAME}] search page={page} entries={len(entries)} "
            f"checked={checked} produced={produced} "
            f"old_streak={old_streak}/{old_streak_limit}",
            flush=True,
        )

        for seq in entries:
            if _stop_requested:
                raise RuntimeError("shutdown requested")

            # TEST MODE only: cap incremental detail checks to avoid sending
            # hundreds of historical recipes during an E2E test.
            if TEST_MODE and checked >= TEST_LIMIT:
                stop_for_test_limit = True
                print(
                    f"[{WORKER_NAME}] incremental TEST MODE STOP: "
                    f"checked={checked} limit={TEST_LIMIT}",
                    flush=True,
                )
                break

            seen.add(seq)
            checked += 1

            m = re.fullmatch(r"([A-I]\d{2})-(\d{3,4})", seq)
            if not m:
                continue
            prefix, seq_num = m.group(1), int(m.group(2))

            status, detail_response, error, latency_ms = request_recipe_page(seq, session)
            if status == "blocked":
                raise BlockedPageError(f"{seq}: {error}")
            if status == "server_error":
                raise ServerResponseError(f"{seq}: {error}")
            if status == "retryable_error":
                raise RetryableRequestError(f"{seq}: {error}")
            if status == "not_found" or detail_response is None:
                print(f"[{WORKER_NAME}] incremental skip {seq}: not found", flush=True)
                continue

            try:
                data = parse_recipe_response(seq, seq_num, prefix, detail_response)
            except Exception as exc:
                raise RetryableRequestError(f"parse error {seq}: {exc}") from exc
            if not data:
                continue

            publish_dt = _parse_publish_date(data.get("上線日期", ""))

            if publish_dt is None:
                # 詳細頁仍無法取得日期時保守保留，避免誤停。
                old_streak = 0
                should_produce = True
                date_label = "unknown"
            elif publish_dt >= cutoff:
                old_streak = 0
                should_produce = True
                date_label = publish_dt.date().isoformat()
            else:
                old_streak += 1
                should_produce = False
                date_label = publish_dt.date().isoformat()

            print(
                f"[{WORKER_NAME}] incremental detail seq={seq} date={date_label} "
                f"recent={should_produce} old_streak={old_streak}/{old_streak_limit}",
                flush=True,
            )

            if should_produce:
                data["proxy_exit_ip"] = None
                data["proxy_country_code"] = None
                producer.send(
                    KAFKA_RESULT_TOPIC,
                    key=seq.encode(),
                    value=data,
                ).get(timeout=30)
                produced += 1

            if old_streak >= old_streak_limit:
                stop_for_old_streak = True
                print(
                    f"[{WORKER_NAME}] incremental discovery STOP: "
                    f"{old_streak} consecutive detail-page dates older than cutoff "
                    f"{cutoff.isoformat()} (page={page}, seq={seq})",
                    flush=True,
                )
                break

            delay = random.uniform(DIRECT_CRAWL_SLEEP_MIN, DIRECT_CRAWL_SLEEP_MAX)
            time.sleep(delay)

        if stop_for_old_streak or stop_for_test_limit:
            break

        time.sleep(random.uniform(CRAWL_SLEEP_MIN, CRAWL_SLEEP_MAX))

    crawl_run_id = str(job.get("crawl_run_id") or "").strip()
    if not crawl_run_id:
        raise RuntimeError("incremental_discovery job is missing crawl_run_id")

    # Run-specific completion record. Airflow uses this instead of MongoDB's
    # global collection-size delta, so delayed writes from an older run cannot
    # make a zero-result incremental crawl look like it found new recipes.
    status_payload = {
        "crawl_run_id": crawl_run_id,
        "mode": "incremental",
        "checked": checked,
        "produced": produced,
        "old_streak": old_streak,
        "old_streak_limit": old_streak_limit,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "worker": WORKER_NAME,
    }
    producer.send(
        KAFKA_INCREMENTAL_STATUS_TOPIC,
        key=crawl_run_id.encode("utf-8"),
        value=status_payload,
    ).get(timeout=30)
    producer.flush()

    print(
        f"[{WORKER_NAME}] incremental discovery finished "
        f"run_id={crawl_run_id} checked={checked} produced={produced} "
        f"old_streak={old_streak}/{old_streak_limit}",
        flush=True,
    )
    return produced


def seq_list_job(
    job,
    producer,
    proxy_collection=None,
    initial_proxy=None,
    initial_session=None,
):
    seqs = list(
        dict.fromkeys(
            str(x).upper()
            for x in job.get("seqs", [])
            if x
        )
    )
    proxy_doc, session = initial_proxy, initial_session
    owns_session = initial_session is None

    if NETWORK_MODE == "direct":
        session = session or build_direct_session()
    elif proxy_doc is None or session is None:
        proxy_doc, session = wait_for_proxy(proxy_collection)
        owns_session = True

    produced = 0
    try:
        for seq in seqs:
            if _stop_requested:
                raise RuntimeError("shutdown requested")

            m = re.fullmatch(r"([A-I]\d{2})-(\d{3,4})", seq)
            if not m:
                continue

            prefix, seq_num = m.group(1), int(m.group(2))
            status, response, error, latency_ms = request_recipe_page(
                seq, session
            )
            if status == "blocked":
                raise BlockedPageError(f"{seq}: {error}")
            if status == "retryable_error":
                raise RetryableRequestError(f"{seq}: {error}")
            if status == "not_found" or response is None:
                continue

            data = parse_recipe_response(
                seq, seq_num, prefix, response
            )
            if not data:
                continue

            if NETWORK_MODE == "proxy" and proxy_doc is not None:
                mark_proxy_success(
                    proxy_collection,
                    proxy_doc["_id"],
                    WORKER_NAME,
                    latency_ms=latency_ms,
                )
                data["proxy_exit_ip"] = proxy_doc.get("exit_ip")
                data["proxy_country_code"] = proxy_doc.get("country_code")
            else:
                data["proxy_exit_ip"] = None
                data["proxy_country_code"] = None

            producer.send(
                KAFKA_RESULT_TOPIC,
                key=seq.encode(),
                value=data,
            ).get(timeout=30)
            produced += 1

            delay = (
                random.uniform(
                    DIRECT_CRAWL_SLEEP_MIN,
                    DIRECT_CRAWL_SLEEP_MAX,
                )
                if NETWORK_MODE == "direct"
                else random.uniform(CRAWL_SLEEP_MIN, CRAWL_SLEEP_MAX)
            )
            time.sleep(delay)

        producer.flush()
        print(
            f"[{WORKER_NAME}] seq_list finished "
            f"seqs={len(seqs)} produced={produced}",
            flush=True,
        )
        return produced
    finally:
        if owns_session:
            if NETWORK_MODE == "proxy" and proxy_doc is not None:
                try:
                    release_proxy(
                        proxy_collection, proxy_doc["_id"], WORKER_NAME
                    )
                except Exception:
                    pass
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass


def create_consumer() -> KafkaConsumer:
    return KafkaConsumer(
        KAFKA_JOB_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=KAFKA_GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda x: json.loads(x.decode("utf-8")),
        max_poll_interval_ms=MAX_POLL_INTERVAL_MS,
        session_timeout_ms=30000,
        max_poll_records=1,
    )


def process_message(
    consumer,
    message,
    producer,
    proxy_collection=None,
    proxy_doc=None,
    session=None,
):
    job = message.value
    job_type = job.get("job_type", "prefix_chunk")

    if job_type == "incremental_discovery":
        active_session = session or (
            build_direct_session()
            if NETWORK_MODE == "direct"
            else None
        )
        owns_session = session is None
        if active_session is None:
            raise RuntimeError(
                "proxy discovery requires an active leased proxy session"
            )
        try:
            incremental_discovery_job(
                job, producer, active_session
            )
        finally:
            if owns_session:
                active_session.close()

    elif job_type == "seq_list":
        seq_list_job(
            job,
            producer,
            proxy_collection,
            proxy_doc,
            session,
        )

    elif job_type in {"prefix_chunk", "prefix_range"}:
        # prefix_range 保留相容性；新 Airflow 只會送第一個 prefix_chunk。
        crawl_job(
            job,
            producer,
            proxy_collection=proxy_collection,
            initial_proxy=proxy_doc,
            initial_session=session,
        )

    else:
        raise ValueError(f"unsupported job_type={job_type!r}")

    # relay/result producer 都已確認成功後，才 commit 目前 job offset。
    consumer.commit()


def main() -> int:
    if NETWORK_MODE not in {"direct", "proxy"}:
        raise ValueError(
            "CRAWLER_NETWORK_MODE must be direct or proxy"
        )

    mongo_client = None
    proxy_collection = None
    if NETWORK_MODE == "proxy":
        mongo_client, proxy_collection = get_proxy_collection()

    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(
            v, ensure_ascii=False
        ).encode("utf-8"),
        acks="all",
        retries=5,
    )

    consumer = None
    proxy_doc = None
    session = None

    try:
        while not _stop_requested:
            # Proxy worker 沒有 verified proxy 時完全不加入 consumer group。
            if NETWORK_MODE == "proxy" and (
                proxy_doc is None or session is None
            ):
                proxy_doc, session = wait_for_proxy(
                    proxy_collection
                )
                print(
                    f"[{WORKER_NAME}] proxy ready; "
                    "joining Kafka group",
                    flush=True,
                )
            elif NETWORK_MODE == "direct" and session is None:
                session = build_direct_session()
                print(
                    f"[{WORKER_NAME}] mode=direct; "
                    f"always available on {KAFKA_JOB_TOPIC}",
                    flush=True,
                )

            if consumer is None:
                consumer = create_consumer()

            try:
                records = consumer.poll(
                    timeout_ms=1000,
                    max_records=1,
                )
            except Exception as exc:
                print(
                    f"[{WORKER_NAME}] Kafka poll failed: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    consumer.close()
                except Exception:
                    pass
                consumer = None
                time.sleep(5)
                continue

            if not records:
                continue

            job_failed = False
            for _, messages in records.items():
                for message in messages:
                    try:
                        process_message(
                            consumer,
                            message,
                            producer,
                            proxy_collection=proxy_collection,
                            proxy_doc=proxy_doc,
                            session=session,
                        )
                    except Exception as exc:
                        job_failed = True
                        print(
                            f"[{WORKER_NAME}] job failed; "
                            "offset not committed; "
                            f"failed chunk will be re-delivered: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                        break
                if job_failed:
                    break

            if not job_failed:
                # 成功後保留同一 consumer + session/proxy，
                # 直接回 poll，不因每個 chunk LeaveGroup。
                continue

            # 失敗 job 不 commit。關閉 consumer，讓 Kafka 從最後
            # committed offset 重送「該 chunk」，而不是整個 prefix。
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    pass
                consumer = None

            # Direct 只重建 HTTP session；Proxy 則釋放目前 proxy，
            # 下一輪先取得新的 verified proxy 再加入 group。
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
                session = None

            if NETWORK_MODE == "proxy" and proxy_doc is not None:
                try:
                    release_proxy(
                        proxy_collection,
                        proxy_doc["_id"],
                        WORKER_NAME,
                    )
                except Exception:
                    pass
                proxy_doc = None

            time.sleep(5)

    finally:
        if consumer is not None:
            try:
                consumer.close()
            except Exception:
                pass

        if NETWORK_MODE == "proxy" and proxy_doc is not None:
            try:
                release_proxy(
                    proxy_collection,
                    proxy_doc["_id"],
                    WORKER_NAME,
                )
            except Exception:
                pass

        if session is not None:
            try:
                session.close()
            except Exception:
                pass

        try:
            producer.flush()
        except Exception:
            pass
        try:
            producer.close()
        except Exception:
            pass

        if mongo_client is not None:
            try:
                mongo_client.close()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
