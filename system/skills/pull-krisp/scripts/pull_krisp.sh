#!/usr/bin/env bash
set -euo pipefail

LOOKBACK_DAYS="7"
SKIP_SUMMARY="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lookback-days)
      LOOKBACK_DAYS="${2:-}"
      shift 2
      ;;
    --skip-summary)
      SKIP_SUMMARY="1"
      shift
      ;;
    -h|--help)
      echo "Usage: pull_krisp.sh [--lookback-days N] [--skip-summary]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if ! [[ "$LOOKBACK_DAYS" =~ ^[0-9]+$ ]] || [[ "$LOOKBACK_DAYS" -lt 1 ]]; then
  echo "Invalid --lookback-days value: $LOOKBACK_DAYS" >&2
  exit 2
fi

KRISP_IMPORT="/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Krisp to obsidian/Scripts/krisp_import_all.py"
SUMMARIZER="/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Transcript summarizer/Scripts/transcript_summarizer.py"

echo "== Pull Krisp =="
echo "Lookback days: $LOOKBACK_DAYS"
echo

python3 "$KRISP_IMPORT" --lookback-days "$LOOKBACK_DAYS"

if [[ "$SKIP_SUMMARY" == "1" ]]; then
  echo
  echo "Summary step skipped."
  exit 0
fi

echo
echo "== Summarize new transcripts =="
python3 "$SUMMARIZER"
