from utilities.object_identity import configured_source_id, relationship_id, relationship_key, view_reference_key


def test_configured_sources_are_process_specific():
    assert configured_source_id("process::A", "TM1_CUBE_VIEW", "source::1") != configured_source_id("process::B", "TM1_CUBE_VIEW", "source::1")


def test_relationship_tuple_is_stable_and_source_specific():
    first = relationship_id(relationship_key("process::A", "USES_VIEW", "view::C::PUBLIC::<none>::V", "SEMANTIC_USAGE"))
    repeated = relationship_id(relationship_key("process::A", "USES_VIEW", "view::C::PUBLIC::<none>::V", "SEMANTIC_USAGE"))
    other = relationship_id(relationship_key("process::B", "USES_VIEW", "view::C::PUBLIC::<none>::V", "SEMANTIC_USAGE"))
    assert first == repeated
    assert first != other


def test_view_reference_includes_cube_and_view():
    assert view_reference_key("Cube A", "Temp") != view_reference_key("Cube B", "Temp")
