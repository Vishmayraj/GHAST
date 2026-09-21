# data/research_datasets/

Public, labeled research datasets used for training and evaluation.

## gps_spoofing_mass/

"Synthetic GPS Dataset for AI-Based Spoofing Detection on Maritime Autonomous Surface Ships" (Ines Agrebi, IEEE DataPort, DOI [10.21227/9zcb-qb02](https://dx.doi.org/10.21227/9zcb-qb02)). 6,351 labeled AIS observations (5,401 Normal / 950 Spoofed) across 4,892 vessels. IEEE DataPort itself gates the download behind an institutional subscription; the author's companion GitHub repo publishes the same file openly, which is what we actually pull from.

Fetch it with:
```
data/research_datasets/gps_spoofing_mass/fetch.sh
```

Loaded by `ml/evaluation/datasets.py`'s `load_gps_spoofing_mass()`. Note: despite "track" in the paper's title, this is point-level labeled data (~1.3 observations/vessel on average), not long per-vessel trajectories - fine for the evaluation harness, but something to know before trying to train a sequence model directly on it.

## Still to source

The HLD (section 5) also calls for real-vs-simulated spoofed AIS track research data. The closest match found so far is Pohontu et al., ["Detection of spoofed AIS: Simulated tracks vs. real maritime data"](https://doi.org/10.33436/v35i1y202503) (Black Sea vessel data, 2025) - no public dataset download found alongside the paper yet, so it isn't wired into the loaders. `ml/evaluation/datasets.py`'s `DATASET_LOADERS` registry is where a loader for it would go once the data's actually in hand.
