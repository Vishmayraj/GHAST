#!/usr/bin/env bash
# Fetches the labeled dataset used by ml/evaluation/datasets.py's
# load_gps_spoofing_mass().
#
# Source: "Synthetic GPS Dataset for AI-Based Spoofing Detection on
# Maritime Autonomous Surface Ships" (Ines Agrebi, IEEE DataPort,
# DOI 10.21227/9zcb-qb02). IEEE DataPort itself gates the download behind
# an institutional subscription, but the author's companion GitHub repo
# publishes the same file openly - that's what this pulls from.
set -euo pipefail
cd "$(dirname "$0")"
curl -fSL -o gps_spoofing_data.csv \
  "https://raw.githubusercontent.com/InesAg405/AI-Detection-Response-for-GPS-Spoofing-on-MASS/main/Data/gps_spoofing%20data.csv"
echo "Wrote $(wc -l < gps_spoofing_data.csv) lines to gps_spoofing_data.csv"
