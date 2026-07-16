"""ForecastAgent — time-series forecasting with Prophet."""
from __future__ import annotations

import warnings
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import structlog

from backend.agents.base import DataLoader
from backend.models.schemas import ForecastResult

warnings.filterwarnings("ignore")
log = structlog.get_logger()


def _build_figure(
    history: pd.DataFrame,
    forecast_dates: pd.Series,
    forecast_mean: pd.Series,
    lower: pd.Series,
    upper: pd.Series,
    value_column: str,
    horizon_days: int,
) -> go.Figure:
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=history["ds"], y=history["y"],
        mode="lines", name="Historique",
        line=dict(color="#00E5FF", width=1.5),
    ))

    fig.add_trace(go.Scatter(
        x=pd.concat([forecast_dates, forecast_dates[::-1]]),
        y=pd.concat([upper, lower[::-1]]),
        fill="toself",
        fillcolor="rgba(255,107,107,0.15)",
        line=dict(color="rgba(255,107,107,0)"),
        name="Intervalle 90%",
    ))

    fig.add_trace(go.Scatter(
        x=forecast_dates, y=forecast_mean,
        mode="lines", name="Prévision (Prophet)",
        line=dict(color="#FF6B6B", width=2.5),
    ))

    fig.update_layout(
        title=f"Prévision {value_column} — {horizon_days} jours · Prophet",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(17,24,39,0.8)",
        font=dict(color="#E2E8F0"),
        xaxis=dict(gridcolor="#1E2D40"),
        yaxis=dict(gridcolor="#1E2D40"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
    return fig


class ForecastAgent:
    def __init__(self, loader: DataLoader) -> None:
        self.loader = loader

    def run(
        self,
        date_column: str,
        value_column: str,
        horizon_days: int,
        sql_query: str,
        model: str = "auto",
    ) -> ForecastResult:
        log.info("forecast_agent.run", col=value_column, horizon=horizon_days)

        df_raw, _ = self.loader.run_sql(sql_query, max_rows=100_000)

        df = df_raw[[date_column, value_column]].rename(
            columns={date_column: "ds", value_column: "y"}
        )
        df["ds"] = pd.to_datetime(df["ds"], errors="coerce")
        df["y"] = pd.to_numeric(df["y"], errors="coerce")
        df = df.dropna().sort_values("ds").reset_index(drop=True)

        if len(df) < 10:
            raise ValueError("Pas assez de données pour une prévision (minimum 10 points).")

        return self._run_prophet(df, value_column, horizon_days)

    def _run_prophet(
        self,
        df: pd.DataFrame,
        value_column: str,
        horizon_days: int,
    ) -> ForecastResult:
        from prophet import Prophet

        m = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=False,
            interval_width=0.90,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(df)

        future = m.make_future_dataframe(periods=horizon_days)
        forecast = m.predict(future)

        train_pred = forecast[forecast["ds"].isin(df["ds"])]["yhat"].values
        actuals = df["y"].values[: len(train_pred)]
        mae = float(np.mean(np.abs(actuals - train_pred)))
        rmse = float(np.sqrt(np.mean((actuals - train_pred) ** 2)))

        future_mask = forecast["ds"] > df["ds"].max()
        fc = forecast[future_mask].head(horizon_days)

        fig = _build_figure(
            df, fc["ds"], fc["yhat"], fc["yhat_lower"], fc["yhat_upper"],
            value_column, horizon_days,
        )

        records = (
            fc[["ds", "yhat", "yhat_lower", "yhat_upper"]]
            .rename(columns={"ds": "date", "yhat": "forecast", "yhat_lower": "lower_90", "yhat_upper": "upper_90"})
            .assign(date=lambda x: x["date"].dt.strftime("%Y-%m-%d"))
            .round(2)
            .to_dict(orient="records")
        )
        return ForecastResult(
            target_column=value_column,
            horizon_days=horizon_days,
            forecast_df=records,
            mae=round(mae, 4),
            rmse=round(rmse, 4),
            plotly_json=fig.to_json(),
        )
