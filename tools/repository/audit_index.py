"""Audit staged Git bytes for size, generated paths and common secrets; never print values."""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private_key": rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    "github_token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b",
    "aws_access_key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "slack_token": rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
    "credential_in_url": rb"https?://[^\s/:@]+:[^\s/@]{12,}@",
}
GENERATED = re.compile(r"(?:^ros2_ws/(?:build|install|log)[^/]*/|^unreal/RMSim/(?:Content|Binaries|Intermediate|Saved|DerivedDataCache)/|(?:^|/)\.pixi/)")


def audit() -> dict:
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    records = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root).split(b"\0")
    findings, items = [], []
    batch = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert batch.stdin is not None and batch.stdout is not None
    for record in filter(None, records):
        meta, raw_path = record.split(b"\t", 1)
        mode, oid, stage = meta.split()
        path = raw_path.decode("utf-8")
        if stage != b"0" or mode == b"160000":
            findings.append({"path": path, "kind": "unmerged_or_embedded_repository"})
            continue
        batch.stdin.write(oid + b"\n"); batch.stdin.flush()
        header = batch.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise RuntimeError("Unexpected Git batch response")
        data = batch.stdout.read(int(header[2]))
        if batch.stdout.read(1) != b"\n":
            raise RuntimeError("Invalid Git batch framing")
        items.append({"path": path, "bytes": len(data), "blob": oid.decode()})
        if len(data) > 10 * 1024 * 1024:
            findings.append({"path": path, "kind": "over_10_MiB", "bytes": len(data)})
        if GENERATED.search(path):
            findings.append({"path": path, "kind": "generated_directory"})
        if Path(path).name in {".env", "local.env", "credentials.json", "secrets.json"} or Path(path).suffix.lower() in {".pem", ".key", ".pfx", ".p12"}:
            findings.append({"path": path, "kind": "credential_filename"})
        for kind, pattern in PATTERNS.items():
            for match in re.finditer(pattern, data):
                findings.append({"path": path, "kind": kind, "line": data[:match.start()].count(b"\n") + 1})
    batch.stdin.close(); batch.stdout.close()
    if batch.wait() != 0:
        raise RuntimeError("git cat-file failed")
    return {"status": "passed" if not findings else "failed",
            "scope": "actual staged blobs; common-secret patterns, not exhaustive secret discovery",
            "files": len(items), "total_bytes": sum(x["bytes"] for x in items),
            "largest": sorted(items, key=lambda x: x["bytes"], reverse=True)[:10], "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit()
    text = json.dumps(report, indent=2, ensure_ascii=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
