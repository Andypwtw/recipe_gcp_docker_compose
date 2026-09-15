from __future__ import annotations


def build_crawl_jobs() -> list[dict]:
    """
    Return the crawl jobs that should be distributed to the four V9 workers.

    This project intentionally DOES NOT invent YTower selectors or URL rules.
    Move the already-verified YTower URL/SEQ discovery logic here.

    Each job must contain at least one stable identifier:
        job_id
        SEQ
        url
        食譜網址

    Example:
        {
            "job_id": "12345",
            "SEQ": "12345",
            "url": "https://www.ytower.com.tw/recipe/..."
        }
    """
    raise NotImplementedError(
        "Move the verified YTower job-discovery logic into "
        "crawler.ytower_crawler.build_crawl_jobs()."
    )


def crawl_job(
    job: dict,
    session,
) -> dict:
    """
    Crawl ONE recipe job.

    `session` is already configured by crawler/worker.py to bind outbound
    HTTP connections to that worker's PRIVATE_IP_n. Use this session for
    every HTTP request so Linux policy routing can select the correct NIC.

    The returned recipe must keep the V8/V9 contract:
        SEQ
        食譜名稱
        上線日期
        關鍵字
        食譜網址
        材料
        做法步驟

    The production CSS selectors are intentionally not guessed here.
    """
    raise NotImplementedError(
        "Move the verified single-recipe crawler logic into "
        "crawler.ytower_crawler.crawl_job(job, session)."
    )
