#!/usr/bin/env bash
# Bash mirror of run_delhivery.py — logs to logs/run_delhivery_<ts>.log
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${API_URL:-http://localhost:8000}"
TS="$(date +%Y%m%d_%H%M%S)"
LOG="logs/run_delhivery_${TS}.log"
mkdir -p logs
exec > >(tee -a "$LOG") 2>&1
echo "API $BASE  log=$LOG  started $TS"

health(){ curl -s "$BASE/health" | python3 -m json.tool; }
reset(){ curl -s -X POST "$BASE/documents/reset" | python3 -m json.tool; }

echo "=== health before ==="; health || { echo "is uvicorn running? python3 -m uvicorn app:app --port 8000 --host 0.0.0.0"; exit 1; }
echo "=== reset ==="; reset || true

echo "=== uploads ==="
for fn in 01-delhivery-prospectus-2022-excerpt.pdf 02-delhivery-annual-report-fy24-excerpt.pdf 03-delhivery-q4-fy24-earnings-presentation.pdf; do
  echo "upload $fn"
  curl -s -X POST "$BASE/documents/upload" -F "file=@$ROOT/starter-datasets/delhivery/$fn" | python3 -m json.tool
done

run(){ local fn="$1" mp="$2"; echo "=== pipeline/run $fn max_pages=$mp ==="; curl --max-time 600 -s -X POST "$BASE/pipeline/run?filename=$fn&max_pages=$mp" | python3 -m json.tool | tee "logs/run_${fn}_${TS}.json"; echo; sleep 2; }
run 01-delhivery-prospectus-2022-excerpt.pdf 30
run 02-delhivery-annual-report-fy24-excerpt.pdf 100
run 03-delhivery-q4-fy24-earnings-presentation.pdf 27

echo "=== health after ==="; health
echo "=== buckets ==="; curl -s "$BASE/buckets" | python3 -m json.tool
echo "=== facts total ==="; curl -s "$BASE/facts" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
curl -s "$BASE/facts" > "logs/facts_${TS}.json"
echo "=== reconciliation by status ==="
for s in CORROBORATED GENUINE_CONTRADICTION RECONCILED_BY_CONTEXT; do
  echo "--- $s ---"
  curl -s "$BASE/reconciliation?status=$s" | python3 -m json.tool | head -n 80
  curl -s "$BASE/reconciliation?status=$s" > "logs/reconciliation_${s}_${TS}.json"
done
echo "=== case4 ==="
for fn in 01-delhivery-prospectus-2022-excerpt.pdf 02-delhivery-annual-report-fy24-excerpt.pdf 03-delhivery-q4-fy24-earnings-presentation.pdf; do
  echo "--- $fn ---"
  curl -s "$BASE/case4-showcase?filename=$fn" | python3 -m json.tool | head -n 30
done
echo "Done log=$LOG"
