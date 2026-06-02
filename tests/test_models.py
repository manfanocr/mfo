"""Tests for the core entity models: defaults, validation, and lossless round-trip."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from mfo.core import (
    BBox,
    EditAction,
    EditRecord,
    OCRSpan,
    Page,
    Point,
    Project,
    Region,
    RegionStatus,
    RegionType,
    RenderArtifact,
    TranslationCandidate,
    TranslationUnit,
)


def _assert_round_trip(model: BaseModel) -> None:
    """A model survives a JSON serialize/deserialize cycle unchanged (invariant I-2)."""
    restored = type(model).model_validate_json(model.model_dump_json())
    assert restored == model


# --- Defaults and identifiers -------------------------------------------------------------


def test_default_ids_are_prefixed_per_entity() -> None:
    assert Project(name="v1", source_lang="ja", target_lang="en").id.startswith("prj_")
    assert Page(project_id="prj_x", index=0, image_path="p.png", width=1, height=1).id.startswith(
        "pg_"
    )
    assert Region(page_id="pg_x", bbox=BBox(x=0, y=0, width=1, height=1)).id.startswith("rgn_")
    assert OCRSpan(region_id="rgn_x", text="あ").id.startswith("ocr_")
    assert TranslationUnit().id.startswith("tu_")
    assert TranslationCandidate(text="hi").id.startswith("cand_")
    assert RenderArtifact(page_id="pg_x", output_path="o.png").id.startswith("rnd_")


def test_ids_are_unique_per_instance() -> None:
    ids = {Region(page_id="pg_x", bbox=BBox(x=0, y=0, width=1, height=1)).id for _ in range(1000)}
    assert len(ids) == 1000


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Project(name="v1", source_lang="ja", target_lang="en", bogus=1)  # type: ignore[call-arg]


def test_negative_bbox_dimensions_rejected() -> None:
    with pytest.raises(ValidationError):
        BBox(x=0, y=0, width=-1, height=1)


# --- TranslationUnit selected-candidate integrity -----------------------------------------


def test_selected_candidate_must_exist() -> None:
    candidate = TranslationCandidate(text="hello")
    unit = TranslationUnit(candidates=[candidate], selected_candidate_id=candidate.id)
    assert unit.selected_candidate_id == candidate.id

    with pytest.raises(ValidationError):
        TranslationUnit(candidates=[candidate], selected_candidate_id="cand_missing")


# --- Lossless round-trip ------------------------------------------------------------------


def test_round_trip_representative_instances() -> None:
    candidate = TranslationCandidate(text="Hello!", rationale="natural phrasing")
    instances: list[BaseModel] = [
        Project(name="vol1", source_lang="ja", target_lang="en", config={"k": [1, 2, None]}),
        Page(project_id="prj_x", index=3, image_path="pages/003.png", width=800, height=1200),
        Region(
            page_id="pg_x",
            bbox=BBox(x=1.5, y=2.5, width=10, height=20),
            polygon=[Point(x=0, y=0), Point(x=1, y=1)],
            type=RegionType.BUBBLE,
            reading_order_index=2,
            confidence=0.92,
            status=RegionStatus.NEEDS_REVIEW,
        ),
        OCRSpan(
            region_id="rgn_x",
            text="こんにちは",
            confidence=0.8,
            token_offsets=[(0, 2), (2, 5)],
        ),
        TranslationUnit(
            ordered_region_ids=["rgn_a", "rgn_b"],
            source_bundle="こんにちは",
            candidates=[candidate],
            selected_candidate_id=candidate.id,
        ),
        EditRecord(
            translation_unit_id="tu_x",
            before="Hi",
            after="Hello",
            action=EditAction.EDIT_TRANSLATION,
        ),
        RenderArtifact(page_id="pg_x", output_path="renders/003.png", params={"font": "noto"}),
    ]
    for instance in instances:
        _assert_round_trip(instance)


# --- Property-based round-trip -------------------------------------------------------------

_finite_floats = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6)
_non_negative = st.floats(allow_nan=False, allow_infinity=False, min_value=0.0, max_value=1e6)


@st.composite
def bboxes(draw: st.DrawFn) -> BBox:
    return BBox(
        x=draw(_finite_floats),
        y=draw(_finite_floats),
        width=draw(_non_negative),
        height=draw(_non_negative),
    )


@st.composite
def regions(draw: st.DrawFn) -> Region:
    return Region(
        page_id=draw(st.text(min_size=1, max_size=12)),
        bbox=draw(bboxes()),
        type=draw(st.sampled_from(list(RegionType))),
        status=draw(st.sampled_from(list(RegionStatus))),
        reading_order_index=draw(st.none() | st.integers(min_value=0, max_value=999)),
        confidence=draw(st.none() | st.floats(min_value=0, max_value=1)),
    )


@st.composite
def translation_units(draw: st.DrawFn) -> TranslationUnit:
    candidates = draw(
        st.lists(st.builds(TranslationCandidate, text=st.text(max_size=20)), max_size=4)
    )
    choices: list[str | None] = [c.id for c in candidates]
    choices.append(None)
    selected = draw(st.sampled_from(choices))
    return TranslationUnit(
        ordered_region_ids=draw(st.lists(st.text(min_size=1, max_size=8), max_size=5)),
        source_bundle=draw(st.text(max_size=40)),
        candidates=candidates,
        selected_candidate_id=selected,
    )


@given(bboxes())
def test_bbox_round_trip(bbox: BBox) -> None:
    _assert_round_trip(bbox)


@given(regions())
def test_region_round_trip(region: Region) -> None:
    _assert_round_trip(region)


@given(translation_units())
def test_translation_unit_round_trip(unit: TranslationUnit) -> None:
    _assert_round_trip(unit)
