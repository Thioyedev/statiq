"""
ForecastAgent — multi-model time-series forecasting.

Model selection strategy
------------------------
"auto"   → N-HiTS if n ≥ 3×horizon, else Prophet
"nhits"  → N-HiTS  (Nixtla neuralforecast — state-of-the-art accuracy)
"deepar" → DeepAR  (probabilistic LSTM, Amazon Research)
"prophet"→ Prophet (best for short / irregular series)

N-HiTS vs DeepAR
-----------------
N-HiTS wins on most M4/M5 benchmarks and trains 50× faster on CPU.
DeepAR is better when you need a full predictive distribution and
have long, regular series (≥ 500 points).
"""
from __future__ import annotations

import warnings
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import structlog

from backend.agents.base import DataLoader
from backend.models.schemas import ForecastResult

warnings.filterwarnings("ignore")
log = structlog.get_logger()

# ── constants ─────────────────────────────────────────────────────────────────
NEURAL_MIN_ROWS_RATIO = 3   # need at least 3×horizon rows for neural models
NEURAL_ABS_MIN = 50         # hard floor


# ── helpers ───────────────────────────────────────────────────────────────────

def _detect_freq(dates: pd.Series) -> str:
    """Infer pandas/neuralforecast frequency string from a date series."""
    diffs = dates.sort_values().diff().dropna()
    if diffs.empty:
        return "D"
    median_days = diffs.median().days
    if median_days <= 1:
        return "D"
    if median_days <= 8:
        return "W"
    if median_days <= 35:
        return "MS"
    if median_days <= 100:
        return "QS"
    return "YS"


def _build_figure(
    history: pd.DataFrame,
    forecast_dates: pd.Series,
    forecast_mean: pd.Series,
    lower: pd.Series,
    upper: pd.Series,
    value_column: str,
    horizon_days: int,
    model_label: str,
) -> go.Figure:
    """Return a styled Plotly figure with history + forecast + confidence band."""
    fig = go.Figure()

    # Historical series
    fig.add_trace(go.Scatter(
        x=history["ds"], y=history["y"],
        mode="lines", name="Historique",
        line=dict(color="#00E5FF", width=1.5),
    ))

    # Confidence band (filled area)
    fig.add_trace(go.Scatter(
        x=pd.concat([forecast_dates, forecast_dates[::-1]]),
        y=pd.concat([upper, lower[::-1]]),
        fill="toself",
        fillcolor="rgba(255,107,107,0.15)",
        line=dict(color="rgba(255,107,107,0)"),
        name="Intervalle 90%",
    ))

    # Forecast line
    fig.add_trace(go.Scatter(
        x=forecast_dates, y=forecast_mean,
        mode="lines", name=f"Prévision ({model_label})",
        line=dict(color="#FF6B6B", width=2.5),
    ))

    fig.update_layout(
        title=f"Prévision {value_column} — {horizon_days} jours · {model_label}",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(17,24,39,0.8)",
        font=dict(color="#E2E8F0"),
        xaxis=dict(gridcolor="#1E2D40"),
        yaxis=dict(gridcolor="#1E2D40"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
    return fig


# ── main agent ────────────────────────────────────────────────────────────────

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
        log.info("forecast_agent.run", col=value_column, horizon=horizon_days, model=model)

        df_raw, _ = self.loader.run_sql(sql_query, max_rows=100_000)

        # ── Prepare clean time series ─────────────────────────────────────────
        df = df_raw[[date_column, value_column]].rename(
            columns={date_column: "ds", value_column: "y"}
        )
        df["ds"] = pd.to_datetime(df["ds"], errors="coerce")
        df["y"] = pd.to_numeric(df["y"], errors="coerce")
        df = df.dropna().sort_values("ds").reset_index(drop=True)

        n = len(df)
        if n < 10:
            raise ValueError("Pas assez de données pour une prévision (minimum 10 points).")

        # ── Model selection ───────────────────────────────────────────────────
        # "auto" always uses Prophet: reliable, memory-safe (<512 MB), fast on CPU.
        # Neural models (nhits/deepar) are available only when explicitly requested.
        neural_ok = (n >= NEURAL_ABS_MIN) and (n >= NEURAL_MIN_ROWS_RATIO * horizon_days)

        if model == "auto":
            chosen = "prophet"
        elif model in ("nhits", "deepar") and not neural_ok:
            log.warning(
                "forecast.fallback",
                reason=f"only {n} rows (need {NEURAL_MIN_ROWS_RATIO * horizon_days}), falling back to Prophet",
            )
            chosen = "prophet"
        else:
            chosen = model

        log.info("forecast_agent.model_chosen", chosen=chosen, n_rows=n)

        if chosen == "prophet":
            return self._run_prophet(df, value_column, horizon_days)
        return self._run_neural(df, value_column, horizon_days, model=chosen)

    # ── Prophet ───────────────────────────────────────────────────────────────

    def _run_prophet(
        self,
        df: pd.DataFrame,
        value_column: str,
        horizon_days: int,
    ) -> ForecastResult:
        try:
            from prophet import Prophet
        except ImportError:
            raise RuntimeError("Prophet non installé. Lancez: pip install prophet")

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

        # In-sample metrics
        train_pred = forecast[forecast["ds"].isin(df["ds"])]["yhat"].values
        actuals = df["y"].values[: len(train_pred)]
        mae = float(np.mean(np.abs(actuals - train_pred)))
        rmse = float(np.sqrt(np.mean((actuals - train_pred) ** 2)))

        future_mask = forecast["ds"] > df["ds"].max()
        fc = forecast[future_mask].head(horizon_days)

        fig = _build_figure(
            df, fc["ds"], fc["yhat"], fc["yhat_lower"], fc["yhat_upper"],
            value_column, horizon_days, "Prophet",
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

    # ── Neural (N-HiTS / DeepAR) ──────────────────────────────────────────────

    def _run_neural(
        self,
        df: pd.DataFrame,
        value_column: str,
        horizon_days: int,
        model: str,
    ) -> ForecastResult:
        try:
            from neuralforecast import NeuralForecast
            from neuralforecast.models import DeepAR, NHITS
            from neuralforecast.losses.pytorch import DistributionLoss, MQLoss
        except ImportError:
            raise RuntimeError("neuralforecast non installé. Lancez: pip install neuralforecast")

        freq = _detect_freq(df["ds"])
        input_size = max(2 * horizon_days, min(60, len(df) // 3))

        # neuralforecast expects: unique_id, ds, y
        df_nf = df.copy()
        df_nf["unique_id"] = "series"

        if model == "deepar":
            model_obj = DeepAR(
                h=horizon_days,
                input_size=input_size,
                loss=DistributionLoss("Normal", level=[80, 95]),
                max_steps=150,
                enable_progress_bar=False,
                enable_model_summary=False,
            )
            label = "DeepAR"
            lo_col, hi_col = "DeepAR-lo-90", "DeepAR-hi-90"
            mean_col = "DeepAR"
        else:  # nhits
            model_obj = NHITS(
                h=horizon_days,
                input_size=input_size,
                loss=MQLoss(level=[80, 95]),
                max_steps=300,
                enable_progress_bar=False,
                enable_model_summary=False,
            )
            label = "N-HiTS"
            lo_col, hi_col = "NHITS-lo-90", "NHITS-hi-90"
            mean_col = "NHITS"

        nf = NeuralForecast(models=[model_obj], freq=freq)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            nf.fit(df_nf)
            pred = nf.predict().reset_index()

        # Rename columns defensively (neuralforecast column naming can vary)
        pred_cols = pred.columns.tolist()
        mean_col = next((c for c in pred_cols if c not in ("unique_id", "ds") and "lo" not in c and "hi" not in c), pred_cols[-1])
        lo_col   = next((c for c in pred_cols if "lo-90" in c or "lo-80" in c), mean_col)
        hi_col   = next((c for c in pred_cols if "hi-90" in c or "hi-80" in c), mean_col)

        # In-sample metrics via cross-validation (fast 1-fold)
        try:
            cv = nf.cross_validation(df_nf, n_windows=1).reset_index()
            cv_mean = cv[mean_col] if mean_col in cv.columns else cv.iloc[:, -1]
            mae  = float(np.mean(np.abs(cv["y"].values - cv_mean.values)))
            rmse = float(np.sqrt(np.mean((cv["y"].values - cv_mean.values) ** 2)))
        except Exception:
            mae, rmse = float("nan"), float("nan")

        fig = _build_figure(
            df, pred["ds"], pred[mean_col], pred[lo_col], pred[hi_col],
            value_column, horizon_days, label,
        )

        records = (
            pred[["ds", mean_col, lo_col, hi_col]]
            .rename(columns={"ds": "date", mean_col: "forecast", lo_col: "lower_90", hi_col: "upper_90"})
            .assign(date=lambda x: pd.to_datetime(x["date"]).dt.strftime("%Y-%m-%d"))
            .round(2)
            .to_dict(orient="records")
        )
        return ForecastResult(
            target_column=value_column,
            horizon_days=horizon_days,
            forecast_df=records,
            mae=round(mae, 4) if not np.isnan(mae) else None,
            rmse=round(rmse, 4) if not np.isnan(rmse) else None,
            plotly_json=fig.to_json(),
        )
