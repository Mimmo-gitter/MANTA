"""SCRUM-582 (TASK-320-1): Python AST -> SCIP index generator and artifact storage."""

from __future__ import annotations

import ast
from pathlib import Path
import uuid

from app.config import settings
from app.refactoring.scip_codec import ScipCallEdge, ScipSymbolRecord, encode_scip_index

INDEXER_VERSION = "manta-scip-python/1.0.0"

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)


def _format_symbol(
    repo_name: str,
    commit_sha: str,
    module_name: str,
    *parts: tuple[str, str],
) -> str:
    """Build deterministic SCIP-style symbol identifier (`kind` in {'module','class','func'})."""
    base = f"scip-python manta {repo_name} {commit_sha} `{module_name}`"
    suffix_tokens: list[str] = []
    for name, kind in parts:
        if kind == "class":
            suffix_tokens.append(f"{name}#")
        else:
            suffix_tokens.append(f"{name}().")
    return f"{base}/" + "".join(suffix_tokens)


class _PythonFileASTExtractor(ast.NodeVisitor):
    """Extract classes, functions/methods, and outgoing calls from a single Python file."""

    def __init__(
        self,
        *,
        rel_path: str,
        module_name: str,
        repo_name: str,
        commit_sha: str,
    ) -> None:
        self.rel_path = rel_path
        self.module_name = module_name
        self.repo_name = repo_name
        self.commit_sha = commit_sha

        self.module_symbol = _format_symbol(repo_name, commit_sha, module_name)
        self.symbols: list[ScipSymbolRecord] = [
            ScipSymbolRecord(
                symbol=self.module_symbol,
                display_name=module_name,
                file_path=rel_path,
                line=1,
                kind="module",
                enclosing_symbol="",
            )
        ]
        # List of tuples: (caller_symbol, raw_callee_name, current_class_name | None, file_imports)
        self.raw_calls: list[tuple[str, str, str | None]] = []
        self.imports: dict[str, str] = {}
        self.local_defs: dict[str, str] = {}

        self._class_stack: list[tuple[str, str]] = []
        self._callable_stack: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bind_name = alias.asname or alias.name.split(".")[0]
            self.imports[bind_name] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            bind_name = alias.asname or alias.name
            self.imports[bind_name] = f"{mod}.{alias.name}" if mod else alias.name

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        cls_symbol = _format_symbol(
            self.repo_name,
            self.commit_sha,
            self.module_name,
            (node.name, "class"),
        )
        enclosing = self._class_stack[-1][1] if self._class_stack else self.module_symbol
        self.symbols.append(
            ScipSymbolRecord(
                symbol=cls_symbol,
                display_name=node.name,
                file_path=self.rel_path,
                line=int(node.lineno),
                kind="class",
                enclosing_symbol=enclosing,
            )
        )
        self.local_defs[node.name] = cls_symbol
        self._class_stack.append((node.name, cls_symbol))
        self.generic_visit(node)
        self._class_stack.pop()

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if self._class_stack:
            cls_name, cls_symbol = self._class_stack[-1]
            fn_symbol = _format_symbol(
                self.repo_name,
                self.commit_sha,
                self.module_name,
                (cls_name, "class"),
                (node.name, "func"),
            )
            kind = "method"
            enclosing = cls_symbol
            self.local_defs[f"{cls_name}.{node.name}"] = fn_symbol
        else:
            fn_symbol = _format_symbol(
                self.repo_name,
                self.commit_sha,
                self.module_name,
                (node.name, "func"),
            )
            kind = "function"
            enclosing = self.module_symbol
            self.local_defs[node.name] = fn_symbol

        self.symbols.append(
            ScipSymbolRecord(
                symbol=fn_symbol,
                display_name=node.name,
                file_path=self.rel_path,
                line=int(node.lineno),
                kind=kind,
                enclosing_symbol=enclosing,
            )
        )
        self._callable_stack.append(fn_symbol)
        self.generic_visit(node)
        self._callable_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._callable_stack:
            caller = self._callable_stack[-1]
            raw_target = _extract_call_name(node.func)
            if raw_target:
                cur_cls = self._class_stack[-1][0] if self._class_stack else None
                self.raw_calls.append((caller, raw_target, cur_cls))
        self.generic_visit(node)


def _extract_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _extract_call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _path_to_module_name(rel_path: Path) -> str:
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else rel_path.stem


def _iter_python_files(repo_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(repo_path.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.relative_to(repo_path).parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def generate_python_scip_index(
    repo_path: Path | str,
    *,
    repo_name: str,
    commit_sha: str,
) -> tuple[bytes, int]:
    """
    Parse a Python repository directory with AST and build binary `index.scip` protobuf bytes.

    Returns `(protobuf_bytes, symbols_count)`.
    """
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Repository path does not exist or is not a directory: {root}")

    extractors: list[_PythonFileASTExtractor] = []
    fqn_to_symbol: dict[str, str] = {}
    all_symbols: list[ScipSymbolRecord] = []

    for py_file in _iter_python_files(root):
        rel_path = py_file.relative_to(root).as_posix()
        mod_name = _path_to_module_name(py_file.relative_to(root))
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel_path)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        extractor = _PythonFileASTExtractor(
            rel_path=rel_path,
            module_name=mod_name,
            repo_name=repo_name,
            commit_sha=commit_sha,
        )
        extractor.visit(tree)
        extractors.append(extractor)
        all_symbols.extend(extractor.symbols)

        fqn_to_symbol[mod_name] = extractor.module_symbol
        for local_name, sym_id in extractor.local_defs.items():
            fqn_to_symbol[f"{mod_name}.{local_name}"] = sym_id

    edges: list[ScipCallEdge] = []
    seen_edges: set[tuple[str, str]] = set()
    for ext in extractors:
        for caller, raw_target, cur_cls in ext.raw_calls:
            callee = _resolve_callee_symbol(
                raw_target=raw_target,
                cur_cls=cur_cls,
                ext=ext,
                fqn_to_symbol=fqn_to_symbol,
            )
            if not callee or callee == caller:
                continue
            pair = (caller, callee)
            if pair not in seen_edges:
                seen_edges.add(pair)
                edges.append(ScipCallEdge(caller=caller, callee=callee))

    raw_bytes = encode_scip_index(
        all_symbols,
        edges,
        tool_name="manta-scip-python",
        tool_version="1.0.0",
        project_root=root.as_uri(),
        language="python",
    )
    return raw_bytes, len(all_symbols)


def _resolve_callee_symbol(
    *,
    raw_target: str,
    cur_cls: str | None,
    ext: _PythonFileASTExtractor,
    fqn_to_symbol: dict[str, str],
) -> str | None:
    if raw_target in ext.local_defs:
        return ext.local_defs[raw_target]

    if cur_cls and raw_target.startswith(("self.", "cls.")):
        attr = raw_target.split(".", 1)[1]
        candidate = ext.local_defs.get(f"{cur_cls}.{attr}")
        if candidate:
            return candidate

    head, *tail = raw_target.split(".", 1)
    if head in ext.imports:
        imported_fqn = ext.imports[head]
        full_fqn = f"{imported_fqn}.{tail[0]}" if tail else imported_fqn
        if full_fqn in fqn_to_symbol:
            return fqn_to_symbol[full_fqn]

    if raw_target in fqn_to_symbol:
        return fqn_to_symbol[raw_target]

    return None


def _safe_segment(raw: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in raw.strip())
    return cleaned or "unknown"


def persist_scip_artifact(
    raw_bytes: bytes,
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
    commit_sha: str,
    storage_dir: str | None = None,
) -> str:
    """Persist binary `index.scip` artifact atomically and return its `file://` URI."""
    base_dir = Path(storage_dir or settings.SCIP_STORAGE_DIR).resolve()
    target_dir = base_dir / tenant_id.hex
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{repository_id.hex}_{_safe_segment(commit_sha)}.scip"
    tmp_path = target_dir / f".{uuid.uuid4().hex[:8]}.tmp"
    tmp_path.write_bytes(raw_bytes)
    tmp_path.replace(target_path)
    return target_path.as_uri()


def load_scip_artifact_bytes(storage_uri: str) -> bytes:
    """Load binary `index.scip` content from a `file://` URI or local filesystem path."""
    if storage_uri.startswith("file://"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(storage_uri)
        raw_path = unquote(parsed.path)
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        path = Path(raw_path)
    else:
        path = Path(storage_uri)
    return path.read_bytes()
