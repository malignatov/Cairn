import pytest

from meta_assistant.storage import Storage


@pytest.fixture
def storage(tmp_path) -> Storage:
    return Storage(tmp_path / "test.db")
