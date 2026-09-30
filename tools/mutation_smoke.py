"""Small, curated mutation sample; runs copies, never edits production files."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ("duplicate_resolution_calls", "list(dict.fromkeys((i.type, i.value) for i in request.identifiers))", "[(i.type, i.value) for i in request.identifiers]"),
    ("omit_merged_accounts", "for uid in [result.data.user_id, *result.data.merged_ids]", "for uid in [result.data.user_id]"),
    ("disable_retry", "max_attempts: int = 3", "max_attempts: int = 1"),
    ("ignore_retry_after", "delay = exc.retry_after", "delay = 0"),
    ("disable_cooldown", "self._cooldowns[scope] = max(self._cooldowns[scope], loop.time() + max(0, delay))", "self._cooldowns[scope] = 0.0"),
    ("disable_backoff", "self.settings.backoff * 2 ** (attempts - 1) * random.uniform(0.5, 1.5)", "0.0"),
    ("disable_concurrency_limit", "asyncio.Semaphore(self.settings.concurrency)", "asyncio.Semaphore(1000)"),
    ("ignore_call_timeout", "asyncio.timeout(self.settings.call_timeout)", "asyncio.timeout(10)"),
    ("ignore_request_deadline", "loop().time() + self.settings.request_timeout", "loop().time() + 10"),
    ("hide_partial_status", 'result.status = "partial"', 'result.status = "complete"'),
    ("hide_not_found_status", 'status="not_found"', 'status="complete"'),
    ("reverse_event_order", "(event.event_time, event.tracking_id))", "(event.event_time, event.tracking_id), reverse=True)"),
    ("constant_backoff", "2 ** (attempts - 1)", "1"),
    ("ignore_jitter", "random.uniform(0.5, 1.5)", "1.0"),
    ("wrong_existing_tracking_source", "tracking_id=tid)", "tracking_id=128934)"),
    ("wrong_tracking_provenance", "tracking_id=tid)", "tracking_id=0)"),
]


def run(cwd):
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                          cwd=cwd, capture_output=True, text=True, timeout=10)


def main():
    output = ROOT / "reports" / "mutation-smoke"
    output.mkdir(parents=True, exist_ok=True)
    source = (ROOT / "app" / "collector.py").read_text()
    results = []
    with tempfile.TemporaryDirectory(prefix="collector-mutations-") as directory:
        work = Path(directory)
        shutil.copytree(ROOT / "app", work / "app", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "tests", work / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        baseline = run(work)
        (output / "baseline.log").write_text(baseline.stdout + baseline.stderr)
        if baseline.returncode:
            raise RuntimeError("Baseline failed; see reports/mutation-smoke/baseline.log")
        for name, old, new in MUTATIONS:
            if source.count(old) != 1:
                raise RuntimeError(f"Mutation {name}: expected exactly one match")
            (work / "app" / "collector.py").write_text(source.replace(old, new, 1))
            shutil.rmtree(work / "app" / "__pycache__", ignore_errors=True)
            try:
                result = run(work)
                status = "killed" if result.returncode else "survived"
                log = result.stdout + result.stderr
            except subprocess.TimeoutExpired:
                status, log = "timeout", "Exceeded mutation subprocess limit (10 seconds)"
            (output / f"{name}.log").write_text(log)
            results.append({"mutation": name, "status": status, "old": old, "new": new})
            print(f"{name}: {status}", flush=True)
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print({status: sum(r["status"] == status for r in results)
           for status in ("killed", "survived", "timeout")})
    if any(result["status"] != "killed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
