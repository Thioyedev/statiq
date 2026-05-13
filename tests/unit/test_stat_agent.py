"""Unit tests for StatAgent — no GCP / Redis dependencies."""
import pandas as pd
import numpy as np
import pytest
import duckdb

from backend.agents.base import DataLoader
from backend.agents.stat_agent import StatAgent
from backend.models.schemas import DataSourceType


@pytest.fixture
def loader_with_data():
    """DataLoader preloaded with a synthetic DataFrame via DuckDB."""
    np.random.seed(42)
    n = 500
    df = pd.DataFrame({
        "trip_duration": np.random.gamma(shape=2, scale=10, size=n),
        "fare": np.random.normal(15, 5, n).clip(2),
        "distance": np.random.exponential(3, n),
        "payment_type": np.random.choice(["cash", "card", "app"], n),
    })
    loader = DataLoader()
    loader.duck.register("df", df)
    loader._loaded_table = "df"
    loader._source_type = DataSourceType.CSV_UPLOAD
    loader._dataset_ref = "test_data"
    return loader


def test_descriptive(loader_with_data):
    agent = StatAgent(loader_with_data)
    result = agent.run("descriptive", ["trip_duration", "fare"])
    assert result.analysis_type == "descriptive"
    assert "trip_duration" in result.summary
    assert "mean" in result.summary["trip_duration"]
    assert len(result.interpretation) > 10


def test_correlation(loader_with_data):
    agent = StatAgent(loader_with_data)
    result = agent.run("correlation", ["trip_duration", "fare", "distance"])
    assert result.analysis_type == "correlation"
    assert "pearson_r" in result.summary


def test_normality(loader_with_data):
    agent = StatAgent(loader_with_data)
    result = agent.run("normality_test", ["fare"])
    assert "is_normal" in result.summary
    assert isinstance(result.summary["is_normal"], bool)


def test_ttest(loader_with_data):
    agent = StatAgent(loader_with_data)
    result = agent.run("t_test", ["fare"], group_by="payment_type")
    assert result.analysis_type == "t_test"
    assert "p_value" in result.summary


def test_regression(loader_with_data):
    agent = StatAgent(loader_with_data)
    result = agent.run("linear_regression", ["distance", "fare"])
    assert "r_squared" in result.summary
    assert result.summary["r_squared"] >= 0
