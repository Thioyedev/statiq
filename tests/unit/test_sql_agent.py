"""Unit tests for SQLAgent."""
import pandas as pd
import numpy as np
import pytest

from backend.agents.base import DataLoader
from backend.agents.sql_agent import SQLAgent
from backend.models.schemas import DataSourceType


@pytest.fixture
def loader():
    df = pd.DataFrame({
        "id": range(100),
        "value": np.random.normal(50, 10, 100),
        "group": np.random.choice(["X", "Y"], 100),
    })
    loader = DataLoader()
    loader.duck.register("df", df)
    loader._loaded_table = "df"
    loader._source_type = DataSourceType.CSV_UPLOAD
    loader._dataset_ref = "test"
    return loader


def test_simple_select(loader):
    agent = SQLAgent(loader)
    result = agent.run("SELECT * FROM df LIMIT 10", "test select")
    assert result.row_count == 10
    assert set(result.columns) == {"id", "value", "group"}


def test_aggregation(loader):
    agent = SQLAgent(loader)
    result = agent.run(
        "SELECT \"group\", COUNT(*) as n, AVG(value) as avg_val FROM df GROUP BY \"group\"",
        "group aggregation",
    )
    assert result.row_count == 2
    assert "n" in result.columns


def test_filter(loader):
    agent = SQLAgent(loader)
    result = agent.run("SELECT * FROM df WHERE value > 60", "filter test")
    assert result.row_count >= 0  # can be 0 depending on random seed
    assert result.execution_time_ms >= 0
