"""Auditoría de compras: ¿compramos bien? ¿qué nos falta?

Cruza compras, ventas, stock y tránsito de Profit para responder:
* equilibrio global compra/venta,
* margen bruto,
* capital inmovilizado vs ventas en riesgo,
* desviaciones por producto, proveedor, categoría, comprador y mes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from data_processing.profit_loader import ProfitData
from utils.config import Config


@dataclass
class AuditResult:
    year: int
    as_of: pd.Timestamp
    products: pd.DataFrame
    suppliers: pd.DataFrame
    categories: pd.DataFrame
    buyers: pd.DataFrame
    monthly: pd.DataFrame
    price_variance: pd.DataFrame
    metrics: dict[str, Any]
    findings: list[str]
    quality: pd.DataFrame
    warehouse_info: dict[str, Any] = field(default_factory=dict)


class PurchaseAudit:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")
        self.target_days = int(config.get("audit.target_coverage_days", 90))
        self.critical_days = int(config.get("audit.critical_coverage_days", 15))
        self.low_days = int(config.get("audit.low_coverage_days", 30))
        self.overbuy_days = int(config.get("audit.overbuy_coverage_days", 180))
        self.lead_days = int(config.get("audit.default_lead_time_days", 30))
        self.top_n = int(config.get("audit.top_n", 20))

    # ------------------------------------------------------------------ público
    def run(self, data: ProfitData, year: int | None = None) -> AuditResult:
        year = int(year or data.as_of.year)
        start = pd.Timestamp(year=year, month=1, day=1)
        as_of = pd.Timestamp(data.as_of)
        if as_of > pd.Timestamp(year=year, month=12, day=31):
            as_of = pd.Timestamp(year=year, month=12, day=31)

        purchases = data.purchases[data.purchases["TipoArticulo"] == "V"].copy()
        sales = data.sales[data.sales["TipoArticulo"] == "V"].copy()
        p_year = purchases[(purchases["Fecha"] >= start) & (purchases["Fecha"] <= as_of)]
        s_year = sales[(sales["Fecha"] >= start) & (sales["Fecha"] <= as_of)]
        s90 = sales[(sales["Fecha"] >= as_of - pd.Timedelta(days=90)) & (sales["Fecha"] <= as_of)]
        p_prev = purchases[(purchases["Fecha"] >= start - pd.Timedelta(days=365)) &
                           (purchases["Fecha"] < start)]

        product = self._product_audit(p_year, s_year, s90, p_prev, data)
        suppliers = self._supplier_audit(p_year, p_prev, data)
        categories = self._category_audit(product)
        buyers = self._buyer_audit(p_year)
        monthly = self._monthly_audit(p_year, s_year)
        variance = self._price_variance(p_year, p_prev, data)
        metrics = self._metrics(product, suppliers, buyers, variance, s_year, p_year, s90)
        findings = self._findings(product, metrics, suppliers, buyers, variance, categories)
        quality = data.quality.copy()

        self.logger.info("Auditoría %s: compras $%s | ventas $%s | margen %.1f%% | quiebres %s",
                         year, f"{metrics['compra_usd']:,.0f}", f"{metrics['venta_usd']:,.0f}",
                         metrics["margen_pct"], metrics["quiebres"])
        return AuditResult(year=year, as_of=as_of, products=product, suppliers=suppliers,
                           categories=categories, buyers=buyers, monthly=monthly,
                           price_variance=variance, metrics=metrics, findings=findings,
                           quality=quality, warehouse_info=getattr(data, "warehouse_info", {}) or {})

    # ------------------------------------------------------------------ producto
    def _product_audit(self, p_year: pd.DataFrame, s_year: pd.DataFrame, s90: pd.DataFrame,
                       p_prev: pd.DataFrame, data: ProfitData) -> pd.DataFrame:
        def agg_purchases(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
            if df.empty:
                return pd.DataFrame(columns=[f"{prefix}_USD", f"{prefix}_Unidades"])
            return df.groupby("EAN").agg(**{
                f"{prefix}_USD": ("Importe_USD", "sum"),
                f"{prefix}_Unidades": ("Cantidad", "sum"),
                f"{prefix}_Lineas": ("Renglon", "size"),
                f"{prefix}_Docs": ("DocNum", "nunique"),
                f"{prefix}_Ultima": ("Fecha", "max"),
            })

        pg = agg_purchases(p_year, "Compra")
        sg = s_year.groupby("EAN").agg(
            Venta_USD=("Importe_USD", "sum"), Venta_Unidades=("Cantidad", "sum"),
            Venta_Docs=("DocNum", "nunique"), Venta_Ultima=("Fecha", "max"),
        )
        s90g = s90.groupby("EAN").agg(Venta90_Unidades=("Cantidad", "sum"),
                                      Venta90_USD=("Importe_USD", "sum"))

        # Costo: promedio ponderado del año, luego última compra histórica
        cost_year = p_year.groupby("EAN").apply(
            lambda x: x["Importe_USD"].sum() / x["Cantidad"].sum() if x["Cantidad"].sum() > 0 else np.nan,
            include_groups=False).rename("Costo_Unit_USD")
        cost_last = (p_year.sort_values("Fecha").groupby("EAN")["CostoUnitario_USD"].last()
                     .rename("Costo_Ultimo_USD"))
        supplier_last = (p_year.sort_values("Fecha").groupby("EAN")
                         .agg(Proveedor=("Proveedor", "last"), CodProveedor=("CodProveedor", "last"),
                              Comprador=("Comprador", "last")))
        line_last = (p_year.sort_values("Fecha").groupby("EAN")
                     .agg(Linea=("Linea", "last"), Sublinea=("Sublinea", "last"),
                          Categoria=("Categoria", "last")))

        stock = data.stock.set_index("EAN")
        products = data.products.set_index("EAN")
        stock_eans = stock[(stock["Stock_Actual"] > 0) | (stock["Stock_Transito"] > 0)].index
        idx = sorted(set(pg.index) | set(sg.index) | set(stock_eans))
        out = pd.DataFrame(index=pd.Index(idx, name="EAN"))
        out = out.join(pg).join(sg).join(s90g).join(stock[
            ["Stock_Actual", "Stock_Transito", "Stock_Comprometido", "Stock_Disponible"]])
        out = out.join(products[["Descripcion", "Linea", "Sublinea", "Categoria",
                                 "Stock_Minimo", "Stock_Maximo", "TipoArticulo"]])
        out = out.join(cost_year).join(cost_last).join(supplier_last).join(line_last, rsuffix="_prod")

        for col in ("Compra_USD", "Compra_Unidades", "Compra_Lineas", "Compra_Docs",
                    "Venta_USD", "Venta_Unidades", "Venta_Docs", "Venta90_Unidades", "Venta90_USD",
                    "Stock_Actual", "Stock_Transito", "Stock_Comprometido", "Stock_Disponible"):
            if col not in out.columns:
                out[col] = 0.0
            out[col] = out[col].fillna(0.0)

        # Completar descripción/categoría desde el maestro
        for col in ("Descripcion", "Linea", "Sublinea", "Categoria"):
            if col in out.columns:
                fill = out[col].fillna("").astype(str)
                out[col] = fill.replace({"": "SIN CLASIFICAR"})

        out["Costo_Unit_USD"] = out["Costo_Unit_USD"].fillna(out["Costo_Ultimo_USD"]).fillna(0.0)
        out["Demanda_Diaria_90"] = out["Venta90_Unidades"] / 90.0
        out["Precio_Unit_USD"] = np.where(out["Venta_Unidades"] > 0,
                                          out["Venta_USD"] / out["Venta_Unidades"], np.nan)
        out["Margen_USD"] = out["Venta_USD"] - out["Venta_Unidades"] * out["Costo_Unit_USD"]
        out["Margen_Pct"] = np.where(out["Venta_USD"] > 0, out["Margen_USD"] / out["Venta_USD"] * 100, np.nan)
        out["Ratio_Compra_Venta"] = np.where(out["Venta_Unidades"] > 0,
                                             out["Compra_Unidades"] / out["Venta_Unidades"], np.nan)
        out["Stock_Valor"] = out["Stock_Actual"] * out["Costo_Unit_USD"]
        out["Transito_Valor"] = out["Stock_Transito"] * out["Costo_Unit_USD"]
        out["Cobertura_Actual_Dias"] = np.where(out["Demanda_Diaria_90"] > 0,
                                                out["Stock_Actual"] / out["Demanda_Diaria_90"],
                                                np.where(out["Stock_Actual"] > 0, 999, 0))
        out["Cobertura_Disponible_Dias"] = np.where(
            out["Demanda_Diaria_90"] > 0,
            out["Stock_Disponible"] / out["Demanda_Diaria_90"],
            np.where(out["Stock_Disponible"] > 0, 999, 0),
        )
        out["Exceso_Unidades"] = np.maximum(
            0.0, out["Stock_Disponible"] - out["Demanda_Diaria_90"] * self.target_days)
        out["Exceso_USD"] = out["Exceso_Unidades"] * out["Costo_Unit_USD"]
        out["Faltante_Unidades"] = np.maximum(
            0.0, out["Demanda_Diaria_90"] * self.lead_days - out["Stock_Disponible"])
        out["Faltante_USD"] = out["Faltante_Unidades"] * out["Precio_Unit_USD"].fillna(0.0)
        out["Cubierto_Transito"] = (out["Stock_Actual"] <= 0) & (out["Stock_Transito"] > 0)
        out["Sin_Compra_2026"] = (out["Compra_Unidades"] == 0) & (out["Venta_Unidades"] > 0)

        out["Estado"] = self._status(out)
        out["Accion"] = out["Estado"].map({
            "QUIEBRE": "Comprar urgente / verificar tránsito",
            "CRÍTICO": "Reponer esta semana",
            "FALTANTE": "Programar compra",
            "SOBRECOMPRA": "Suspender compra y liquidar/trasladar",
            "SIN VENTA": "Revisar vigencia y capital inmovilizado",
            "SIN COMPRA 2026": "Verificar si hay stock suficiente o falta compra",
            "OK": "Mantener",
        }).fillna("Revisar")

        out = out.reset_index()
        out["Proveedor"] = out["Proveedor"].fillna("SIN ASIGNAR")
        return out.sort_values(["Compra_USD", "Venta_USD"], ascending=False).reset_index(drop=True)

    def _status(self, df: pd.DataFrame) -> pd.Series:
        status = np.full(len(df), "OK", dtype=object)
        # De abajo hacia arriba para que las alertas más graves sobrescriban
        status = np.where((df["Compra_Unidades"] == 0) & (df["Venta_Unidades"] > 0), "SIN COMPRA 2026", status)
        status = np.where((df["Venta_Unidades"] == 0) &
                          ((df["Stock_Actual"] + df["Stock_Transito"]) > 0), "SIN VENTA", status)
        status = np.where((df["Demanda_Diaria_90"] > 0) &
                          (df["Cobertura_Disponible_Dias"] > self.overbuy_days), "SOBRECOMPRA", status)
        status = np.where((df["Demanda_Diaria_90"] > 0) &
                          (df["Cobertura_Disponible_Dias"] < self.low_days), "FALTANTE", status)
        status = np.where((df["Demanda_Diaria_90"] > 0) &
                          (df["Cobertura_Disponible_Dias"] < self.critical_days), "CRÍTICO", status)
        status = np.where((df["Venta_Unidades"] > 0) & (df["Stock_Disponible"] <= 0), "QUIEBRE", status)
        return pd.Series(status, index=df.index)

    # ------------------------------------------------------------------ dimensiones
    def _supplier_audit(self, p_year: pd.DataFrame, p_prev: pd.DataFrame,
                        data: ProfitData) -> pd.DataFrame:
        if p_year.empty:
            return pd.DataFrame()
        grouped = p_year.groupby(["CodProveedor", "Proveedor"], dropna=False).agg(
            Compra_USD=("Importe_USD", "sum"), Unidades=("Cantidad", "sum"),
            Docs=("DocNum", "nunique"), Productos=("EAN", "nunique"),
            Ticket_Prom_USD=("Importe_USD", lambda s: s.sum() / max(len(s), 1)),
            Ultima_Compra=("Fecha", "max"),
        ).reset_index()
        total = grouped["Compra_USD"].sum()
        grouped["Share_Pct"] = grouped["Compra_USD"] / total * 100 if total else 0
        grouped["Ticket_Prom_USD"] = grouped["Compra_USD"] / grouped["Docs"].replace(0, np.nan)
        grouped = grouped.drop(columns=["Ticket_Prom_USD"], errors="ignore")
        grouped["Ticket_Prom_USD"] = grouped["Compra_USD"] / grouped["Docs"].replace(0, np.nan)
        grouped = grouped.sort_values("Compra_USD", ascending=False).reset_index(drop=True)

        # Variación de costo por proveedor (productos comunes con volumen)
        var_rows = []
        for (cod, name), part in p_year.groupby(["CodProveedor", "Proveedor"], dropna=False):
            prev = p_prev[p_prev["CodProveedor"] == cod]
            if prev.empty or part.empty:
                continue
            c26 = part["Importe_USD"].sum() / part["Cantidad"].sum() if part["Cantidad"].sum() > 0 else np.nan
            c25 = prev["Importe_USD"].sum() / prev["Cantidad"].sum() if prev["Cantidad"].sum() > 0 else np.nan
            if pd.notna(c25) and pd.notna(c26) and c25 > 0:
                var_rows.append({"CodProveedor": cod, "Costo_Var_Pct": (c26 / c25 - 1) * 100})
        if var_rows:
            grouped = grouped.merge(pd.DataFrame(var_rows), on="CodProveedor", how="left")
        else:
            grouped["Costo_Var_Pct"] = np.nan

        if not data.returns.empty:
            ret = data.returns.groupby("CodProveedor").agg(Devoluciones_USD=("Importe", "sum")).reset_index()
            grouped = grouped.merge(ret, on="CodProveedor", how="left")
        else:
            grouped["Devoluciones_USD"] = 0.0
        grouped["Devoluciones_USD"] = grouped["Devoluciones_USD"].fillna(0.0)
        return grouped

    def _category_audit(self, product: pd.DataFrame) -> pd.DataFrame:
        if product.empty:
            return pd.DataFrame()
        grouped = product.groupby("Categoria", dropna=False).agg(
            Compra_USD=("Compra_USD", "sum"), Venta_USD=("Venta_USD", "sum"),
            Compra_Unidades=("Compra_Unidades", "sum"), Venta_Unidades=("Venta_Unidades", "sum"),
            Margen_USD=("Margen_USD", "sum"), Stock_Valor=("Stock_Valor", "sum"),
            Transito_Valor=("Transito_Valor", "sum"), Productos=("EAN", "nunique"),
        ).reset_index()
        grouped["Margen_Pct"] = np.where(grouped["Venta_USD"] > 0,
                                         grouped["Margen_USD"] / grouped["Venta_USD"] * 100, np.nan)
        grouped["Ratio_Compra_Venta"] = np.where(grouped["Venta_USD"] > 0,
                                                 grouped["Compra_USD"] / grouped["Venta_USD"], np.nan)
        return grouped.sort_values("Compra_USD", ascending=False).reset_index(drop=True)

    def _buyer_audit(self, p_year: pd.DataFrame) -> pd.DataFrame:
        if p_year.empty:
            return pd.DataFrame()
        grouped = p_year.groupby("Comprador", dropna=False).agg(
            Compra_USD=("Importe_USD", "sum"), Unidades=("Cantidad", "sum"),
            Docs=("DocNum", "nunique"), Proveedores=("CodProveedor", "nunique"),
            Productos=("EAN", "nunique"),
        ).reset_index()
        grouped["Ticket_Prom_USD"] = grouped["Compra_USD"] / grouped["Docs"].replace(0, np.nan)
        grouped["Share_Pct"] = grouped["Compra_USD"] / grouped["Compra_USD"].sum() * 100
        return grouped.sort_values("Compra_USD", ascending=False).reset_index(drop=True)

    def _monthly_audit(self, p_year: pd.DataFrame, s_year: pd.DataFrame) -> pd.DataFrame:
        pm = p_year.groupby(p_year["Fecha"].dt.strftime("%Y-%m")).agg(
            Compra_USD=("Importe_USD", "sum"), Compra_Unidades=("Cantidad", "sum"),
            Docs_Compra=("DocNum", "nunique"))
        sm = s_year.groupby(s_year["Fecha"].dt.strftime("%Y-%m")).agg(
            Venta_USD=("Importe_USD", "sum"), Venta_Unidades=("Cantidad", "sum"),
            Docs_Venta=("DocNum", "nunique"))
        out = pm.join(sm, how="outer").fillna(0)
        out.index.name = "Mes"
        out = out.reset_index()
        out["Ratio_Compra_Venta"] = np.where(out["Venta_USD"] > 0, out["Compra_USD"] / out["Venta_USD"], np.nan)
        return out.sort_values("Mes").reset_index(drop=True)

    def _price_variance(self, p_year: pd.DataFrame, p_prev: pd.DataFrame,
                        data: ProfitData) -> pd.DataFrame:
        if p_year.empty or p_prev.empty:
            return pd.DataFrame()
        def wavg(df):
            return df["Importe_USD"].sum() / df["Cantidad"].sum() if df["Cantidad"].sum() > 0 else np.nan
        c26 = p_year.groupby("EAN").apply(wavg, include_groups=False).rename("Costo_2026")
        c25 = p_prev.groupby("EAN").apply(wavg, include_groups=False).rename("Costo_2025")
        out = pd.concat([c25, c26], axis=1).dropna()
        if out.empty:
            return pd.DataFrame()
        out["Var_Pct"] = (out["Costo_2026"] / out["Costo_2025"] - 1) * 100
        out = out.reset_index()
        prod = data.products.set_index("EAN")
        out = out.join(prod[["Descripcion", "Categoria"]], on="EAN", how="left")
        purchases = p_year.groupby("EAN").agg(Compra_2026_USD=("Importe_USD", "sum"),
                                              Proveedor=("Proveedor", "last")).reset_index()
        out = out.merge(purchases, on="EAN", how="left")
        return out.sort_values("Var_Pct", ascending=False).reset_index(drop=True)

    # ------------------------------------------------------------------ métricas
    def _metrics(self, product: pd.DataFrame, suppliers: pd.DataFrame, buyers: pd.DataFrame,
                 variance: pd.DataFrame, s_year: pd.DataFrame, p_year: pd.DataFrame,
                 s90: pd.DataFrame) -> dict[str, Any]:
        compra = float(product["Compra_USD"].sum())
        venta = float(product["Venta_USD"].sum())
        cogs = float((product["Venta_Unidades"] * product["Costo_Unit_USD"]).sum())
        margen = venta - cogs
        stock_val = float(product["Stock_Valor"].sum())
        transito_val = float(product["Transito_Valor"].sum())
        daily = float(product["Venta90_Unidades"].sum() / 90) if not product.empty else 0
        disponible = float((product["Stock_Actual"] + product["Stock_Transito"] - product["Stock_Comprometido"]).sum())
        cobertura = disponible / daily if daily > 0 else np.nan
        status_counts = product["Estado"].value_counts().to_dict() if not product.empty else {}
        top5 = suppliers["Share_Pct"].head(5).sum() if not suppliers.empty else 0
        hhi = float(((suppliers["Share_Pct"] / 100) ** 2).sum()) if not suppliers.empty else 0
        top_buyer = buyers.iloc[0]["Share_Pct"] if not buyers.empty else 0
        sin_compra_mask = (product["Compra_Unidades"] == 0) & (product["Venta_Unidades"] > 0)
        activos = product[(product["Compra_Unidades"] > 0) | (product["Venta_Unidades"] > 0)]
        return {
            "year": int(s_year["Fecha"].dt.year.iloc[0]) if not s_year.empty else None,
            "compra_usd": compra,
            "venta_usd": venta,
            "cogs_usd": cogs,
            "margen_usd": margen,
            "margen_pct": margen / venta * 100 if venta else 0,
            "compra_unidades": float(product["Compra_Unidades"].sum()),
            "venta_unidades": float(product["Venta_Unidades"].sum()),
            "ratio_unidades": float(product["Compra_Unidades"].sum() / product["Venta_Unidades"].sum()) if product["Venta_Unidades"].sum() else np.nan,
            "ratio_usd": compra / venta if venta else np.nan,
            "stock_usd": stock_val,
            "transito_usd": transito_val,
            "inventario_total_usd": stock_val + transito_val,
            "cobertura_dias": cobertura,
            "productos": int(len(product)),
            "proveedores": int(len(suppliers)),
            "compradores": int(len(buyers)),
            "top5_share": float(top5),
            "hhi": hhi,
            "top_buyer_share": float(top_buyer),
            "quiebres": int(status_counts.get("QUIEBRE", 0)),
            "criticos": int(status_counts.get("CRÍTICO", 0)),
            "faltantes": int(status_counts.get("FALTANTE", 0)),
            "sobrecompra": int(status_counts.get("SOBRECOMPRA", 0)),
            "sin_venta": int(status_counts.get("SIN VENTA", 0)),
            "sin_compra": int(status_counts.get("SIN COMPRA 2026", 0)),
            "ok": int(status_counts.get("OK", 0)),
            "capital_exceso_usd": float(product["Exceso_USD"].sum()),
            "venta_en_riesgo_usd": float(product["Faltante_USD"].sum()),
            "venta_quiebre_usd": float(product.loc[product["Estado"] == "QUIEBRE", "Venta_USD"].sum()),
            "venta_critico_usd": float(product.loc[product["Estado"] == "CRÍTICO", "Venta_USD"].sum()),
            "venta_faltante_usd": float(product.loc[product["Estado"] == "FALTANTE", "Venta_USD"].sum()),
            "sin_venta_stock_usd": float(product.loc[product["Estado"] == "SIN VENTA", "Stock_Valor"].sum()),
            "venta_sin_compra_usd": float(product.loc[product["Estado"] == "SIN COMPRA 2026", "Venta_USD"].sum()),
            "sin_compra_total": int(sin_compra_mask.sum()),
            "venta_sin_compra_total_usd": float(product.loc[sin_compra_mask, "Venta_USD"].sum()),
            "var_precio_mediana": float(variance["Var_Pct"].median()) if not variance.empty else np.nan,
            "var_precio_up15": int((variance["Var_Pct"] > 15).sum()) if not variance.empty else 0,
            "productos_sin_proveedor": int((product["Proveedor"].fillna("SIN ASIGNAR") == "SIN ASIGNAR").sum()),
            "activos_sin_proveedor": int((activos["Proveedor"].fillna("SIN ASIGNAR") == "SIN ASIGNAR").sum()),
            "quiebre_cubierto_transito": int(product["Cubierto_Transito"].sum()),
            "transito_sin_venta_usd": float(product.loc[product["Venta90_Unidades"] == 0, "Transito_Valor"].sum()),
            "docs_compra": int(p_year["DocNum"].nunique()),
            "docs_venta": int(s_year["DocNum"].nunique()),
            "venta90_usd": float(s90["Importe_USD"].sum()),
        }

    def _findings(self, product: pd.DataFrame, m: dict[str, Any], suppliers: pd.DataFrame,
                  buyers: pd.DataFrame, variance: pd.DataFrame, categories: pd.DataFrame) -> list[str]:
        findings: list[str] = []
        findings.append(
            f"Compra de mercancía ${m['compra_usd']:,.0f} vs venta ${m['venta_usd']:,.0f}. "
            f"Costo de venta ${m['cogs_usd']:,.0f} y margen bruto {m['margen_pct']:.1f}%. "
            f"En unidades se compró {m['ratio_unidades'] * 100:.1f}% de lo vendido: el monto global está equilibrado."
        )
        findings.append(
            f"El capital total en inventario y tránsito es ${m['inventario_total_usd']:,.0f} "
            f"(stock ${m['stock_usd']:,.0f} + tránsito ${m['transito_usd']:,.0f}), equivalente a "
            f"{m['cobertura_dias']:.0f} días de venta. El objetivo sano es 60–90 días."
        )
        findings.append(
            f"Hay {m['quiebres']} quiebres reales (sin stock ni tránsito) sobre SKU que vendieron "
            f"${m['venta_quiebre_usd']:,.0f} en el año; la venta inmediata en riesgo por faltante es "
            f"${m['venta_en_riesgo_usd']:,.0f}. Además hay {m['criticos']} SKU críticos y {m['faltantes']} en faltante."
        )
        findings.append(
            f"{m['sobrecompra']} SKU superan 180 días de cobertura; el exceso de inventario asciende a "
            f"${m['capital_exceso_usd']:,.0f}. {m['sin_venta']} SKU sin venta concentran "
            f"${m['sin_venta_stock_usd']:,.0f} inmovilizados y ${m['transito_sin_venta_usd']:,.0f} en tránsito."
        )
        findings.append(
            f"{m['sin_compra_total']} SKU vendieron en 2026 sin compra registrada "
            f"(${m['venta_sin_compra_total_usd']:,.0f}); pueden estar cubiertos por inventario inicial "
            f"o ser un faltante de reposición. Adicionalmente, {m['quiebre_cubierto_transito']} SKU sin stock físico "
            "tienen unidades en tránsito."
        )
        if not suppliers.empty:
            top = suppliers.iloc[0]
            findings.append(
                f"Concentración: {m['proveedores']} proveedores, top 5 = {m['top5_share']:.1f}% de la compra "
                f"(HHI {m['hhi']:.3f}). Principal proveedor: {top['Proveedor']} ({top['Share_Pct']:.1f}%)."
            )
        if not buyers.empty:
            findings.append(
                f"Compras registradas por {m['compradores']} usuario(s); el principal concentra "
                f"{m['top_buyer_share']:.1f}% del monto. Conviene separar comprador y aprobador."
            )
        if not variance.empty:
            findings.append(
                f"En {len(variance)} productos comparables, el costo unitario en USD varió "
                f"{m['var_precio_mediana']:+.1f}% (mediana); {m['var_precio_up15']} subieron más de 15%."
            )
        if not categories.empty:
            over = categories[categories["Ratio_Compra_Venta"] > 1.5].head(3)
            if not over.empty:
                names = ", ".join(f"{r.Categoria} ({r.Ratio_Compra_Venta:.1f}x)" for r in over.itertuples())
                findings.append(f"Categorías con compra muy por encima de la venta: {names}.")
        findings.append(
            f"{m['activos_sin_proveedor']} SKU activos del universo auditado no tienen proveedor asignado; "
            "es la brecha de datos que más limita automatizar la compra."
        )
        return findings
