"""StatAgent — performs statistical analysis using scipy, statsmodels, pingouin."""
from __future__ import annotations
import pandas as pd
import scipy.stats as stats
import statsmodels.api as sm
import structlog
from backend.agents.base import DataLoader
from backend.models.schemas import StatResult

log = structlog.get_logger()


class StatAgent:
    def __init__(self, loader: DataLoader) -> None:
        self.loader = loader

    def run(
        self,
        analysis_type: str,
        columns: list[str],
        group_by: str | None = None,
        sql_filter: str | None = None,
    ) -> StatResult:
        log.info("stat_agent.run", type=analysis_type, columns=columns)

        # Load data
        if loader_table := getattr(self.loader, "_loaded_table", None):
            where = f"WHERE {sql_filter}" if sql_filter else ""
            col_sql = ", ".join(f'"{c}"' for c in columns)
            group_sql = f', "{group_by}"' if group_by else ""
            sql = f"SELECT {col_sql}{group_sql} FROM {loader_table} {where} LIMIT 50000"
        else:
            sql = f"SELECT {', '.join(columns)} LIMIT 50000"

        df, _ = self.loader.run_sql(sql)

        dispatch = {
            "descriptive": self._descriptive,
            "correlation": self._correlation,
            "normality_test": self._normality,
            "t_test": self._ttest,
            "anova": self._anova,
            "chi_square": self._chi_square,
            "linear_regression": self._regression,
        }
        handler = dispatch.get(analysis_type, self._descriptive)
        return handler(df, columns, group_by)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _descriptive(self, df: pd.DataFrame, columns: list[str], _) -> StatResult:
        numeric = df[columns].select_dtypes(include="number")
        desc = numeric.describe(percentiles=[0.25, 0.5, 0.75]).to_dict()
        # Add skewness and kurtosis
        for col in numeric.columns:
            desc[col]["skewness"] = round(float(numeric[col].skew()), 4)
            desc[col]["kurtosis"] = round(float(numeric[col].kurt()), 4)

        interp_parts = []
        for col, s in desc.items():
            mean_, std_ = s.get("mean", 0), s.get("std", 0)
            skew_ = s.get("skewness", 0)
            skew_label = "symétrique" if abs(skew_) < 0.5 else ("asymétrie positive" if skew_ > 0 else "asymétrie négative")
            interp_parts.append(
                f"**{col}** : moyenne={mean_:.2f}, écart-type={std_:.2f}, distribution {skew_label}."
            )

        return StatResult(
            analysis_type="descriptive",
            summary=desc,
            interpretation="\n".join(interp_parts),
        )

    def _correlation(self, df: pd.DataFrame, columns: list[str], _) -> StatResult:
        numeric = df[columns].select_dtypes(include="number")
        corr = numeric.corr(method="pearson")
        pval_matrix = {}
        for c1 in numeric.columns:
            pval_matrix[c1] = {}
            for c2 in numeric.columns:
                if c1 != c2:
                    _, p = stats.pearsonr(
                        numeric[c1].dropna(), numeric[c2].dropna()
                    )
                    pval_matrix[c1][c2] = round(float(p), 4)

        strong = [
            (c1, c2, round(float(corr.loc[c1, c2]), 3))
            for c1 in corr.columns
            for c2 in corr.columns
            if c1 < c2 and abs(corr.loc[c1, c2]) > 0.5
        ]
        interp = (
            "Corrélations fortes (|r|>0.5) : "
            + "; ".join(f"{a}↔{b} r={r}" for a, b, r in strong)
            if strong
            else "Aucune corrélation forte (|r|>0.5) détectée entre les variables sélectionnées."
        )

        return StatResult(
            analysis_type="correlation",
            summary={"pearson_r": corr.round(3).to_dict(), "p_values": pval_matrix},
            interpretation=interp,
        )

    def _normality(self, df: pd.DataFrame, columns: list[str], _) -> StatResult:
        col = columns[0]
        series = df[col].dropna()
        if len(series) > 5000:
            series = series.sample(5000, random_state=42)

        stat_sw, p_sw = stats.shapiro(series) if len(series) <= 5000 else (None, None)
        stat_ks, p_ks = stats.kstest(series, "norm", args=(series.mean(), series.std()))

        is_normal = bool((p_sw or 1.0) > 0.05 and p_ks > 0.05)
        interp = (
            f"**{col}** suit approximativement une distribution normale (Shapiro p={p_sw:.4f}, KS p={p_ks:.4f})."
            if is_normal
            else f"**{col}** ne suit PAS une distribution normale (Shapiro p={p_sw:.4f}, KS p={p_ks:.4f}). "
            "Utilisez des tests non-paramétriques."
        )

        return StatResult(
            analysis_type="normality_test",
            summary={
                "column": col,
                "shapiro_stat": round(float(stat_sw), 4) if stat_sw else None,
                "shapiro_p": round(float(p_sw), 4) if p_sw else None,
                "ks_stat": round(float(stat_ks), 4),
                "ks_p": round(float(p_ks), 4),
                "is_normal": is_normal,
            },
            interpretation=interp,
        )

    def _ttest(self, df: pd.DataFrame, columns: list[str], group_by: str | None) -> StatResult:
        target = columns[0]
        if group_by and group_by in df.columns:
            groups = df.groupby(group_by)[target].apply(list)
            if len(groups) >= 2:
                g1, g2 = groups.iloc[0], groups.iloc[1]
                t_stat, p_val = stats.ttest_ind(g1, g2, equal_var=False)
                interp = (
                    f"Test t de Welch entre les groupes de **{group_by}** sur **{target}** : "
                    f"t={t_stat:.3f}, p={p_val:.4f}. "
                    + ("Différence significative (p<0.05)." if p_val < 0.05 else "Pas de différence significative (p≥0.05).")
                )
                return StatResult(
                    analysis_type="t_test",
                    summary={"t_statistic": round(t_stat, 4), "p_value": round(p_val, 4), "groups": groups.index.tolist()},
                    interpretation=interp,
                )
        return StatResult(
            analysis_type="t_test",
            summary={"error": f"group_by column '{group_by}' not found or fewer than 2 groups in data."},
            interpretation=f"Impossible d'effectuer le test t : colonne de groupe '{group_by}' absente ou données insuffisantes.",
        )

    def _anova(self, df: pd.DataFrame, columns: list[str], group_by: str | None) -> StatResult:
        target = columns[0]
        if not group_by or group_by not in df.columns:
            return self._descriptive(df, columns, None)
        groups = [g[target].dropna().tolist() for _, g in df.groupby(group_by)]
        f_stat, p_val = stats.f_oneway(*groups)
        interp = (
            f"ANOVA sur **{target}** par **{group_by}** : F={f_stat:.3f}, p={p_val:.4f}. "
            + ("Au moins un groupe diffère significativement (p<0.05)." if p_val < 0.05 else "Pas de différence entre les groupes (p≥0.05).")
        )
        return StatResult(
            analysis_type="anova",
            summary={"f_statistic": round(f_stat, 4), "p_value": round(p_val, 4), "n_groups": len(groups)},
            interpretation=interp,
        )

    def _chi_square(self, df: pd.DataFrame, columns: list[str], _) -> StatResult:
        if len(columns) < 2:
            return self._descriptive(df, columns, None)
        ct = pd.crosstab(df[columns[0]], df[columns[1]])
        chi2, p, dof, _ = stats.chi2_contingency(ct)
        interp = (
            f"Test χ² entre **{columns[0]}** et **{columns[1]}** : χ²={chi2:.3f}, ddl={dof}, p={p:.4f}. "
            + ("Dépendance significative (p<0.05)." if p < 0.05 else "Pas de dépendance significative (p≥0.05).")
        )
        return StatResult(
            analysis_type="chi_square",
            summary={"chi2": round(chi2, 4), "p_value": round(p, 4), "dof": dof},
            interpretation=interp,
        )

    def _regression(self, df: pd.DataFrame, columns: list[str], _) -> StatResult:
        if len(columns) < 2:
            return self._descriptive(df, columns, None)
        y = df[columns[-1]].dropna()
        X = df[columns[:-1]].dropna()
        common = X.index.intersection(y.index)
        X, y = X.loc[common], y.loc[common]
        X_c = sm.add_constant(X)
        model = sm.OLS(y, X_c).fit()

        interp = (
            f"Régression OLS : R²={model.rsquared:.3f}, R²adj={model.rsquared_adj:.3f}, "
            f"F-stat={model.fvalue:.3f} (p={model.f_pvalue:.4f}). "
        )
        sig_vars = [p for p, pv in model.pvalues.items() if pv < 0.05 and p != "const"]
        if sig_vars:
            interp += f"Variables significatives (p<0.05) : {', '.join(sig_vars)}."

        return StatResult(
            analysis_type="linear_regression",
            summary={
                "r_squared": round(model.rsquared, 4),
                "adj_r_squared": round(model.rsquared_adj, 4),
                "f_statistic": round(model.fvalue, 4),
                "f_pvalue": round(model.f_pvalue, 4),
                "coefficients": {k: round(v, 4) for k, v in model.params.items()},
                "p_values": {k: round(v, 4) for k, v in model.pvalues.items()},
                "aic": round(model.aic, 2),
                "bic": round(model.bic, 2),
            },
            interpretation=interp,
        )
