"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def sample_embedding_dim() -> int:
    return 1024
