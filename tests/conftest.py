import os

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.getenv("SECRECON_INTEGRATION") != "1":
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(
                    pytest.mark.skip(reason="Set SECRECON_INTEGRATION=1 for real services")
                )
