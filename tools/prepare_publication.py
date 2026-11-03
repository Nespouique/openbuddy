"""Prepare reviewed public text files for publication through the GitHub MCP."""

import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def files():
    selected = {
        ".gitignore": ROOT / ".gitignore",
        "AGENTS.md": ROOT / "distribution/public/AGENTS.md",
        "README.md": ROOT / "README.md",
        "THIRD_PARTY.md": ROOT / "THIRD_PARTY.md",
        ".github/workflows/release.yml": ROOT / ".github/workflows/release.yml",
        "plugin/openbuddy/index.js": ROOT / "integration/openbuddy/dist/index.js",
        "plugin/openbuddy/package.json": ROOT / "integration/openbuddy/dist/package.json",
        "plugin/openbuddy/LICENSES.txt": ROOT / "integration/openbuddy/dist/LICENSES.txt",
    }
    patterns = ("c6lcd/*.py", "pc/*.py", "pc/requirements*.txt", "tools/*.py", "tests/*.py",
                "integration/openbuddy/src/*.ts", "integration/openbuddy/test/*.ts",
                "integration/openbuddy/assets/*", "integration/openbuddy/*.json",
                "integration/openbuddy/build.mjs", "distribution/workplace/*.md", "distribution/public/*.md",
                "docs/animations.md", "docs/ble-prototype.md", "docs/opencode-events.md", "docs/hardware.md")
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            if path.is_file() and not path.is_symlink():
                selected[path.relative_to(ROOT).as_posix()] = path
    return selected


def prepare():
    result = []
    for name, path in sorted(files().items()):
        content = path.read_text(encoding="utf-8")
        # Device identifiers in examples/tests are placeholders in the public copy.
        content = content.replace("AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF")
        content = content.replace("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff")
        content = content.replace("AABBCCDDEEFF", "AABBCCDDEEFF")
        if re.search(r"C:[\\/]+Users[\\/]+[A-Za-z0-9._-]+[\\/]", content, re.IGNORECASE):
            raise ValueError("Personal filesystem path in public file: " + name)
        if re.search(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|[-]{5}BEGIN [A-Z ]*PRIVATE KEY[-]{5})", content):
            raise ValueError("Possible credential in public file: " + name)
        result.append({"path": name, "content": content})
    return result


def main():
    parser = argparse.ArgumentParser(description="Prepare the allowlisted public-source payload")
    parser.add_argument("--output", type=Path, default=ROOT / "build/github-source.json")
    args = parser.parse_args()
    payload = prepare()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"files": payload}, ensure_ascii=False), encoding="utf-8")
    print("Prepared", len(payload), "public text files:", args.output)
    for item in payload:
        print(item["path"], hashlib.sha256(item["content"].encode()).hexdigest()[:12])


if __name__ == "__main__":
    main()
