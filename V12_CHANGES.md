# V12 changes

- First successful crawl remains FULL: A01-I10, 1..MAX_SEQ_NUMBER.
- Added MongoDB `crawl_state` checkpoint. Checkpoint advances only after crawler queue, Mongo writer, ETL and MySQL validation succeed.
- Airflow checks daily and only runs when 7 days have elapsed since the last successful checkpoint.
- Incremental run uses `recipe-search.asp` with checkpoint minus one day overlap.
- Added Kafka `incremental_discovery` and `seq_list` job types.
- Added free TW proxy sources: Proxifly, HProxy, Proxmint, while preserving existing sources and validation.
- Added optional LumiProxy/Croxy endpoint environment variables; secrets remain outside Git in `.env`.
- Normalized shell scripts to LF line endings for GCP/Linux Bash.
