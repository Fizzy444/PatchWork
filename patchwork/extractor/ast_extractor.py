"""
AST-based context extractor: Parses Python source files using AST to extract
only the broken function/class and relevant module imports.
"""

import ast
from typing import Optional, List, Tuple
from pydantic import BaseModel


class ASTContext(BaseModel):
    file_path: str
    target_line: int
    enclosing_symbol: Optional[str] = None
    symbol_type: Optional[str] = None  # "function", "async_function", "class", "module"
    start_line: int
    end_line: int
    function_code: str
    module_imports: List[str] = []
    full_file_code: str

    def get_focused_prompt_context(self) -> str:
        """Generates a compact, highly focused snippet for LLM diagnosis."""
        imports_block = "\n".join(self.module_imports) if self.module_imports else "# No external imports"
        return (
            f"=== File: {self.file_path} ===\n"
            f"=== Module Imports ===\n{imports_block}\n\n"
            f"=== Broken {self.symbol_type or 'Block'} ({self.enclosing_symbol or 'Scope'}) "
            f"[Lines {self.start_line}-{self.end_line}] ===\n"
            f"{self.function_code}\n"
        )


class ASTContextExtractor:
    @staticmethod
    def extract_context(source_code: str, file_path: str, target_line: int) -> ASTContext:
        """
        Extracts only the failing function/class and module imports from source_code.
        Falls back gracefully if the file contains syntax errors or is not Python.
        """
        lines = source_code.splitlines(keepends=True)
        total_lines = len(lines)

        try:
            tree = ast.parse(source_code, filename=file_path)
        except Exception:
            # Fallback if AST parse fails: grab a 40-line window around target line
            start = max(1, target_line - 20)
            end = min(total_lines, target_line + 20)
            snippet = "".join(lines[start - 1:end])
            return ASTContext(
                file_path=file_path,
                target_line=target_line,
                enclosing_symbol="window_fallback",
                symbol_type="window",
                start_line=start,
                end_line=end,
                function_code=snippet,
                module_imports=[],
                full_file_code=source_code
            )

        # 1. Collect all import statements
        imports: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # Extract line(s) of import
                start = getattr(node, "lineno", 1)
                end = getattr(node, "end_lineno", start)
                import_text = "".join(lines[start - 1:end]).strip()
                if import_text and import_text not in imports:
                    imports.append(import_text)

        # 2. Find the tightest enclosing function or class definition
        best_node = None
        best_type = None
        best_name = None

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                node_start = getattr(node, "lineno", 0)
                node_end = getattr(node, "end_lineno", node_start)

                if node_start <= target_line <= node_end:
                    # Choose innermost node (smallest line range)
                    if best_node is None or (node_end - node_start < getattr(best_node, "end_lineno", 999999) - getattr(best_node, "lineno", 0)):
                        best_node = node
                        best_name = node.name
                        if isinstance(node, ast.AsyncFunctionDef):
                            best_type = "async_function"
                        elif isinstance(node, ast.FunctionDef):
                            best_type = "function"
                        else:
                            best_type = "class"

        if best_node:
            start_line = getattr(best_node, "lineno", 1)
            end_line = getattr(best_node, "end_lineno", total_lines)
            function_code = "".join(lines[start_line - 1:end_line])
        else:
            # Module-level line: grab a window around target_line
            start_line = max(1, target_line - 15)
            end_line = min(total_lines, target_line + 15)
            best_name = "module_level"
            best_type = "module"
            function_code = "".join(lines[start_line - 1:end_line])

        return ASTContext(
            file_path=file_path,
            target_line=target_line,
            enclosing_symbol=best_name,
            symbol_type=best_type,
            start_line=start_line,
            end_line=end_line,
            function_code=function_code,
            module_imports=imports,
            full_file_code=source_code
        )
