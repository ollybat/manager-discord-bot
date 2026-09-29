from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).parent / "grid_a1"
FILES = sorted(ROOT.glob("*.py"))


def _fail(message):
    print(f"::error::{message}", flush=True)
    raise SystemExit(1)


def main():
    paths = [Path(__file__).resolve().parent / "bot.py", *FILES]
    marker = "[" + "more lines in file"
    for path in paths:
        source = path.read_text(encoding="utf-8")
        lowered = source.lower()
        if marker in lowered or "todo: implement" in lowered or "pass  # placeholder" in lowered:
            _fail(f"placeholder found: {path}")
        if any(token in lowered for token in ("drop table", "cloudflared", "ngrok", "socat", "ssh -r")):
            _fail(f"prohibited destructive/relay token found: {path}")
        try:
            ast.parse(source, filename=str(path))
        except SyntaxError as error:
            _fail(f"Python syntax error in {path}:{error.lineno}: {error.msg}")
    print(f"validated {len(paths)} Python modules; AST and safety-marker checks passed")


if __name__ == "__main__":
    main()
