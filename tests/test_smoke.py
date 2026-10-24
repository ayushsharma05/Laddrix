"""The package imports: the smallest proof the install works."""

import mocktrade


def test_package_imports():
    assert mocktrade.__version__
