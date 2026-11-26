"""SCRUM-582 (TASK-320-1): SCIP Protobuf Codec (`index.scip` encode/decode)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

SYMBOL_ROLE_DEFINITION = 1
SYMBOL_ROLE_IMPORT = 2
SYMBOL_ROLE_WRITE_ACCESS = 4
SYMBOL_ROLE_READ_ACCESS = 8


def _add_field(
    msg_proto: descriptor_pb2.DescriptorProto,
    *,
    name: str,
    number: int,
    field_type: int,
    repeated: bool = False,
    type_name: str | None = None,
) -> None:
    f = msg_proto.field.add()
    f.name = name
    f.number = number
    f.type = field_type
    f.label = (
        descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
        if repeated
        else descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    )
    if type_name is not None:
        f.type_name = type_name


def _build_scip_message_classes() -> dict[str, type]:
    """Construct canonical SCIP protobuf wire schema in a dedicated DescriptorPool."""
    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "manta_scip.proto"
    fd.package = "scip"
    fd.syntax = "proto3"

    FDP = descriptor_pb2.FieldDescriptorProto

    tool_info = fd.message_type.add()
    tool_info.name = "ToolInfo"
    _add_field(tool_info, name="name", number=1, field_type=FDP.TYPE_STRING)
    _add_field(tool_info, name="version", number=2, field_type=FDP.TYPE_STRING)
    _add_field(tool_info, name="arguments", number=3, field_type=FDP.TYPE_STRING, repeated=True)

    metadata = fd.message_type.add()
    metadata.name = "Metadata"
    _add_field(metadata, name="version", number=1, field_type=FDP.TYPE_INT32)
    _add_field(
        metadata,
        name="tool_info",
        number=2,
        field_type=FDP.TYPE_MESSAGE,
        type_name=".scip.ToolInfo",
    )
    _add_field(metadata, name="project_root", number=3, field_type=FDP.TYPE_STRING)
    _add_field(metadata, name="text_document_encoding", number=4, field_type=FDP.TYPE_INT32)

    relationship = fd.message_type.add()
    relationship.name = "Relationship"
    _add_field(relationship, name="symbol", number=1, field_type=FDP.TYPE_STRING)
    _add_field(relationship, name="is_reference", number=2, field_type=FDP.TYPE_BOOL)
    _add_field(relationship, name="is_implementation", number=3, field_type=FDP.TYPE_BOOL)
    _add_field(relationship, name="is_type_definition", number=4, field_type=FDP.TYPE_BOOL)
    _add_field(relationship, name="is_definition", number=5, field_type=FDP.TYPE_BOOL)

    symbol_info = fd.message_type.add()
    symbol_info.name = "SymbolInformation"
    _add_field(symbol_info, name="symbol", number=1, field_type=FDP.TYPE_STRING)
    _add_field(
        symbol_info,
        name="documentation",
        number=3,
        field_type=FDP.TYPE_STRING,
        repeated=True,
    )
    _add_field(
        symbol_info,
        name="relationships",
        number=4,
        field_type=FDP.TYPE_MESSAGE,
        repeated=True,
        type_name=".scip.Relationship",
    )
    _add_field(symbol_info, name="kind", number=5, field_type=FDP.TYPE_INT32)
    _add_field(symbol_info, name="display_name", number=6, field_type=FDP.TYPE_STRING)
    _add_field(symbol_info, name="enclosing_symbol", number=8, field_type=FDP.TYPE_STRING)

    occurrence = fd.message_type.add()
    occurrence.name = "Occurrence"
    _add_field(occurrence, name="range", number=1, field_type=FDP.TYPE_INT32, repeated=True)
    _add_field(occurrence, name="symbol", number=2, field_type=FDP.TYPE_STRING)
    _add_field(occurrence, name="symbol_roles", number=3, field_type=FDP.TYPE_INT32)
    _add_field(
        occurrence,
        name="enclosing_range",
        number=7,
        field_type=FDP.TYPE_INT32,
        repeated=True,
    )

    document = fd.message_type.add()
    document.name = "Document"
    _add_field(document, name="relative_path", number=1, field_type=FDP.TYPE_STRING)
    _add_field(
        document,
        name="occurrences",
        number=2,
        field_type=FDP.TYPE_MESSAGE,
        repeated=True,
        type_name=".scip.Occurrence",
    )
    _add_field(
        document,
        name="symbols",
        number=3,
        field_type=FDP.TYPE_MESSAGE,
        repeated=True,
        type_name=".scip.SymbolInformation",
    )
    _add_field(document, name="language", number=4, field_type=FDP.TYPE_STRING)

    index_msg = fd.message_type.add()
    index_msg.name = "Index"
    _add_field(
        index_msg,
        name="metadata",
        number=1,
        field_type=FDP.TYPE_MESSAGE,
        type_name=".scip.Metadata",
    )
    _add_field(
        index_msg,
        name="documents",
        number=2,
        field_type=FDP.TYPE_MESSAGE,
        repeated=True,
        type_name=".scip.Document",
    )
    _add_field(
        index_msg,
        name="external_symbols",
        number=3,
        field_type=FDP.TYPE_MESSAGE,
        repeated=True,
        type_name=".scip.SymbolInformation",
    )

    pool = descriptor_pool.DescriptorPool()
    file_desc = pool.AddSerializedFile(fd.SerializeToString())
    return {
        name: message_factory.GetMessageClass(desc)
        for name, desc in file_desc.message_types_by_name.items()
    }


_SCIP_CLASSES = _build_scip_message_classes()
ScipIndexProto = _SCIP_CLASSES["Index"]
ScipMetadataProto = _SCIP_CLASSES["Metadata"]
ScipToolInfoProto = _SCIP_CLASSES["ToolInfo"]
ScipDocumentProto = _SCIP_CLASSES["Document"]
ScipOccurrenceProto = _SCIP_CLASSES["Occurrence"]
ScipSymbolInformationProto = _SCIP_CLASSES["SymbolInformation"]
ScipRelationshipProto = _SCIP_CLASSES["Relationship"]


_KIND_STR_TO_INT = {
    "unspecified": 0,
    "class": 7,
    "function": 17,
    "method": 26,
    "module": 29,
    "variable": 61,
}
_KIND_INT_TO_STR = {v: k for k, v in _KIND_STR_TO_INT.items() if k != "unspecified"}


@dataclass(frozen=True, slots=True)
class ScipSymbolRecord:
    symbol: str
    display_name: str
    file_path: str = ""
    line: int = 1
    kind: str = "function"
    enclosing_symbol: str = ""


@dataclass(frozen=True, slots=True)
class ScipCallEdge:
    caller: str
    callee: str


@dataclass(slots=True)
class ParsedScipIndex:
    tool_name: str
    tool_version: str
    project_root: str
    symbols: list[ScipSymbolRecord] = field(default_factory=list)
    edges: list[ScipCallEdge] = field(default_factory=list)
    occurrence_count: int = 0


def encode_scip_index(
    symbols: Iterable[ScipSymbolRecord],
    edges: Iterable[ScipCallEdge],
    *,
    tool_name: str = "manta-scip-python",
    tool_version: str = "1.0.0",
    project_root: str = "",
    language: str = "python",
) -> bytes:
    """Serialize symbol records and caller->callee call edges into `index.scip` protobuf bytes."""
    index_msg = ScipIndexProto()
    index_msg.metadata.version = 0
    index_msg.metadata.tool_info.name = tool_name
    index_msg.metadata.tool_info.version = tool_version
    index_msg.metadata.project_root = project_root
    index_msg.metadata.text_document_encoding = 1  # UTF8

    edges_by_caller: dict[str, list[str]] = {}
    for edge in edges:
        if edge.caller and edge.callee:
            edges_by_caller.setdefault(edge.caller, []).append(edge.callee)

    docs_by_path: dict[str, list[ScipSymbolRecord]] = {}
    for sym in symbols:
        docs_by_path.setdefault(sym.file_path or "<external>", []).append(sym)

    for rel_path, doc_symbols in docs_by_path.items():
        if rel_path == "<external>":
            for sym in doc_symbols:
                ext = index_msg.external_symbols.add()
                ext.symbol = sym.symbol
                ext.display_name = sym.display_name or sym.symbol
                ext.kind = _KIND_STR_TO_INT.get(sym.kind, 17)
                ext.enclosing_symbol = sym.enclosing_symbol
                for callee in edges_by_caller.get(sym.symbol, ()):
                    rel = ext.relationships.add()
                    rel.symbol = callee
                    rel.is_reference = True
            continue

        doc = index_msg.documents.add()
        doc.relative_path = rel_path
        doc.language = language

        for sym in doc_symbols:
            s_info = doc.symbols.add()
            s_info.symbol = sym.symbol
            s_info.display_name = sym.display_name or sym.symbol
            s_info.kind = _KIND_STR_TO_INT.get(sym.kind, 17)
            s_info.enclosing_symbol = sym.enclosing_symbol

            line_idx = max(0, sym.line - 1)
            def_occ = doc.occurrences.add()
            def_occ.symbol = sym.symbol
            def_occ.symbol_roles = SYMBOL_ROLE_DEFINITION
            def_occ.range.extend([line_idx, 0, max(1, len(s_info.display_name))])

            for callee in edges_by_caller.get(sym.symbol, ()):
                rel = s_info.relationships.add()
                rel.symbol = callee
                rel.is_reference = True

                ref_occ = doc.occurrences.add()
                ref_occ.symbol = callee
                ref_occ.symbol_roles = SYMBOL_ROLE_READ_ACCESS
                ref_occ.range.extend([line_idx, 0, 1])

    return bytes(index_msg.SerializeToString())


def decode_scip_index(raw_bytes: bytes) -> ParsedScipIndex:
    """Deserialize binary `index.scip` protobuf bytes into structured symbols and call edges."""
    if not raw_bytes:
        raise ValueError("SCIP protobuf payload cannot be empty")

    index_msg = ScipIndexProto.FromString(raw_bytes)
    symbols: list[ScipSymbolRecord] = []
    edges: list[ScipCallEdge] = []
    occurrence_count = 0

    for doc in index_msg.documents:
        occurrence_count += len(doc.occurrences)
        def_lines: dict[str, int] = {}
        for occ in doc.occurrences:
            if occ.symbol and (occ.symbol_roles & SYMBOL_ROLE_DEFINITION):
                if occ.range:
                    def_lines.setdefault(occ.symbol, int(occ.range[0]) + 1)

        for s_info in doc.symbols:
            sym_id = s_info.symbol
            if not sym_id:
                continue
            symbols.append(
                ScipSymbolRecord(
                    symbol=sym_id,
                    display_name=s_info.display_name or sym_id,
                    file_path=doc.relative_path,
                    line=def_lines.get(sym_id, 1),
                    kind=_KIND_INT_TO_STR.get(int(s_info.kind), "function"),
                    enclosing_symbol=s_info.enclosing_symbol,
                )
            )
            for rel in s_info.relationships:
                if rel.symbol and (rel.is_reference or not rel.is_definition):
                    edges.append(ScipCallEdge(caller=sym_id, callee=rel.symbol))

    for ext in index_msg.external_symbols:
        if not ext.symbol:
            continue
        symbols.append(
            ScipSymbolRecord(
                symbol=ext.symbol,
                display_name=ext.display_name or ext.symbol,
                file_path="",
                line=1,
                kind=_KIND_INT_TO_STR.get(int(ext.kind), "function"),
                enclosing_symbol=ext.enclosing_symbol,
            )
        )
        for rel in ext.relationships:
            if rel.symbol and (rel.is_reference or not rel.is_definition):
                edges.append(ScipCallEdge(caller=ext.symbol, callee=rel.symbol))

    return ParsedScipIndex(
        tool_name=index_msg.metadata.tool_info.name,
        tool_version=index_msg.metadata.tool_info.version,
        project_root=index_msg.metadata.project_root,
        symbols=symbols,
        edges=edges,
        occurrence_count=occurrence_count,
    )
