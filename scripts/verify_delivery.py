"""Re-run reproducible evidence on disposable copies; never changes user data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "verification"
NAMES = ("minagent/runtime.py", "minagent/store.py")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    work = ROOT / "artifacts" / ("transaction-" + uuid.uuid4().hex)
    work.mkdir(parents=True)
    ledger = ["SELF-REVIEW TRANSACTION — NOT A REAL LLM RUN", "Changes: Runtime.loop memory refresh; Store.sessions first-seq title."]
    original_hashes = {name: sha(ROOT / name) for name in NAMES}

    def clone(label):
        dest = work / label
        dest.mkdir()
        for directory in ("minagent", "tests"):
            shutil.copytree(ROOT / directory, dest / directory, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "pyproject.toml", dest / "pyproject.toml")
        (dest / "artifacts").mkdir()
        return dest

    def run(label, command, cwd):
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        ledger.extend(["\n" + label, "CWD: " + str(cwd), "COMMAND: " + json.dumps(command), "EXIT: " + str(result.returncode), "STDOUT:\n" + result.stdout, "STDERR:\n" + result.stderr])
        (OUT / "VERIFICATION.txt").write_text("\n".join(ledger),encoding="utf-8")
        return result.returncode

    selector = "memory_refreshes_between_model_steps or session_title_is_first_message"

    def check(label, cwd):
        return run(label, [sys.executable, "-m", "pytest", "tests/test_agent.py", "-q", "--tb=short", "-k", selector, "--basetemp", str(cwd / "artifacts" / ("pytest-" + uuid.uuid4().hex))], cwd)

    baseline = clone("baseline")
    restored = clone("restored")
    reconstructed = clone("reconstructed")
    bash = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
    for dest in (baseline, reconstructed):
        assert run("PREPARE BASELINE COPY", [bash, str(OUT / "ROLLBACK.sh"), str(dest)], ROOT) == 0
    assert check("BASELINE", baseline) == 1
    assert check("MODIFIED", ROOT) == 0
    assert run("ROLLBACK EXECUTION", [bash, str(OUT / "ROLLBACK.sh"), str(restored)], ROOT) == 0
    for name in NAMES:
        assert sha(restored / name) == sha(baseline / name)
        ledger.append("ROLLBACK HASH MATCH " + name + " " + sha(restored / name))
    assert check("ROLLBACK", restored) == 1
    assert run("PATCH RECONSTRUCTION", ["git", "apply", str(OUT / "CHANGE.patch")], reconstructed) == 0
    for name in NAMES:
        assert sha(reconstructed / name) == original_hashes[name]
        assert sha(ROOT / name) == original_hashes[name]
        ledger.append("MODIFIED/PATCH SHA " + name + " " + original_hashes[name])
    code = run("FULL OFFLINE SUITE", [sys.executable, "-m", "pytest", "-q", "--tb=short", "--basetemp", str(work / "full-suite")], ROOT)
    assert code == 0
    ledger.extend(["\nROLES:", "MODIFIED_FILE: " + str(ROOT / NAMES[0]), "SUPPLEMENTAL_MODIFIED_FILE: " + str(ROOT / NAMES[1]), "DIFF_FILE: " + str(OUT / "CHANGE.patch"), "VERIFICATION: " + str(OUT / "VERIFICATION.txt"), "ROLLBACK: " + str(OUT / "ROLLBACK.sh"), "\nObserved: BASELINE 2 failed; MODIFIED 2 passed; ROLLBACK 2 failed.", "Live model acceptance is SKIPPED without explicit RUN_LIVE=1 and credentials."])
    verification = OUT / "VERIFICATION.txt"
    verification.write_text("\n".join(ledger), encoding="utf-8")
    for path in [ROOT / NAMES[0], OUT / "CHANGE.patch", verification, OUT / "ROLLBACK.sh"]:
        data = path.read_bytes()
        print(str(path.resolve()), len(data), hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    main()
