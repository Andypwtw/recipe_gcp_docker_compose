# V12 - Full once, then checkpoint incremental updates

## Crawl policy
- First successful run: FULL A01-I10, numeric 1..MAX_SEQ_NUMBER.
- Checkpoint is written only after Kafka -> MongoDB -> ETL -> MySQL validation succeeds.
- Airflow checks daily. If fewer than 7 days have elapsed since the last successful checkpoint, the run is short-circuited.
- When due, one `incremental_discovery` job reads `recipe-search.asp` with a one-day overlap.
- Search entries newer than the cutoff are split into `seq_list` Kafka jobs so all crawler workers can share the work.
- If a search entry has no parseable date, it is included rather than dropped; MongoDB SEQ upsert makes overlap safe.
- 403/429/CAPTCHA/challenge is never counted as missing.

## Proxy sources
Free discovery now includes existing ProxyScrape/Databay/proxio sources plus Proxifly TW, HProxy TW and Proxmint TW API. Every candidate still has to pass TCP, HTTPS egress, GeoIP=TW and latency checks before leasing.

LumiProxy and Croxy are optional authenticated providers. Generate proxy endpoints in the provider dashboard and place them only in local `.env` as `LUMIPROXY_PROXY_URLS` / `CROXY_PROXY_URLS`. Do not commit credentials.

Public proxies are untrusted. Do not send credentials, cookies, tokens, personal data or authenticated sessions through them. Site 403/CAPTCHA is treated as a stop/cooldown signal, not something to bypass.
