"""OWL 2 RL inference and SHACL shape validation, ported from `scripts/kg/build.py`."""

from __future__ import annotations

import warnings
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

import owlrl
import pyshacl
import rdflib
import rdflib.collection
from owlrl.Namespaces import ERRNS
from rdflib import OWL, RDF, RDFS, BNode, URIRef
from rdflib.term import Node

RB: Final = rdflib.Namespace("https://benchmarks.rewire.it/vocab#")
SH: Final = rdflib.Namespace("http://www.w3.org/ns/shacl#")
ID_PREFIX: Final = "https://benchmarks.rewire.it/id/"
MLS_HAS_VALUE: Final = URIRef("http://www.w3.org/ns/mls#hasValue")
_SHARD_PARTS: Final = 2

GRAPHS: Final[dict[str, str]] = {
    "asserted": "https://benchmarks.rewire.it/graph/asserted",
    "inferred": "https://benchmarks.rewire.it/graph/inferred",
    "derived": "https://benchmarks.rewire.it/graph/derived",
    "ontology": "https://benchmarks.rewire.it/graph/ontology",
}

PROTECTED: Final[frozenset[URIRef]] = frozenset(
    {
        RB.evaluation,
        RB.testedSystem,
        RB.testedOn,
        RB.dataset,
        RB.family,
        RB.variantOf,
        RB.configurationOf,
        RB.aliasOf,
        RB.usesModel,
        RB.assessedBy,
        RB.status,
        MLS_HAS_VALUE,
        OWL.sameAs,
    }
)

PROPERTY_AXIOMS: Final = (
    RDFS.subPropertyOf,
    OWL.equivalentProperty,
    OWL.inverseOf,
    OWL.propertyDisjointWith,
    RDFS.domain,
    RDFS.range,
)
PROPERTY_TYPES: Final = (
    OWL.TransitiveProperty,
    OWL.SymmetricProperty,
    OWL.AsymmetricProperty,
    OWL.IrreflexiveProperty,
    OWL.FunctionalProperty,
    OWL.InverseFunctionalProperty,
)
TARGETS: Final = (SH.targetClass, SH.targetNode, SH.targetObjectsOf, SH.targetSubjectsOf)

type Triple = tuple[Node, Node, Node]


class BuildError(Exception):
    """SHACL validation, OWL reasoning, or KG build check failure."""


def in_vocabulary(predicate: URIRef, obj: object) -> bool:
    """Keep only `rb:` types and `rb:` predicates (`build.py:in_vocabulary`)."""
    if predicate == RDF.type:
        return isinstance(obj, URIRef) and str(obj).startswith(str(RB))
    return str(predicate).startswith(str(RB))


def ontology_files(root: Path) -> list[Path]:
    """The vocabulary, attribute declarations, pinned imports, and SKOS vocabularies under `root`."""
    ontology_dir = root / "data/ontology"
    imports_dir = ontology_dir / "imports"
    vocab_dir = root / "data/vocab"
    imports = sorted(p for p in imports_dir.iterdir() if p.suffix in {".ttl", ".nt"})
    vocabs = sorted(vocab_dir.glob("*.ttl"))
    return [
        ontology_dir / "rb.ttl",
        ontology_dir / "rb-attributes.ttl",
        *imports,
        *vocabs,
    ]


def load_ontology(root: Path) -> rdflib.Graph:
    """Parse ontology and SKOS files under `root` into an RDF graph."""
    graph = rdflib.Graph()
    for path in ontology_files(root):
        graph.parse(path, format="nt" if path.suffix == ".nt" else "turtle")
    return graph


def _parse_nquads(*, path: Path | None = None, data: str | None = None) -> rdflib.Graph:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"rdflib\..*")
        dataset = rdflib.Dataset()
        dataset.parse(path, data=data, format="nquads")
        graph = rdflib.Graph()
        for subj, pred, obj, _ in dataset.quads():
            graph.add((subj, pred, obj))
        return graph


def load_asserted(path: Path) -> rdflib.Graph:
    """Read an N-Quads file into an unnamed graph of triples (`build.py:load_asserted`)."""
    return _parse_nquads(path=path)


def graph_from_lines(lines: Sequence[str]) -> rdflib.Graph:
    """Parse N-Quads lines in memory into a triple graph without temp files."""
    return _parse_nquads(data="\n".join(lines) + "\n") if lines else rdflib.Graph()


def axiom_properties(ontology: rdflib.Graph) -> set[Node]:
    """Every property participating in an OWL/RDFS property axiom or property chain."""
    found: set[Node] = set()
    for predicate in PROPERTY_AXIOMS:
        for subj, obj in ontology.subject_objects(predicate):
            found.update(term for term in (subj, obj) if isinstance(term, URIRef))
    for kind in PROPERTY_TYPES:
        found.update(ontology.subjects(RDF.type, kind))
    for prop, head in ontology.subject_objects(OWL.propertyChainAxiom):
        found.add(prop)
        found.update(rdflib.collection.Collection(ontology, head))
    return found


def _filter_inferred(closure: rdflib.Graph, asserted: rdflib.Graph, ontology: rdflib.Graph) -> set[Triple]:
    inferred: set[Triple] = set()
    for subj, pred, obj in closure:
        if (subj, pred, obj) in asserted or (subj, pred, obj) in ontology:
            continue
        if not isinstance(subj, URIRef) or not str(subj).startswith(ID_PREFIX):
            continue
        if isinstance(obj, BNode) or (pred == OWL.sameAs and subj == obj):
            continue
        if isinstance(pred, URIRef) and in_vocabulary(pred, obj):
            inferred.add((subj, pred, obj))
    return inferred


def infer(asserted: rdflib.Graph, ontology: rdflib.Graph) -> set[Triple]:
    """Run OWL 2 RL closure and return inferred `rb:` statements about records."""
    closure = rdflib.Graph()
    active = axiom_properties(ontology)
    for triple in asserted:
        if triple[1] == RDF.type or triple[1] in active:
            closure.add(triple)
    for triple in ontology:
        closure.add(triple)
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics, axiomatic_triples=False, datatype_axioms=False).expand(
        closure
    )

    unsatisfiable = sorted(str(subj) for subj in closure.subjects(RDF.type, OWL.Nothing))
    if unsatisfiable:
        raise BuildError("Records typed owl:Nothing: " + ", ".join(unsatisfiable[:20]))
    errors = sorted(str(msg) for msg in closure.objects(None, ERRNS.error))
    if errors:
        raise BuildError("The reasoner found inconsistencies:\n  " + "\n  ".join(errors[:20]))
    merged = [
        (str(subj), str(obj))
        for subj, obj in closure.subject_objects(OWL.sameAs)
        if subj != obj and str(subj).startswith(ID_PREFIX)
    ]
    if merged:
        raise BuildError(f"owl:sameAs was inferred between records, e.g. {merged[0]}")
    return _filter_inferred(closure, asserted, ontology)


def check_inferred(asserted: rdflib.Graph, inferred: set[Triple]) -> None:
    """Verify no protected links were inferred and `rb:resultFor` matches `rb:testedSystem`."""
    protected = Counter(str(pred) for _, pred, _ in inferred if pred in PROTECTED)
    if protected:
        raise BuildError(f"Protected links were inferred: {dict(protected)}")
    tested: dict[Node, set[Node]] = {}
    for evaluation, system in asserted.subject_objects(RB.testedSystem):
        tested.setdefault(evaluation, set()).add(system)
    for result, system in ((subj, obj) for subj, pred, obj in inferred if pred == RB.resultFor):
        evaluations = set(asserted.objects(result, RB.evaluation))
        allowed: set[Node] = set().union(*(tested.get(ev, set()) for ev in evaluations))
        if system not in allowed:
            raise BuildError(
                f"{result} was inferred to be a result for {system}, which its evaluation did not test"
            )


def record_of(node: str) -> str:
    """The record IRI owning a node: the record itself, or `<record>` for `<record>/<path>`."""
    return ID_PREFIX + node[len(ID_PREFIX) :].split("/", 1)[0] if node.startswith(ID_PREFIX) else node


def without_inferred_paths(shapes: rdflib.Graph, vocabulary_file: Path) -> rdflib.Graph:
    """Drop property shapes on property-chain or inverse properties for asserted-only SHACL."""
    vocabulary = rdflib.Graph().parse(vocabulary_file, format="turtle")
    derived = set(vocabulary.subjects(OWL.propertyChainAxiom, None)) | set(
        vocabulary.subjects(OWL.inverseOf, None)
    )
    for shape, prop in list(shapes.subject_objects(SH.property)):
        if shapes.value(prop, SH.path) in derived:
            shapes.remove((shape, SH.property, prop))
    return shapes


def shard_shapes(shapes: rdflib.Graph, index: int, count: int) -> rdflib.Graph:
    """Keep targets only for shard `index` (1-based) of `count` groups of targeted shapes."""
    targeted = sorted({shape for target in TARGETS for shape in shapes.subjects(target, None)}, key=str)
    for position, shape in enumerate(targeted):
        if position % count != index - 1:
            for target in TARGETS:
                shapes.remove((shape, target, None))
    return shapes


def combined_data_graph(asserted: rdflib.Graph, inferred: Iterable[Triple] = ()) -> rdflib.Graph:
    """Combine asserted and inferred triples into one RDF graph without mutating `asserted`."""
    data = rdflib.Graph()
    for triple in asserted:
        data.add(triple)
    for triple in inferred:
        data.add(triple)
    return data


def validate_shapes(
    root: Path,
    data: rdflib.Graph,
    *,
    asserted_only: bool = False,
    context: set[str] | None = None,
    shard: tuple[int, int] | None = None,
) -> tuple[bool, str]:
    """Validate `data` against `shapes.ttl` and `attribute-shapes.ttl` under `root`."""
    ontology_dir = root / "data/ontology"
    vocab_dir = root / "data/vocab"
    vocabulary_file = ontology_dir / "rb.ttl"
    shapes_file = ontology_dir / "shapes.ttl"
    attr_shapes_file = ontology_dir / "attribute-shapes.ttl"

    eval_graph = combined_data_graph(data)
    shapes = rdflib.Graph().parse(shapes_file, format="turtle").parse(attr_shapes_file, format="turtle")
    if asserted_only:
        shapes = without_inferred_paths(shapes, vocabulary_file)
    if shard is not None:
        shapes = shard_shapes(shapes, *shard)

    eval_graph.parse(vocabulary_file, format="turtle")
    if vocab_dir.is_dir():
        for path in sorted(vocab_dir.glob("*.ttl")):
            eval_graph.parse(path, format="turtle")

    conforms, results, report = pyshacl.validate(
        eval_graph, shacl_graph=shapes, inference="none", advanced=False
    )
    if conforms or not context:
        return bool(conforms), str(report)
    kept = [
        res
        for res in results.subjects(RDF.type, SH.ValidationResult)
        if record_of(str(results.value(res, SH.focusNode))) not in context
    ]
    if not kept:
        return True, "Conforms (violations only on context records)"
    lines = [f"Constraint violations on checked records: {len(kept)}"]
    for res in kept:
        path = results.value(res, SH.resultPath)
        focus = results.value(res, SH.focusNode)
        msg = results.value(res, SH.resultMessage)
        suffix = f" {path}" if path else ""
        lines.append(f"- {focus}{suffix}: {msg}")
    return False, "\n".join(lines)


def nquads(triples: Iterable[Triple], graph: str) -> str:
    """Format triples as sorted N-Quads in a named graph (`build.py:nquads`)."""
    nt = rdflib.Graph()
    for triple in triples:
        nt.add(triple)
    suffix = f" <{graph}> ."
    lines = sorted(
        line[: -len(" .")] + suffix for line in nt.serialize(format="nt").splitlines() if line.strip()
    )
    return "\n".join(lines) + "\n" if lines else ""


def parse_shard(spec: str) -> tuple[int, int]:
    """Parse `I/N` shard selector (`1 <= I <= N`)."""
    parts = spec.split("/")
    if len(parts) != _SHARD_PARTS:
        raise BuildError(f"--shard must be I/N with 1 <= I <= N, not {spec}")
    try:
        index, count = int(parts[0]), int(parts[1])
    except ValueError as error:
        raise BuildError(f"--shard must be I/N with 1 <= I <= N, not {spec}") from error
    if not (1 <= index <= count):
        raise BuildError(f"--shard must be I/N with 1 <= I <= N, not {spec}")
    return index, count


def shapes_only_check(
    root: Path,
    asserted: rdflib.Graph,
    *,
    context: set[str] | None = None,
    shard: str | None = None,
) -> str:
    """Validate asserted statements under `root`, raising `BuildError` on non-conformance."""
    parsed_shard = parse_shard(shard) if shard is not None else None
    conforms, report = validate_shapes(
        root, asserted, asserted_only=True, context=context, shard=parsed_shard
    )
    if not conforms:
        raise BuildError("SHACL validation failed:\n" + report[:5000])
    group = f", shape group {shard}" if shard else ""
    return f"Records conform to the shapes ({len(asserted)} statements{group})"
