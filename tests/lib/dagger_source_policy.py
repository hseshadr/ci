from __future__ import annotations

import ast
import re
import sys
from itertools import chain
from pathlib import Path
from typing import Final

SENSITIVE_NAME: Final = re.compile(r"(?:token|secret|password|credential|private_key|api_key)$")
IMPLICIT_WORKSPACE: Final = frozenset({"current_workspace", "currentWorkspace"})
Inspection = tuple[list[str], bool]


def annotation_name(annotation: ast.expr | None) -> str:
    """Return the source spelling for a type annotation."""
    return "" if annotation is None else ast.unparse(annotation)


class ModuleVisitor(ast.NodeVisitor):
    """Collect unsafe Dagger module boundary behavior from Python syntax."""

    def __init__(self) -> None:
        self.reasons: list[str] = []
        self.has_directory_source = False

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.target.id == "source":
            annotation = annotation_name(node.annotation)
            self.has_directory_source = "Directory" in annotation and "DefaultPath" in annotation
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name in IMPLICIT_WORKSPACE:
            self.reasons.append("module reads the implicit current workspace")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._inspect_arguments(node.args)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._inspect_arguments(node.args)
        self.generic_visit(node)

    def _inspect_arguments(self, arguments: ast.arguments) -> None:
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
            annotation = annotation_name(argument.annotation)
            if SENSITIVE_NAME.search(argument.arg) and "Secret" not in annotation:
                self.reasons.append(f"credential argument '{argument.arg}' is not typed Secret")


def inspect_file(path: Path) -> Inspection:
    """Parse one real module file and return its findings."""
    visitor = ModuleVisitor()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return visitor.reasons, visitor.has_directory_source


def qualify_inspection(item: tuple[Path, Inspection]) -> list[str]:
    """Attach a source path to each finding."""
    path, (reasons, _) = item
    return [f"{path}:{reason}" for reason in reasons]


def collect_findings(paths: list[Path], inspections: list[Inspection]) -> list[str]:
    """Flatten qualified findings without changing their source order."""
    paired = zip(paths, inspections, strict=True)
    return list(chain.from_iterable(map(qualify_inspection, paired)))


def inspect_paths(paths: list[Path]) -> list[str]:
    """Inspect all Python module files as one Dagger module."""
    inspections = list(map(inspect_file, paths))
    findings = collect_findings(paths, inspections)
    has_source = any(result[1] for result in inspections)
    if paths and not has_source:
        findings.append("module has no explicit Directory source field")
    return findings


def main(arguments: list[str]) -> int:
    """Emit one semantic finding per line."""
    findings = inspect_paths([Path(argument) for argument in arguments])
    if findings:
        sys.stdout.write("\n".join(findings) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
