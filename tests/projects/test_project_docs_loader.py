import pytest

from operator_core.projects.docs import (
    ALL_DOC_TYPES,
    ProjectDoc,
    ProjectDocNotFoundError,
    ProjectDocsLoader,
    UnknownProjectDocTypeError,
)


@pytest.fixture()
def loader() -> ProjectDocsLoader:
    return ProjectDocsLoader()


class TestLoadSingle:
    def test_loads_project_state(self, loader: ProjectDocsLoader) -> None:
        doc = loader.load("everydayengel", "project_state")
        assert isinstance(doc, ProjectDoc)
        assert doc.project_key == "everydayengel"
        assert doc.doc_type == "project_state"
        assert not doc.is_empty
        assert "everydayengel" in doc.content.lower()

    def test_loads_content_rules(self, loader: ProjectDocsLoader) -> None:
        doc = loader.load("everydayengel", "content_rules")
        assert not doc.is_empty
        assert "content" in doc.content.lower()

    def test_loads_monetization_rules(self, loader: ProjectDocsLoader) -> None:
        doc = loader.load("everydayengel", "monetization_rules")
        assert not doc.is_empty
        assert "monetization" in doc.content.lower() or "monetisierung" in doc.content.lower()

    def test_loads_operational_semantics(self, loader: ProjectDocsLoader) -> None:
        doc = loader.load("everydayengel", "operational_semantics")
        assert not doc.is_empty
        assert "content_stage" in doc.content or "object" in doc.content.lower()

    def test_path_points_to_real_file(self, loader: ProjectDocsLoader) -> None:
        doc = loader.load("everydayengel", "project_state")
        assert doc.path.exists()
        assert doc.path.name == "project-state.md"

    def test_excerpt_truncates_long_content(self, loader: ProjectDocsLoader) -> None:
        doc = loader.load("everydayengel", "project_state")
        excerpt = doc.excerpt(max_chars=50)
        assert len(excerpt) <= 53  # 50 + ellipsis


class TestLoadAll:
    def test_returns_all_four_doc_types(self, loader: ProjectDocsLoader) -> None:
        docs = loader.load_all("everydayengel")
        assert set(docs.keys()) == set(ALL_DOC_TYPES)

    def test_all_docs_have_content(self, loader: ProjectDocsLoader) -> None:
        docs = loader.load_all("everydayengel")
        for doc_type, doc in docs.items():
            assert not doc.is_empty, f"{doc_type} is empty"

    def test_all_docs_have_correct_project_key(self, loader: ProjectDocsLoader) -> None:
        docs = loader.load_all("everydayengel")
        for doc in docs.values():
            assert doc.project_key == "everydayengel"


class TestAvailableDocTypes:
    def test_everydayengel_has_all_four_types(self, loader: ProjectDocsLoader) -> None:
        available = loader.available_doc_types("everydayengel")
        assert set(available) == set(ALL_DOC_TYPES)

    def test_unknown_project_returns_empty(self, loader: ProjectDocsLoader) -> None:
        available = loader.available_doc_types("nonexistent_project_xyz")
        assert available == []


class TestErrorCases:
    def test_unknown_project_raises_doc_not_found(self, loader: ProjectDocsLoader) -> None:
        with pytest.raises(ProjectDocNotFoundError) as exc_info:
            loader.load("nonexistent_project_xyz", "project_state")
        assert "nonexistent_project_xyz" in str(exc_info.value)

    def test_unknown_doc_type_raises_unknown_type_error(self, loader: ProjectDocsLoader) -> None:
        with pytest.raises(UnknownProjectDocTypeError) as exc_info:
            loader.load("everydayengel", "invalid_doc_type")  # type: ignore[arg-type]
        assert "invalid_doc_type" in str(exc_info.value)
