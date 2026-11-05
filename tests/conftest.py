from __future__ import annotations

import os

import pytest

from backend.runtime.application import factory
from tests.testing_sandbox import DirectTestSandboxLauncher


def pytest_collection_modifyitems(items):
    """Protected Broker objects require a genuinely elevated Windows token."""
    elevated = False
    if os.name == "nt":
        import win32security

        elevated = bool(
            win32security.CheckTokenMembership(
                None, win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None)
            )
        )
    if not elevated:
        for item in items:
            if item.get_closest_marker("windows_admin"):
                item.add_marker(
                    pytest.mark.skip(reason="Requires an elevated Windows token for protected Broker objects")
                )


@pytest.fixture
def local_sandbox_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the explicit local test launcher when a test does not exercise Broker isolation."""

    monkeypatch.setattr(
        factory,
        "_sandbox_runtime",
        lambda _config, **_kwargs: (
            DirectTestSandboxLauncher(),
            {},
        ),
    )
