# Evaluación de criterios de aceptación

Este documento contrasta los criterios del proyecto con los resultados medidos sobre el archivo real (corte 19/12/2025) y explica por qué algunas metas de precisión no son alcanzables con este portafolio.

---

## Resumen

| # | Criterio | Meta | Resultado | Veredicto |
|---|----------|------|-----------|-----------|
| 1 | Funcional | Órdenes semanales para > 200 productos | **519 líneas** | ✅ Cumple |
| 2 | Preciso | MAPE < 20% en A/B | **98,6%** (mediana) | ❌ No alcanzable |
| 3 | Preciso | Predicciones ±25% en 80% de A/B | **1,0%** de productos (hit por mes: 19,3%) | ❌ No alcanzable |
| 4 | Usable | Excel familiar con < 5 clics | 1 comando / doble clic; 9 hojas con filtros | ✅ Cumple |
| 5 | Accionable | Cantidades discretas y prioridades claras | Múltiplos por proveedor, 4 prioridades + REVISAR | ✅ Cumple |
| 6 | Adaptable | Parámetros editables sin reprogramar | YAML + CSV (proveedores y productos) | ✅ Cumple |
| 7 | Cobertura | > 80% del volumen de compras | **76,1%** del USD A/B (68,6% de productos) | ⚠️ Parcial |
| 8 | ROI | −25% stock-outs en 90 días | No medible sin historial de inventario | ⏳ Pendiente de medición |

---

## 1. Funcionalidad: 519 órdenes

El sistema genera una línea por producto que requiere reposición, con:

- cantidad ajustada al múltiplo del proveedor,
- stock de seguridad calculado,
- prioridad (CRÍTICA / ALTA / MEDIA / BAJA),
- justificación legible,
- costo unitario y total,
- modelo usado y confianza.

La meta de > 200 productos se supera ampliamente. La distribución actual es:

| Prioridad | Órdenes | Unidades | Inversión |
|-----------|---------|----------|-----------|
| CRÍTICA | 6 | 1.290 | $7.388 |
| ALTA | 229 | 14.178 | $48.983 |
| MEDIA | 159 | 6.036 | $14.271 |
| BAJA | 125 | 5.196 | $8.531 |

---

## 2. Precisión: por qué las metas no son alcanzables

### 2.1 Los datos

| Hecho | Valor |
|-------|-------|
| Periodos de 4 semanas sin venta | 51,5% |
| Mediana de demanda A/B | ~18 unidades/mes |
| Productos A/B con menos de 10 u/mes | 254 (51,5% de A/B) |
| Productos con catálogo real | 53 de 2.278 |
| Productos con stock informado | 55 de 2.278 |

Con demandas de 1 a 20 unidades al mes, un error absoluto de 5 unidades ya representa 25%–500%. La meta de ±25% exige predecir con precisión de unidades, algo que la serie no permite.

### 2.2 El oráculo: el techo de la precisión

Para saber si el problema es el modelo o los datos, se calculó un **oráculo**: para cada producto y cada mes de backtesting se eligió el modelo que **menos error absoluto tuvo en ese mes específico** (información del futuro que ningún modelo real tiene).

Resultado:

- WAPE A/B del oráculo: **31,2%**.
- Porcentaje de productos A/B que quedarían dentro de ±25% incluso con el oráculo: **36,9%**.

Es decir, **ni con hindsight se llega al 80% de productos dentro de ±25%**. La meta no es alcanzable con este portafolio, independientemente del algoritmo.

### 2.3 Resultados reales vs oráculo

| Modelo / política | WAPE A/B | Hit ±25% |
|-------------------|----------|----------|
| Naive (repetir última semana) | 98,4% | — |
| Política XYZ del sistema | **68,5%** | 19,3% |
| Validación cruzada de la política | 69,6% | — |
| Oráculo (hindsight) | 31,2% | 36,9% |

La política reduce el error del naive en **30,4%** y mantiene el sesgo en **−5,9%** (ligera subestimación, compensada por el stock de seguridad). Es una mejora real, pero no alcanza para las metas absolutas.

### 2.4 El error depende del volumen

| Demanda mensual | Productos | WAPE |
|-----------------|-----------|------|
| < 10 u | 354 | 128,0% |
| 10–25 u | 155 | 78,2% |
| 25–50 u | 58 | 68,0% |
| 50–100 u | 27 | 59,7% |
| 100–250 u | 12 | 71,2% |
| > 250 u | 7 | 39,3% |

Incluso en el tramo de mayor volumen, el WAPE es 39% y el hit ±25% es 28,6%. La mejora vendría de más datos (inventario, quiebres) y de una definición de KPI adecuada, no de cambiar de algoritmo.

### 2.5 MAPE no es la métrica correcta

El MAPE divide por la venta del mes. Cuando la venta es 0 o 1 unidad, el error porcentual explota. Por eso el sistema reporta:

- **WAPE**: error absoluto / venta total. Robusto ante ceros.
- **Sesgo**: suma de errores / venta total. Detecta sub/sobreestimación.
- **Hit ±25%**: solo sobre meses con venta.
- **Cobertura y nivel de servicio**: la métrica que realmente importa para compras.

---

## 3. Usabilidad y accionabilidad

- Un solo comando (`python main.py`) o doble clic (`run.bat`).
- Excel con filtros, paneles congelados, colores por prioridad y totales.
- Cada línea incluye una justificación en lenguaje natural:
  “Cobertura actual 7 d (lead time 30 d); demanda 37 d: 378 u; stock: 76 u; stock de seguridad: 221 u.”
- Dashboard HTML autocontenido para gerencia.
- Alertas separadas por severidad y responsable.

---

## 4. Cobertura de compra: 76,1% del USD A/B

- 576 productos A/B activos.
- 395 reciben orden (68,6%).
- Representan el **76,1% del valor de venta A/B**.

Los 181 restantes:

- 27 tienen stock suficiente (no requieren compra),
- 7 no tienen demanda pronosticada,
- 146 alcanzan la cobertura máxima (134 sin stock informado y 12 con stock conocido): el sistema no genera orden automática para evitar sobrecompra semanal y los reporta en la alerta **“COMPRA MÍNIMA EXCEDE COBERTURA”** para decisión manual,
- 1 queda sin pronóstico por historial insuficiente.

Esta es una decisión deliberada de control de capital. Si se prefiere priorizar disponibilidad sobre capital, se puede bajar `inventory.max_coverage_days` o cambiar la política de stock desconocido a `zero`.

---

## 5. KPI propuestos (redefinición)

Las metas originales mezclan precisión estadística con disponibilidad. Se propone medir el sistema con:

| KPI | Meta propuesta | Cómo se mide |
|-----|----------------|--------------|
| WAPE portafolio A/B | ≤ 50% al mes 3; ≤ 40% al mes 6 | Backtesting mensual |
| Sesgo A/B | \|sesgo\| ≤ 10% | Backtesting |
| Valor agregado vs naive | ≥ 20% de reducción de WAPE | Backtesting |
| Cobertura de compra A/B | ≥ 80% del USD | Órdenes / demanda A/B |
| Nivel de servicio | ≥ 95% de líneas disponibles | Historial de stock |
| Stock-outs | −25% vs línea base | Conteo semanal |
| Capital inmovilizado | ≤ 60 días de cobertura | Inventario / demanda |
| Usabilidad | < 30 min de capacitación | Prueba con compradores |

Estos KPI son alcanzables y alineados con el objetivo de negocio (disponibilidad + eficiencia de capital).

---

## 6. Plan para mejorar la precisión

| Acción | Impacto esperado |
|--------|------------------|
| Cargar inventario completo semanal | Alto: cobertura real y prioridades exactas |
| Completar `EAN → proveedor → costo` (top 200) | Alto: costos y lead times reales |
| Registrar quiebres (stock = 0) | Alto: separa demanda real de venta censurada |
| Incorporar lead time variable por proveedor | Medio: mejor stock de seguridad |
| Recalibrar umbrales XYZ con 12 meses de uso | Medio |
| Evaluar modelo por categoría/molecula | Medio: más señal por agregación |
| Medir nivel de servicio real | Alto: permite cerrar el ciclo |

---

## 7. Conclusión

El sistema **cumple** los criterios funcionales, de usabilidad, accionabilidad y adaptabilidad. **No cumple** las metas de precisión absoluta porque son estructuralmente inalcanzables con la intermitencia y la censura de los datos actuales; el propio oráculo lo demuestra.

La decisión recomendada no es ocultar la brecha, sino:

1. Reportarla con transparencia (el Excel y el dashboard lo hacen).
2. Medir el éxito con KPI de disponibilidad, sesgo y valor agregado.
3. Invertir primero en datos (inventario, catálogo, quiebres), que es donde está el mayor retorno.
