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
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "ytower-crawler-group")
WORKER_NAME = os.getenv("WORKER_NAME", "crawler-worker")
NETWORK_MODE = os.getenv("CRAWLER_NETWORK_MODE", "proxy").strip().lower()

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "20"))
REQUEST_RETRIES = int(os.getenv("REQUEST_RETRIES", "3"))
MAX_PROXY_SWITCHES_PER_SEQ = int(os.getenv("MAX_PROXY_SWITCHES_PER_SEQ", "5"))
PROXY_WAIT_SECONDS = int(os.getenv("PROXY_WAIT_SECONDS", "15"))
MAX_NOT_FOUND_LIMIT = int(os.getenv("MAX_NOT_FOUND_LIMIT", "50"))
FULL_CRAWL_CHUNK_SIZE = int(os.getenv("FULL_CRAWL_CHUNK_SIZE", "250"))
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
      1-250 成功後才 enqueue 251-500。
    因此不會預先把後續 chunk 全部排入 Kafka。

    consecutive_not_found 會由 job 帶到下一個 chunk，保留跨 chunk 的
    「連續 50 個 numeric SEQ 不存在就停止」語意。
    """
    prefix = str(job["prefix"])
    start_num = max(1, int(job.get("start_num", 1)))
    requested_end = int(job.get("end_num", start_num + FULL_CRAWL_CHUNK_SIZE - 1))
    max_seq_number = int(job.get("max_seq_number", requested_end))
    chunk_size = max(1, int(job.get("chunk_size", FULL_CRAWL_CHUNK_SIZE)))
    end_num = min(requested_end, max_seq_number)
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
    """Return [(SEQ, datetime|None)]. Missing dates are kept deliberately to avoid false negatives."""
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        m = SEQ_RE.search(href)
        if not m:
            continue
        seq = m.group(1).upper()
        container = a
        for _ in range(4):
            if container.parent is None:
                break
            container = container.parent
            text = container.get_text(" ", strip=True)
            dm = DATE_RE.search(text)
            if dm:
                try:
                    found[seq] = datetime(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)), tzinfo=timezone.utc)
                except ValueError:
                    found.setdefault(seq, None)
                break
        else:
            found.setdefault(seq, None)
        found.setdefault(seq, None)
    return list(found.items())


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


def incremental_discovery_job(job, producer, session):
    cutoff = _parse_cutoff(job["cutoff_iso"])
    max_pages = int(job.get("search_max_pages", 100))
    selected, seen = [], set()
    old_pages = 0
    for page in range(1, max_pages + 1):
        response = _get_search_page(session, page)
        entries = _extract_search_entries(response.text)
        new_entries = [(seq, dt) for seq, dt in entries if seq not in seen]
        if not new_entries:
            print(f"[{WORKER_NAME}] incremental discovery stop page={page}: no new SEQ", flush=True)
            break
        all_dated = all(dt is not None for _, dt in new_entries)
        page_has_recent = False
        for seq, dt in new_entries:
            seen.add(seq)
            # Unknown date is included on purpose: overlap + Mongo upsert is safer than dropping a recipe.
            if dt is None or dt >= cutoff:
                selected.append(seq)
                page_has_recent = True
        if all_dated and not page_has_recent:
            old_pages += 1
        else:
            old_pages = 0
        print(f"[{WORKER_NAME}] search page={page} entries={len(new_entries)} selected_total={len(selected)}", flush=True)
        if old_pages >= YTOWER_SEARCH_OLD_PAGE_STOP:
            print(f"[{WORKER_NAME}] stop after {old_pages} fully-old dated pages", flush=True)
            break
        time.sleep(random.uniform(CRAWL_SLEEP_MIN, CRAWL_SLEEP_MAX))

    for i in range(0, len(selected), YTOWER_SEARCH_CHUNK_SIZE):
        seqs = selected[i:i + YTOWER_SEARCH_CHUNK_SIZE]
        producer.send(KAFKA_JOB_TOPIC, key=f"incremental-{i//YTOWER_SEARCH_CHUNK_SIZE}".encode(),
                      value={"job_type": "seq_list", "seqs": seqs, "cutoff_iso": job["cutoff_iso"]}).get(timeout=30)
    producer.flush()
    print(f"[{WORKER_NAME}] incremental discovery selected={len(selected)} jobs={(len(selected)+YTOWER_SEARCH_CHUNK_SIZE-1)//YTOWER_SEARCH_CHUNK_SIZE}", flush=True)
    return len(selected)


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
