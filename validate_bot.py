from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).parent / "grid_a1"
FILES = list(ROOT.glob("*.py"))

def main():
    for path in FILES:
        source = path.read_text(encoding="utf-8")
        if "[more lines in file" in source or "[18 more lines" in source or "[6 more lines" in source:
            raise SystemExit(f"placeholder found: {path}")
        ast.parse(source, filename=str(path))
    print(f"validated {len(FILES)} Grid A1 Python modules")

if __name__ == "__main__":
    main()
