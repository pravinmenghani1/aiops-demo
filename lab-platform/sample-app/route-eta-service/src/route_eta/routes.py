import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def load_route(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())
