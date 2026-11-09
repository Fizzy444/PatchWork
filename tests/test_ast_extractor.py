"""
Unit tests for AST-based Context Extractor (Component C Pro-level feature).
"""

from patchwork.extractor.ast_extractor import ASTContextExtractor

SAMPLE_PYTHON_SOURCE = """import os
import sys
from typing import List, Optional

GLOBAL_LIMIT = 100

def helper_func():
    return 42

def process_data(items: List[int]) -> int:
    # A function that has an off-by-one or division bug
    total = 0
    for item in items:
        total += item
    return total / len(items)

class Calculator:
    def multiply(self, a, b):
        return a * b
"""


def test_ast_extractor_isolates_target_function_and_imports():
    # Target line 14 is inside process_data
    ctx = ASTContextExtractor.extract_context(SAMPLE_PYTHON_SOURCE, "data_service.py", 14)
    
    assert ctx.file_path == "data_service.py"
    assert ctx.enclosing_symbol == "process_data"
    assert ctx.symbol_type == "function"
    assert "def process_data" in ctx.function_code
    assert "helper_func" not in ctx.function_code
    assert "class Calculator" not in ctx.function_code

    # Verify imports were gathered
    assert any("import os" in imp for imp in ctx.module_imports)
    assert any("from typing import List, Optional" in imp for imp in ctx.module_imports)

    # Check focused prompt representation
    focused = ctx.get_focused_prompt_context()
    assert "=== Module Imports ===" in focused
    assert "process_data" in focused


def test_ast_extractor_class_method():
    # Target line 19 is inside Calculator.multiply
    ctx = ASTContextExtractor.extract_context(SAMPLE_PYTHON_SOURCE, "data_service.py", 19)
    assert ctx.enclosing_symbol == "multiply"
    assert "def multiply" in ctx.function_code
