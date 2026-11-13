# Metodología del sistema

Este documento explica **cómo** se calculan la segmentación, el pronóstico, el stock de seguridad y las órdenes. Está pensado para el equipo de compras y para auditoría técnica.

---

## 1. Datos de entrada y calidad

Con el archivo de ejemplo (corte 19/12/2025):

| Métrica | Valor |
|---------|-------|
| Transacciones | 88.605 |
| Productos distintos | 2.278 |
| Rango de fechas | 30/01/2023 – 19/12/2025 |
| Productos con venta en los últimos 12 meses | 1.217 |
| Productos en catálogo maestro | 53 |
| Productos con inventario informado | 55 |
| Cobertura del catálogo sobre ventas USD | 16,2% |
| Periodos de 4 semanas sin venta | 51,5% |
| Venta 12 meses | $772.929 |

### Tratamiento de datos faltantes

1. **Producto vendido sin catálogo:** se agrega al maestro con `Origen = ESTIMADO`.
   - Costo = precio promedio ponderado de venta × (1 − margen configurado). Margen por defecto: 15%.
   - Proveedor = `SIN ASIGNAR`.
   - Múltiplo y lead time = valores por defecto.
2. **Producto sin stock:** no se asume stock cero. Se aplica la política `unknown_stock_policy`:
   - `minimum` (por defecto): sugiere la demanda del lead time + revisión, sin stock de seguridad.
   - `zero`: asume stock 0 y pide el objetivo completo.
   - `skip`: no genera orden; solo alerta.
3. **EAN, fechas y números:** normalización tolerante a `.0`, notación científica, acentos, comas decimales y símbolos de moneda.

---

## 2. Análisis ABC

Ventana: últimos **365 días** desde la fecha de corte (`analysis.window_days`).

- **ABC por valor:** se ordenan los productos por venta USD descendente y se acumula su participación.
  - A: hasta 80% acumulado.
  - B: 80% – 95%.
  - C: 95% – 100%.
  - El primer producto siempre es A, aunque concentre más del 80%.
- **ABC por volumen:** mismo procedimiento sobre unidades, con cortes 70% / 90%.

Resultado actual:

| Clase | Productos | % Productos | % Ventas USD |
|-------|-----------|-------------|--------------|
| A | 343 | 28,2% | 79,9% |
| B | 369 | 30,3% | 15,0% |
| C | 505 | 41,5% | 5,0% |

---

## 3. Análisis XYZ

La demanda de una droguería mayorista es intermitente: la mayoría de los SKU no vende todas las semanas. Por eso la variabilidad se calcula sobre **13 periodos de 4 semanas** (364 días), no sobre semanas sueltas.

Para cada producto:

```text
CV = desviación_estándar(demanda_28d) / media(demanda_28d)
```

Clasificación (`analysis.xyz_cv_thresholds`):

| Clase | CV | Interpretación |
|-------|----|----------------|
| X | ≤ 1,0 | relativamente estable |
| Y | 1,0 – 2,0 | variable |
| Z | > 2,0 | errática / intermitente |

**¿Por qué umbrales 1,0 / 2,0 y no 0,3 / 0,6?**
Con umbrales de libro (0,3/0,6), el 99% del portafolio cae en Z y la segmentación deja de ser útil. Con 1,0/2,0 la distribución es 21% X, 46% Y, 33% Z, lo que permite diferenciar estrategias. Los umbrales son configurables y deben recalibrarse si cambia la mezcla de portafolio.

También se calcula el **patrón de demanda** con ADI (intervalo medio entre demandas) y CV² de la demanda no nula:

- `SUAVE`, `ERRATICO`, `INTERMITENTE`, `IRREGULAR` o `SIN DEMANDA`.

Resultado actual:

| Clase | Productos | % | CV promedio | Periodos sin venta |
|-------|-----------|---|-------------|--------------------|
| X | 254 | 21,0% | 0,76 | 10,6% |
| Y | 553 | 45,6% | 1,51 | 48,6% |
| Z | 405 | 33,4% | 2,81 | 81,2% |

---

## 4. Segmentación híbrida

Se combinan ABC y XYZ en 9 segmentos. La estrategia es diferenciada:

| Segmento | Estrategia | Modelo sugerido |
|----------|------------|-----------------|
| AX | Revisión semanal, stock de seguridad calculado y seguimiento de quiebres | Suavizado estacional / SARIMA |
| AY | Pronóstico semanal con ensemble; validar tendencia | Ensemble |
| AZ | Demanda errática: compra por evento, revisión manual | Croston / reglas |
| BX | Revisión quincenal, modelos intermedios | Suavizado exponencial |
| BY | Revisión quincenal, media móvil con tendencia | Media móvil + tendencia |
| BZ | Reglas simples y control de exceso | Croston / reglas |
| CX | Reposición por mínimos, revisión mensual | Media móvil |
| CY | Stock mínimo y compras consolidadas | Media móvil |
| CZ | Reducir inventario; comprar por necesidad | Reglas / naive |

Distribución actual: AX 165, AY 148, AZ 30, BX 75, BY 214, BZ 80, CX 14, CY 191, CZ 300.

---

## 5. Pronóstico

### 5.1 Serie de trabajo

- Agregación **semanal** (semana terminada en domingo).
- Se eliminan los ceros anteriores a la primera venta (producto que aún no existía).
- Se conservan los ceros posteriores: pueden ser falta de stock o ausencia real de demanda.
- La última semana incompleta se excluye del entrenamiento.

### 5.2 Modelos evaluados

| Modelo | Uso principal |
|--------|---------------|
| Naive (última semana) | Referencia |
| Media móvil 4 / 8 semanas | Nivel reciente |
| Suavizado exponencial simple (α optimizado) | Series con nivel cambiante |
| Holt amortiguado | Tendencia |
| Naive estacional (52 semanas) | Estacionalidad anual |
| Croston-SBA | Demanda intermitente |
| TSB (probabilidad × tamaño) | Intermitencia con cambios de frecuencia |
| Ensemble | Mediana de los modelos base |
| Ensemble dinámico | Ponderación por error reciente (candidato) |

Todos están implementados en NumPy: sin Prophet, TensorFlow ni statsmodels. Esto reduce dependencias, acelera la ejecución (~18 s para 2.278 productos) y facilita empaquetar un `.exe`.

### 5.3 Backtesting

- **6 orígenes mensuales móviles** (últimos 6 meses).
- En cada origen se entrena con la historia disponible y se pronostican **4 semanas**.
- Se compara el total pronosticado contra el total real del periodo.
- Métrica de selección: **WAPE**

```text
WAPE = Σ |pronóstico − real| / Σ real × 100
```

- Se incluyen los periodos con venta cero para no premiar pronósticos en cero.
- MAPE y “hit ±25%” se calculan solo sobre periodos con venta > 0.

### 5.4 Política por clase XYZ

El modelo **no se elige producto a producto**: con pocos orígenes y demanda errática, esa selección sobreajusta. Se elige una política por clase XYZ, agregando el error de todos los productos de la clase:

| Grupo | Modelo | WAPE | WAPE CV | Sesgo | Hit ±25% |
|-------|--------|------|---------|-------|----------|
| X | Croston-SBA | 56,9% | 58,3% | −3,0% | 24,6% |
| Y | TSB | 90,1% | 90,1% | −6,2% | 14,9% |
| Z | TSB | 125,7% | 124,6% | −36,2% | 8,7% |

“WAPE CV” re-selecciona la política dejando un mes fuera en cada fold (validación cruzada). La diferencia entre WAPE y WAPE CV es pequeña, lo que indica que la política es estable.

### 5.5 Resultado del portafolio A/B

| Indicador | Valor |
|-----------|-------|
| WAPE política | 68,5% |
| WAPE validación cruzada | 69,6% |
| WAPE naive (referencia) | 98,4% |
| Mejora vs naive | 30,4% |
| Sesgo | −5,9% |
| Hit ±25% | 19,3% |
| MAPE mediana | 98,6% |
| Productos A/B evaluados | 493 |
| Productos dentro de ±25% (WAPE) | 1,0% |

### 5.6 ¿Por qué la precisión es limitada?

Tres hechos medidos:

1. **Intermitencia:** 51,5% de los periodos de 4 semanas no tiene venta. La mediana de demanda A/B es ~18 unidades/mes.
2. **Oráculo:** si se eligiera el mejor modelo para cada producto y cada mes **con hindsight**, el WAPE A/B sería **31,2%**. Ningún modelo puede superar ese límite con la información disponible.
3. **Censura:** los ceros pueden ser quiebres de stock, no ausencia de demanda. Sin historial de inventario no se puede distinguir.

Por eso el sistema prioriza:
- controlar el **sesgo** (no subestimar sistemáticamente),
- dimensionar el **stock de seguridad** con el error observado,
- y reportar la precisión de forma honesta, en lugar de prometer una meta inalcanzable.

Ver [`ACEPTACION.md`](ACEPTACION.md) para la discusión completa.

---

## 6. Cálculo de la orden

Para cada producto activo:

```text
cobertura_dias = lead_time + dias_revision
demanda_cobertura = Σ pronóstico semanal hasta cobertura_dias
sigma_cobertura = sigma_28 × √(cobertura_dias / 28)
stock_seguridad = z × sigma_cobertura
stock_seguridad = min(stock_seguridad, demanda_diaria × max_safety_days)
objetivo = demanda_cobertura + stock_seguridad
```

Ajustes:

1. `objetivo = max(objetivo, Stock_Minimo)` si está configurado.
2. `objetivo = min(objetivo, Stock_Maximo)` si está configurado, **pero** luego se fuerza `objetivo ≥ demanda_cobertura`. El máximo es un objetivo blando: nunca debe impedir cubrir el lead time.
3. `cantidad = objetivo − stock_disponible`.
4. Si `cantidad ≤ 0`: no se genera orden (`STOCK_SUFICIENTE`), salvo que la cobertura sea menor al lead time (se fuerza la reposición).
5. `cantidad = max(cantidad, pedido_minimo)`.
6. `cantidad = ceil(cantidad / múltiplo) × múltiplo`.
7. Tope por cobertura máxima (`max_coverage_days`, 120 días).
8. Descuento por volumen según proveedor.

### Ejemplo real (CLONZEP 2MG X30 TAB)

```text
Lead time = 30 d; revisión = 7 d → cobertura = 37 d
Demanda 37 d = 378 u; stock = 76 u; sigma = 65 u
Stock de seguridad = 1,645 × 65 × √(37/28) = 221 u
Objetivo = 378 + 221 = 599 u → limitado por Stock_Maximo = 360 u
Se fuerza objetivo ≥ demanda = 378 u
Cantidad = 378 − 76 = 302 u → múltiplo 36 → 288 u
Resultado: 288 u, prioridad CRÍTICA (cobertura 7,4 d < lead time)
```

El sistema advierte en la justificación que el máximo configurado limita el pedido.

### Nivel de servicio

`inventory.service_level` (0,95 por defecto) define `z`:

| Nivel | z |
|-------|---|
| 90% | 1,282 |
| 95% | 1,645 |
| 97,5% | 1,960 |
| 99% | 2,326 |

---

## 7. Prioridades

| Prioridad | Regla (stock informado) |
|-----------|--------------------------|
| CRÍTICA | cobertura ≤ max(7 d, lead time) |
| ALTA | cobertura ≤ lead time + 15 d |
| MEDIA | cobertura ≤ lead time + 30 d |
| BAJA | cobertura mayor |

Sin stock informado: prioridad según ABC, riesgo de quiebre y valor. Se marca `Stock_Informado = No`.

---

## 8. Alertas

| Tipo | Disparador | Acción |
|------|------------|--------|
| STOCK CRÍTICO | cobertura < 7 d | Comprar urgente |
| QUIEBRE ANTES DE ENTREGA | cobertura < lead time | Adelantar pedido |
| STOCK BAJO | cobertura < 15 d | Programar compra |
| EXCESO DE INVENTARIO | cobertura > 540 d | Suspender compras / liquidar |
| POSIBLE QUIEBRE | brecha de venta > 2× ADI en A/B | Verificar disponibilidad |
| CAMBIO DE TENDENCIA | ±30% vs 30 d previos (mín. 20 u) | Investigar |
| SIN VENTAS RECIENTES | 60–120 d sin venta | Verificar vigencia |
| POSIBLE DESCONTINUACIÓN | > 120 d sin venta | Retirar del catálogo activo |
| PRODUCTO NUEVO | < 90 d de historia | Compra conservadora |
| ALTA INCERTIDUMBRE | WAPE > 60% | Revisión manual |
| COMPRA GRANDE | > $10.000 por proveedor | Negociar descuento |
| INVENTARIO NO INFORMADO | productos activos sin stock | Cargar inventario |
| CATÁLOGO INCOMPLETO | productos sin proveedor/costo | Completar maestro |

---

## 9. Validación y supuestos

- El backtesting usa datos pasados; no garantiza el desempeño futuro.
- No se modela variabilidad del lead time ni quiebres del proveedor.
- El costo de productos sin catálogo es estimado; el impacto en la inversión total debe interpretarse con cautela.
- La cobertura real solo puede calcularse para los 55 productos con stock.
- El sistema no incluye fechas de caducidad.

---

## 10. Reproducibilidad

```bash
python main.py --no-open --no-dashboard
python -m unittest discover -s tests -p "test_*.py" -v
```

La ejecución es determinista salvo por el redondeo de pronósticos; no hay entrenamiento aleatorio.
