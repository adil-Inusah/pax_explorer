from utilities.object_resolution import ObjectResolver


def objects():
    return [
        {"object_id": "view::Cube A::PUBLIC::<none>::View 1", "object_type": "VIEW", "properties": {"cube_name": "Cube A", "view_name": "View 1"}},
        {"object_id": "view::Cube B::PUBLIC::<none>::View 1", "object_type": "VIEW", "properties": {"cube_name": "Cube B", "view_name": "View 1"}},
    ]


def test_exact_view_resolution():
    result = ObjectResolver(objects()).resolve_view("Cube A", "View 1")
    assert result["resolution_status"] == "CANONICAL_EXACT_MATCH"
    assert result["resolved_target_object_id"] == "view::Cube A::PUBLIC::<none>::View 1"


def test_missing_public_view_remains_unresolved():
    result = ObjectResolver(objects()).resolve_view("Cube A", "Temp")
    assert result["resolution_status"] == "TARGET_NOT_IN_PUBLIC_CATALOG"
    assert result["resolved_target_object_id"] is None
