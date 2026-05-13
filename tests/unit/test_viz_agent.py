"""Unit tests for VizAgent."""
import json
import pandas as pd
import numpy as np
import pytest

from backend.agents.base import DataLoader
from backend.agents.viz_agent import VizAgent
from backend.models.schemas import DataSourceType


@pytest.fixture
def loader_with_data():
    np.random.seed(0)
    n = 200
    df = pd.DataFrame({
        "month": pd.date_range("2023-01-01", periods=n, freq="D").strftime("%Y-%m"),
        "revenue": np.random.normal(1000, 200, n).clip(0),
        "category": np.random.choice(["A", "B", "C"], n),
        "count": np.random.randint(1, 100, n),
    })
    loader = DataLoader()
    loader.duck.register("df", df)
    loader._loaded_table = "df"
    loader._source_type = DataSourceType.CSV_UPLOAD
    loader._dataset_ref = "test_viz"
    return loader


def test_bar_chart(loader_with_data):
    agent = VizAgent(loader_with_data)
    result = agent.run(
        chart_type="bar",
        x_column="category",
        y_column="revenue",
        sql_query="SELECT category, AVG(revenue) as revenue FROM df GROUP BY category",
        title="Revenue par catégorie",
    )
    assert result.chart_type == "bar"
    assert result.plotly_json
    fig_data = json.loads(result.plotly_json)
    assert "data" in fig_data


def test_line_chart(loader_with_data):
    agent = VizAgent(loader_with_data)
    result = agent.run(
        chart_type="line",
        x_column="month",
        y_column="revenue",
        sql_query="SELECT month, SUM(revenue) as revenue FROM df GROUP BY month ORDER BY month",
        title="Revenu mensuel",
    )
    assert result.chart_type == "line"
    assert json.loads(result.plotly_json)


def test_scatter_chart(loader_with_data):
    agent = VizAgent(loader_with_data)
    result = agent.run(
        chart_type="scatter",
        x_column="count",
        y_column="revenue",
        sql_query="SELECT count, revenue FROM df LIMIT 200",
        title="Count vs Revenue",
    )
    assert result.chart_type == "scatter"
