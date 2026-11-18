from __future__ import annotations

import pytest

from gather.config import Settings, load_settings

from support import build_workspace


@pytest.fixture()
def settings(tmp_path) -> Settings:
    data = tmp_path / "data"
    return load_settings(
        data_dir=data,
        db_path=data / "gather.db",
        checkpoints_path=data / "checkpoints.db",
        auto_approve=False,
    )


@pytest.fixture()
def env(settings):
    workspace = build_workspace(settings)
    try:
        yield workspace.as_env()
    finally:
        workspace.close()
