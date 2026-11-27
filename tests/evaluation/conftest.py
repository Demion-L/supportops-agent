import sys
from pathlib import Path

# `evaluation/` лежит в корне репозитория (вне src/): делаем его импортируемым,
# не меняя pyproject.toml.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
