# History Compaction

Separate recent hot dashboard rows from cold archived payloads so SQLite growth from `snapshots.raw_json` and `signals.feature_json` does not dominate normal dashboard and maintenance queries.
