"""
Tool definitions passed to Claude's tool_use API.
Each tool maps to a specialized agent method.
"""

TOOLS: list[dict] = [
    {
        "name": "run_sql_query",
        "description": (
            "Execute a SQL query on the dataset (BigQuery or local DuckDB). "
            "Use this to filter, aggregate, join, or explore data. "
            "Always prefer this when the question requires precise numeric answers from data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": (
                        "Valid SQL query. For BigQuery use fully qualified table names. "
                        "For uploaded CSV use the table alias 'df'. "
                        "Limit to 10000 rows unless aggregating."
                    ),
                },
                "purpose": {
                    "type": "string",
                    "description": "One sentence explaining what this query computes and why.",
                },
            },
            "required": ["sql", "purpose"],
        },
    },
    {
        "name": "run_statistical_analysis",
        "description": (
            "Perform statistical analysis on a column or pair of columns. "
            "Supports: descriptive stats, correlation, normality test, t-test, "
            "ANOVA, chi-square, linear regression."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "analysis_type": {
                    "type": "string",
                    "enum": [
                        "descriptive",
                        "correlation",
                        "normality_test",
                        "t_test",
                        "anova",
                        "chi_square",
                        "linear_regression",
                    ],
                    "description": "Type of statistical analysis to perform.",
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column names to include. 1 column for descriptive/normality, 2+ for others.",
                },
                "group_by": {
                    "type": "string",
                    "description": "Optional column name to group by (for t_test, anova).",
                },
                "sql_filter": {
                    "type": "string",
                    "description": "Optional WHERE clause to filter data before analysis.",
                },
            },
            "required": ["analysis_type", "columns"],
        },
    },
    {
        "name": "create_visualization",
        "description": (
            "Generate an interactive Plotly chart. Choose the chart type that best "
            "answers the question visually. For time series use 'line', for distributions "
            "use 'histogram' or 'box', for comparisons use 'bar', for relationships use 'scatter'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "line", "scatter", "histogram", "box", "heatmap", "pie", "area"],
                    "description": "Plotly chart type.",
                },
                "x_column": {
                    "type": "string",
                    "description": "Column for X axis.",
                },
                "y_column": {
                    "type": "string",
                    "description": "Column for Y axis (or value column for histogram/pie).",
                },
                "color_column": {
                    "type": "string",
                    "description": "Optional column for color grouping.",
                },
                "sql_query": {
                    "type": "string",
                    "description": (
                        "SQL query to prepare the data for this chart. "
                        "Must return columns matching x_column, y_column (and color_column if set). "
                        "Apply aggregations here."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Chart title.",
                },
                "aggregation": {
                    "type": "string",
                    "enum": ["none", "sum", "mean", "count", "median", "max", "min"],
                    "description": "How to aggregate y values if x has duplicates.",
                },
            },
            "required": ["chart_type", "x_column", "y_column", "sql_query", "title"],
        },
    },
    {
        "name": "run_forecast",
        "description": (
            "Forecast a time series column into the future. "
            "Supports Prophet (small series), N-HiTS (state-of-the-art neural), "
            "and DeepAR (probabilistic LSTM). "
            "Only use when the data has a clear date/time column and the user asks "
            "about future trends or predictions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_column": {
                    "type": "string",
                    "description": "Name of the date or datetime column.",
                },
                "value_column": {
                    "type": "string",
                    "description": "Numeric column to forecast.",
                },
                "horizon_days": {
                    "type": "integer",
                    "description": "Number of days to forecast into the future.",
                    "minimum": 7,
                    "maximum": 365,
                },
                "sql_query": {
                    "type": "string",
                    "description": (
                        "SQL query returning exactly two columns: date_column and value_column. "
                        "Apply any aggregation (e.g. daily totals) here."
                    ),
                },
                "model": {
                    "type": "string",
                    "enum": ["auto", "nhits", "deepar", "prophet"],
                    "description": (
                        "Forecasting model to use. "
                        "'auto' selects N-HiTS for large series (≥50 pts, ≥3×horizon) or Prophet otherwise. "
                        "'nhits' forces N-HiTS (fast neural, best accuracy on most benchmarks). "
                        "'deepar' forces DeepAR (probabilistic LSTM, good for long regular series). "
                        "'prophet' forces Prophet (best for short or irregular series). "
                        "Use 'deepar' or 'nhits' only when the user explicitly asks for a deep learning model."
                    ),
                },
            },
            "required": ["date_column", "value_column", "horizon_days", "sql_query"],
        },
    },
]


# LangChain-compatible version of TOOLS (uses "parameters" instead of "input_schema")
LC_TOOLS: list[dict] = [
    {**t, "parameters": t["input_schema"]}
    for t in [{k: v for k, v in tool.items() if k != "input_schema"} | {"input_schema": tool["input_schema"]}
              for tool in TOOLS]
]
# Simpler rewrite:
LC_TOOLS = [{k if k != "input_schema" else "parameters": v for k, v in tool.items()} for tool in TOOLS]


ROUTER_SYSTEM_PROMPT = """Tu es StatIQ, un assistant d'analyse de données expert.

Tu as accès aux outils suivants :
- **run_sql_query** : explorer, filtrer, agréger les données via SQL
- **run_statistical_analysis** : tests statistiques, corrélations, régressions
- **create_visualization** : générer des graphiques Plotly interactifs
- **run_forecast** : prévisions de séries temporelles (Prophet · N-HiTS · DeepAR)

## Stratégie d'analyse
1. Commence TOUJOURS par une requête SQL pour explorer les données si tu ne les connais pas encore.
2. Enchaîne les outils logiquement : SQL → Stats → Viz (dans cet ordre quand pertinent).
3. Génère UNE visualisation principale, la plus pertinente pour la question.
4. Si la question porte sur une tendance future, utilise run_forecast.
5. Après tous les tool_use, synthétise les résultats en un insight clair et actionnable.

## Règles
- Parle toujours en français sauf pour le code SQL.
- Sois précis sur les chiffres : cite les valeurs exactes issues des outils.
- Si les données sont insuffisantes, dis-le clairement.
- Ne fabrique JAMAIS de données : tout doit venir des outils.

## Contexte dataset
{dataset_context}
"""
