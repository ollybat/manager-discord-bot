from pathlib import Path
import ast

root = Path(__file__).parent
paths = [root / 'bot.py', *sorted((root / 'grid_a1').glob('*.py'))]
for path in paths:
    text = path.read_text(encoding='utf-8')
    lowered = text.lower()
    assert 'more lines in file' not in lowered and 'todo: implement' not in lowered and 'pass  # placeholder' not in lowered, path
    ast.parse(text, filename=str(path))
print(f'AST validation passed for {len(paths)} files; no truncation placeholders found')
