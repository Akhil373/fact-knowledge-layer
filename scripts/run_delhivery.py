#!/usr/bin/env python3
"""One-click Delhivery deep test — logs every step to logs/run_delhivery_<ts>.log + console."""
import os, sys, json, time, pathlib, logging
import requests

BASE = os.getenv("API_URL", "http://localhost:8000")
ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "starter-datasets" / "delhivery"
LOGDIR = ROOT / "logs"
LOGDIR.mkdir(exist_ok=True)
ts = time.strftime("%Y%m%d_%H%M%S")
LOGFILE = LOGDIR / f"run_delhivery_{ts}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOGFILE), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

FILES = [
    ("01-delhivery-prospectus-2022-excerpt.pdf", 30),
    ("02-delhivery-annual-report-fy24-excerpt.pdf", 100),
    ("03-delhivery-q4-fy24-earnings-presentation.pdf", 27),
]

def get(path, timeout=10):
    r = requests.get(f"{BASE}{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()

def post(path, timeout=600, **kw):
    r = requests.post(f"{BASE}{path}", timeout=timeout, **kw)
    r.raise_for_status()
    return r.json()

def main():
    log.info(f"API {BASE} log={LOGFILE}")
    # 0. health
    try:
        h = get("/health")
        log.info(f"health before: {json.dumps(h, indent=2)}")
    except Exception as e:
        log.error(f"health failed — is uvicorn running? {e}")
        log.error("start with: python3 -m uvicorn app:app --port 8000 --host 0.0.0.0")
        sys.exit(1)

    # reset
    try:
        r = post("/documents/reset", timeout=10)
        log.info(f"reset: {r}")
    except Exception as e:
        log.warning(f"reset failed (may not exist yet): {e}")

    # upload
    for fn, _ in FILES:
        p = DATA / fn
        if not p.exists():
            log.error(f"missing {p}")
            sys.exit(1)
        log.info(f"upload {fn}")
        with open(p, "rb") as f:
            res = post("/documents/upload", files={"file": (fn, f, "application/pdf")}, timeout=30)
        log.info(f"  -> {res}")

    # run pipeline deep
    for fn, mp in FILES:
        log.info(f"pipeline/run {fn} max_pages={mp} (this hangs 30-90s per file, Groq qwen 1000 OTPM)…")
        t0 = time.time()
        try:
            res = post(f"/pipeline/run?filename={fn}&max_pages={mp}", timeout=600)
            dt = time.time() - t0
            log.info(f"  done {dt:.1f}s: {json.dumps(res, indent=2)}")
            # also write raw json
            (LOGDIR / f"run_{fn}_{ts}.json").write_text(json.dumps(res, indent=2))
        except Exception as e:
            log.error(f"  failed after {time.time()-t0:.1f}s: {e}")
            if hasattr(e, 'response') and e.response is not None:
                log.error(e.response.text[:2000])

    # checks
    log.info("=== health after ===")
    log.info(json.dumps(get("/health"), indent=2))
    log.info("=== buckets ===")
    log.info(json.dumps(get("/buckets"), indent=2))

    facts = get("/facts")
    log.info(f"facts total={len(facts)}")
    (LOGDIR / f"facts_{ts}.json").write_text(json.dumps(facts, indent=2))
    # per-bucket summary
    from collections import Counter
    cnt = Counter(f"{x['canonical_metric']}\t{x['source_doc_id'][:14]}\t{x['normalized_period']}" for x in facts)
    for k, c in cnt.most_common(30):
        log.info(f"  {c:2d} {k}")

    for status in ["CORROBORATED", "GENUINE_CONTRADICTION", "RECONCILED_BY_CONTEXT"]:
        rec = get(f"/reconciliation?status={status}") if True else []
        try:
            rec = get(f"/reconciliation?status={status}")
        except Exception as e:
            rec = []
            log.error(f"reconciliation {status} err {e}")
        log.info(f"reconciliation {status}: {len(rec)}")
        if rec:
            log.info(json.dumps(rec[0], indent=2))
            (LOGDIR / f"reconciliation_{status}_{ts}.json").write_text(json.dumps(rec, indent=2))

    all_rec = get("/reconciliation")
    log.info(f"reconciliation ALL: {len(all_rec)}")
    (LOGDIR / f"reconciliation_all_{ts}.json").write_text(json.dumps(all_rec, indent=2))

    # case4
    for fn, _ in FILES:
        try:
            c4 = get(f"/case4-showcase?filename={fn}")
            log.info(f"case4 {fn}: pages={c4['num_pages']} jumps={c4['page_jumps'][:3]} footnotes={c4['footnote_table_pages'][:3]}")
        except Exception as e:
            log.error(f"case4 {fn} err {e}")

    log.info(f"Done. Full log: {LOGFILE}")

if __name__ == "__main__":
    main()
