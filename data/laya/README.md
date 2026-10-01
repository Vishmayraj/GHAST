# data/laya

Laya fine-tuning data. Empty on purpose.

The earlier contents (9,000 rows made by injecting synthetic spoofs into real windows) were removed. They are in git history at commit `c835f59` if you ever need to reproduce the model currently in use. New data comes from real windows and human labels:

```text
cd ml
python -m evaluation.laya_export queue --dsn $POSTGRES_DSN --source live \
    --start 2026-09-01 --end 2026-09-30 --out ../data/laya/review_queue.jsonl
# label windows: write labels.jsonl, one {"id": "...", "label": "..."} per line
python -m evaluation.laya_export build --queue ../data/laya/review_queue.jsonl \
    --labels ../data/laya/labels.jsonl --out-dir ../data/laya
```

`build` writes `train.jsonl`, `holdout.jsonl` and `manifest.json`, which `notebooks/laya_finetune_ghast_kaggle_2xT4.ipynb` reads. See `docs/laya_pattern_classifier.md` and `ImplementationPlans/04_Laya_Real_Labels.md`.
