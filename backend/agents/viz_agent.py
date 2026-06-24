"""VizAgent — generates interactive Plotly charts from SQL data."""
from __future__ import annotations
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import structlog
import pandas as pd
from backend.agents.base import DataLoader
from backend.models.schemas import VizResult

log = structlog.get_logger()

# Clean template for dark UI
pio.templates["statiq"] = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(17,24,39,0.8)",
        font=dict(color="#E2E8F0", family="IBM Plex Mono, monospace"),
        colorway=["#00E5FF", "#FF6B6B", "#69FF47", "#FFD93D", "#C77DFF", "#FF9A3C"],
        xaxis=dict(gridcolor="#1E2D40", linecolor="#1E2D40"),
        yaxis=dict(gridcolor="#1E2D40", linecolor="#1E2D40"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
)


class VizAgent:
    def __init__(self, loader: DataLoader) -> None:
        self.loader = loader

    def run(
        self,
        chart_type: str,
        x_column: str,
        y_column: str,
        sql_query: str,
        title: str,
        color_column: str | None = None,
        aggregation: str = "none",
    ) -> VizResult:
        log.info("viz_agent.run", chart=chart_type, title=title)

        df, _ = self.loader.run_sql(sql_query, max_rows=5_000)

        # Apply aggregation if needed and not already done in SQL
        if aggregation not in ("none", None) and x_column in df.columns and y_column in df.columns:
            agg_func = {"sum": "sum", "mean": "mean", "count": "count", "median": "median",
                        "max": "max", "min": "min"}.get(aggregation, "mean")
            grp_cols = [x_column] + ([color_column] if color_column and color_column in df.columns else [])
            df = df.groupby(grp_cols)[y_column].agg(agg_func).reset_index()

        fig = self._build_chart(df, chart_type, x_column, y_column, color_column, title)
        fig.update_layout(template="statiq", margin=dict(l=40, r=20, t=50, b=40))

        return VizResult(
            chart_type=chart_type,
            plotly_json=fig.to_json(),
            title=title,
            description=f"Graphique {chart_type} : {y_column} par {x_column}",
        )

    def _build_chart(
        self,
        df: pd.DataFrame,
        chart_type: str,
        x: str,
        y: str,
        color: str | None,
        title: str,
    ) -> go.Figure:
        kw = dict(data_frame=df, x=x, y=y, title=title)
        if color and color in df.columns:
            kw["color"] = color

        match chart_type:
            case "bar":
                return px.bar(**kw, barmode="group")
            case "line":
                return px.line(**kw, markers=True)
            case "area":
                return px.area(**kw)
            case "scatter":
                return px.scatter(**kw, opacity=0.7)
            case "histogram":
                return px.histogram(df, x=x, title=title, nbins=50,
                                    color=color if color and color in df.columns else None)
            case "box":
                return px.box(**kw)
            case "pie":
                return px.pie(df, names=x, values=y, title=title)
            case "heatmap":
                if color and color in df.columns:
                    pivot = df.pivot_table(index=x, columns=color, values=y, aggfunc="mean")
                    return px.imshow(pivot, title=title, color_continuous_scale="RdYlBu_r")
                return px.bar(**kw)
            case _:
                return px.bar(**kw)
