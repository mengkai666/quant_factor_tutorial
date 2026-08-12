import ast
from pathlib import Path


def test_decision_dashboard_parses_with_python_310_grammar():
    source = Path("src/decision_dashboard.py").read_text(encoding="utf-8")

    ast.parse(source, filename="src/decision_dashboard.py", feature_version=(3, 10))