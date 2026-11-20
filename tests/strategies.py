"""Hypothesis strategies shared by the property tests."""

from __future__ import annotations

from hypothesis import strategies as st

from rewire_evidence.canonical import EXACT_INTEGER_LIMIT, JsonObject, JsonValue

#: Numbers a JavaScript value can hold exactly as written: safe integers and finite doubles.
numbers = st.integers(-EXACT_INTEGER_LIMIT, EXACT_INTEGER_LIMIT) | st.floats(
    allow_nan=False, allow_infinity=False
)
#: Keys that include array indices, so property order is exercised and not only insertion order.
keys = (
    st.text(max_size=6) | st.integers(0, 2**33).map(str) | st.sampled_from(["0", "1", "01", "-1", "10", "9"])
)
scalars = st.none() | st.booleans() | numbers | st.text(max_size=12)
json_values: st.SearchStrategy[JsonValue] = st.recursive(
    scalars,
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(keys, inner, max_size=4),
    max_leaves=12,
)
json_objects: st.SearchStrategy[JsonObject] = st.dictionaries(keys, json_values, max_size=5)

#: Record IDs as the store's pattern allows them: lowercase words joined by single hyphens.
record_ids = st.from_regex(r"[a-z0-9]{1,6}(-[a-z0-9]{1,6}){0,3}", fullmatch=True)
kinds = st.sampled_from(["model", "dataset_subset", "use_case", "source", "evaluation", "result", "claim"])


@st.composite
def stores(draw: st.DrawFn) -> list[JsonObject]:
    """A set of records with unique IDs, of mixed entity and evidence kinds."""
    ids = draw(st.lists(record_ids, unique=True, max_size=12))
    return [
        {
            "id": record_id,
            "kind": draw(kinds),
            "name": draw(st.text(max_size=8)),
            "attributes": draw(json_objects),
        }
        for record_id in ids
    ]
