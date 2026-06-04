# Regression test: a requirements.txt-only project (no pyproject.toml) must have
# its dependencies installed by `pytest.test`. cowsay is declared only in
# requirements.txt and is absent from the default base.
import cowsay


def test_third_party_dependency_is_installed():
    assert hasattr(cowsay, "cow")
