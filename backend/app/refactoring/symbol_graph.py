"""SCRUM-582 (TASK-320-1): Sub-1.5ms In-Memory SCIP Symbol Call-Graph Service & Tenant Registry."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from threading import RLock
import uuid

from app.refactoring.scip_codec import ParsedScipIndex, ScipSymbolRecord, decode_scip_index
from app.refactoring.scip_indexer import load_scip_artifact_bytes


@dataclass(frozen=True, slots=True)
class SymbolNode:
    symbol: str
    display_name: str
    file_path: str
    line: int
    kind: str
    enclosing_symbol: str


class InMemorySymbolGraph:
    """
    Dense integer-indexed adjacency structure deserialized from `index.scip`.

    Provides O(1) dictionary resolution from symbol identifier -> integer ID and
    contiguous tuple slice reads for caller/callee lookups well under 1.5ms at 50k+ symbols.
    """

    def __init__(
        self,
        *,
        commit_sha: str,
        nodes: tuple[SymbolNode, ...],
        sym_to_id: dict[str, int],
        short_name_to_ids: dict[str, tuple[int, ...]],
        callers: tuple[tuple[int, ...], ...],
        callees: tuple[tuple[int, ...], ...],
    ) -> None:
        self.commit_sha = commit_sha
        self._nodes = nodes
        self._sym_to_id = sym_to_id
        self._short_name_to_ids = short_name_to_ids
        self._callers = callers
        self._callees = callees

    @property
    def symbol_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(adj) for adj in self._callees)

    @classmethod
    def from_parsed_index(
        cls,
        parsed: ParsedScipIndex,
        *,
        commit_sha: str,
    ) -> "InMemorySymbolGraph":
        nodes_list: list[SymbolNode] = []
        sym_to_id: dict[str, int] = {}
        short_buckets: dict[str, list[int]] = {}

        def _ensure_node(rec: ScipSymbolRecord) -> int:
            existing = sym_to_id.get(rec.symbol)
            if existing is not None:
                return existing
            idx = len(nodes_list)
            sym_to_id[rec.symbol] = idx
            display = rec.display_name or rec.symbol
            node = SymbolNode(
                symbol=rec.symbol,
                display_name=display,
                file_path=rec.file_path,
                line=rec.line,
                kind=rec.kind,
                enclosing_symbol=rec.enclosing_symbol,
            )
            nodes_list.append(node)
            short_buckets.setdefault(display, []).append(idx)
            return idx

        for rec in parsed.symbols:
            _ensure_node(rec)

        caller_sets: list[list[int]] = [[] for _ in range(len(nodes_list))]
        callee_sets: list[list[int]] = [[] for _ in range(len(nodes_list))]

        for edge in parsed.edges:
            if edge.caller not in sym_to_id:
                caller_idx = _ensure_node(
                    ScipSymbolRecord(symbol=edge.caller, display_name=edge.caller)
                )
                caller_sets.append([])
                callee_sets.append([])
            else:
                caller_idx = sym_to_id[edge.caller]

            if edge.callee not in sym_to_id:
                callee_idx = _ensure_node(
                    ScipSymbolRecord(symbol=edge.callee, display_name=edge.callee)
                )
                caller_sets.append([])
                callee_sets.append([])
            else:
                callee_idx = sym_to_id[edge.callee]

            callee_sets[caller_idx].append(callee_idx)
            caller_sets[callee_idx].append(caller_idx)

        callers = tuple(tuple(dict.fromkeys(adj)) for adj in caller_sets)
        callees = tuple(tuple(dict.fromkeys(adj)) for adj in callee_sets)
        short_name_to_ids = {k: tuple(v) for k, v in short_buckets.items()}

        return cls(
            commit_sha=commit_sha,
            nodes=tuple(nodes_list),
            sym_to_id=sym_to_id,
            short_name_to_ids=short_name_to_ids,
            callers=callers,
            callees=callees,
        )

    @classmethod
    def from_protobuf_bytes(
        cls,
        raw_bytes: bytes,
        *,
        commit_sha: str,
    ) -> "InMemorySymbolGraph":
        parsed = decode_scip_index(raw_bytes)
        return cls.from_parsed_index(parsed, commit_sha=commit_sha)

    def resolve_symbol_id(self, symbol_or_name: str) -> int | None:
        idx = self._sym_to_id.get(symbol_or_name)
        if idx is not None:
            return idx
        matches = self._short_name_to_ids.get(symbol_or_name)
        return matches[0] if matches else None

    def get_node(self, symbol_or_name: str) -> SymbolNode | None:
        idx = self.resolve_symbol_id(symbol_or_name)
        return self._nodes[idx] if idx is not None else None

    def _traverse(
        self,
        symbol_or_name: str,
        *,
        adjacency: tuple[tuple[int, ...], ...],
        max_depth: int = 1,
        limit: int = 100,
    ) -> list[SymbolNode]:
        start_id = self.resolve_symbol_id(symbol_or_name)
        if start_id is None or max_depth < 1 or limit < 1:
            return []

        if max_depth == 1:
            nodes = self._nodes
            return [nodes[neighbor] for neighbor in adjacency[start_id][:limit]]

        visited: set[int] = {start_id}
        queue: deque[tuple[int, int]] = deque([(start_id, 0)])
        result: list[SymbolNode] = []
        nodes = self._nodes

        while queue and len(result) < limit:
            curr_id, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for nxt_id in adjacency[curr_id]:
                if nxt_id not in visited:
                    visited.add(nxt_id)
                    result.append(nodes[nxt_id])
                    if len(result) >= limit:
                        break
                    queue.append((nxt_id, depth + 1))
        return result

    def get_callers(
        self,
        symbol: str,
        *,
        max_depth: int = 1,
        limit: int = 100,
    ) -> list[SymbolNode]:
        """Return callers of `symbol` up to `max_depth` hops."""
        return self._traverse(
            symbol,
            adjacency=self._callers,
            max_depth=max_depth,
            limit=limit,
        )

    def get_callees(
        self,
        symbol: str,
        *,
        max_depth: int = 1,
        limit: int = 100,
    ) -> list[SymbolNode]:
        """Return callees invoked by `symbol` up to `max_depth` hops."""
        return self._traverse(
            symbol,
            adjacency=self._callees,
            max_depth=max_depth,
            limit=limit,
        )

    def get_graph_slice(
        self,
        root_symbol: str | None = None,
        *,
        depth: int = 1,
        limit: int = 100,
    ) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
        """Return React Flow-friendly `(nodes, edges)` dicts centered at `root_symbol` or top nodes."""
        if not self._nodes or limit < 1:
            return [], []

        selected_ids: list[int] = []
        seen: set[int] = set()

        def _add_id(node_id: int) -> None:
            if node_id not in seen and len(selected_ids) < limit:
                seen.add(node_id)
                selected_ids.append(node_id)

        if root_symbol:
            root_id = self.resolve_symbol_id(root_symbol)
            if root_id is not None:
                _add_id(root_id)
                queue: deque[tuple[int, int]] = deque([(root_id, 0)])
                while queue and len(selected_ids) < limit:
                    curr_id, d = queue.popleft()
                    if d >= depth:
                        continue
                    for neighbor in self._callers[curr_id] + self._callees[curr_id]:
                        if neighbor not in seen:
                            _add_id(neighbor)
                            if len(selected_ids) >= limit:
                                break
                            queue.append((neighbor, d + 1))

        if not selected_ids:
            for idx in range(min(limit, len(self._nodes))):
                _add_id(idx)

        selected_set = set(selected_ids)
        nodes_out: list[dict[str, object]] = []
        edges_out: list[dict[str, str]] = []
        for idx in selected_ids:
            n = self._nodes[idx]
            nodes_out.append(
                {
                    "id": n.symbol,
                    "display_name": n.display_name,
                    "file_path": n.file_path,
                    "line": n.line,
                    "kind": n.kind,
                    "enclosing_symbol": n.enclosing_symbol,
                }
            )
            for callee_idx in self._callees[idx]:
                if callee_idx in selected_set:
                    edges_out.append(
                        {
                            "source": n.symbol,
                            "target": self._nodes[callee_idx].symbol,
                            "relation": "calls",
                        }
                    )

        return nodes_out, edges_out


class SymbolGraphRegistry:
    """Tenant-scoped bounded LRU registry of pre-deserialized RAM symbol graphs."""

    def __init__(self, max_entries: int = 64) -> None:
        self._max_entries = max(1, max_entries)
        self._lock = RLock()
        self._graphs: OrderedDict[tuple[uuid.UUID, uuid.UUID, str], InMemorySymbolGraph] = (
            OrderedDict()
        )

    def put(
        self,
        *,
        tenant_id: uuid.UUID,
        repository_id: uuid.UUID,
        commit_sha: str,
        graph: InMemorySymbolGraph,
    ) -> None:
        key = (tenant_id, repository_id, commit_sha)
        with self._lock:
            if key in self._graphs:
                self._graphs.move_to_end(key)
            self._graphs[key] = graph
            while len(self._graphs) > self._max_entries:
                self._graphs.popitem(last=False)

    def get(
        self,
        *,
        tenant_id: uuid.UUID,
        repository_id: uuid.UUID,
        commit_sha: str,
    ) -> InMemorySymbolGraph | None:
        key = (tenant_id, repository_id, commit_sha)
        with self._lock:
            graph = self._graphs.get(key)
            if graph is not None:
                self._graphs.move_to_end(key)
            return graph

    def is_loaded(
        self,
        *,
        tenant_id: uuid.UUID,
        repository_id: uuid.UUID,
        commit_sha: str,
    ) -> bool:
        with self._lock:
            return (tenant_id, repository_id, commit_sha) in self._graphs

    def get_or_load(
        self,
        *,
        tenant_id: uuid.UUID,
        repository_id: uuid.UUID,
        commit_sha: str,
        storage_uri: str,
    ) -> InMemorySymbolGraph:
        cached = self.get(
            tenant_id=tenant_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
        )
        if cached is not None:
            return cached

        raw_bytes = load_scip_artifact_bytes(storage_uri)
        graph = InMemorySymbolGraph.from_protobuf_bytes(raw_bytes, commit_sha=commit_sha)
        self.put(
            tenant_id=tenant_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            graph=graph,
        )
        return graph

    def invalidate_repository(
        self,
        *,
        tenant_id: uuid.UUID,
        repository_id: uuid.UUID,
    ) -> int:
        with self._lock:
            keys = [k for k in self._graphs if k[0] == tenant_id and k[1] == repository_id]
            for k in keys:
                del self._graphs[k]
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._graphs.clear()


symbol_graph_registry = SymbolGraphRegistry()
