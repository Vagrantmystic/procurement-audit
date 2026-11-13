# Reglas de negocio implementadas para Órdenes de Compra

**Estado:** activas en el motor de compras.
**Archivos clave:** `order_calculator.py`, `price_guard.py`, `catalog_enricher.py`, `order_manager.py`, `alert_system.py`.
**Configuración:** `config/parameters.yaml` (`inventory`, `lifecycle`, `price`).
**Base RAG machine-readable:** `config/reglas_oc.yaml` (reglas, KPIs y flujo).

---

## 1. Base de cálculo: solo `ACT`

### Regla

```text
Inventario_OC = max(0, ACT − COM)
LLE = 0 en la fórmula
```

- `ACT`: stock físico actual.
- `COM`: comprometido/ventas pendientes; se resta si el dato es confiable.
- `LLE`: stock por llegar; **nunca se suma, resta ni promedia** para calcular la necesidad.

`Stock_Disponible` (que incluye `LLE`) se conserva solo para reportes históricos; la OC usa `Stock_Usado_OC`.

### Control de duplicidad por `LLE`

`LLE` no es inventario: es una obligación pendiente. Por eso se usa como **bandera de bloqueo**, no como disponibilidad.

| Situación | Resultado |
|---|---|
| `LLE = 0` | Cálculo normal con `ACT − COM`. |
| `LLE > 0` y política `block` (default) | `Cantidad_OC = 0`, motivo `LLE_PENDIENTE`; validar OC/ETA. |
| `LLE > 0` y `OC_Abierta` o `LLE_Confirmado` | Bloqueo; estado `CUBIERTO_POR_TRANSITO`/`LLE_PENDIENTE`. |
| `LLE > 0` y política `validate` | Solo bloquea si está confirmado; si no, calcula con `ACT`. |
| `LLE > 0` y política `ignore` | No bloquea, pero tampoco suma; puede duplicar si el tránsito es real. |

Configuración:

```yaml
inventory:
  oc_base: "ACT"
  use_committed_in_oc: true
  lle_policy: "block"          # block | validate | ignore
  lle_max_age_days: 90
  lle_protection_buffer_days: 15
```

### Fórmulas

```text
d      = ventas netas 90 días / 90
SS     = z × σd × √LT
S*     = d × (LT + ciclo_revisión) + SS
Need   = max(0, S* − max(0, ACT − COM))
Qty    = min(Need, max(0, d × Cobertura_Max − Inventario_OC))
Qty    = ceil(Qty / múltiplo) × múltiplo
```

Si `Qty < mínimo`:

```text
si (Inventario_OC + mínimo) / d ≤ Cobertura_Max y clase ∈ {A,B}:
    Qty = mínimo
si no:
    Qty = 0 + alerta "MÍNIMO EXCEDE COBERTURA"
```

### KPIs

| KPI | Fórmula | Meta inicial |
|---|---|---|
| Cobertura ACT | `(ACT − COM) / d` | 45–90 días |
| Quiebre | `SKU con ACT=0 y venta>0 / SKU con venta` | < 2% |
| OC duplicada por LLE | `OC con LLE>0 no validado / total OC` | 0 |
| Exceso | `SKU con cobertura > máx / total` | < 10% |

---

## 2. Ciclo de vida: EOL, obsoleto y liquidación

### Estados

| Estado | Regla de detección | ¿Compra? |
|---|---|---|
| `ACTIVO` | Vende y no cumple otro estado | Sí |
| `EN_OBSERVACION` | ≥ 90 días sin venta, sin stock inmovilizado | Revisar; no bloquea por defecto |
| `OBSOLETO` | ≥ 180 días sin venta **y** stock > 0 | **No** |
| `LIQUIDACION` | `Dias_Para_Vencer ≤ 180` y stock > 0 | **No** |
| `EOL` | Manual: proveedor/mercado/`Estado_Ciclo_Vida_Manual=EOL` | **No** |
| `AGOTADO` | EOL/obsoleto con stock ≤ 0 | **No** |
| `EXCLUIDO` | Manual/comercial/regulatorio | **No** |

`OBSOLETO` no se marca solo por “no vender”: exige stock inmovilizado. Un SKU sin ventas y sin stock queda en `EN_OBSERVACION`, no se bloquea.

### Reglas duras

- `Compra_Bloqueada = true` para `EOL`, `OBSOLETO`, `LIQUIDACION`, `EXCLUIDO`, `AGOTADO`.
- `Punto_Pedido = 0`, `SS = 0`, `Forecast = 0` para bloqueados.
- No se genera OC automática, aunque el pronóstico sea positivo.
- Excepción solo con **pedido firme de cliente/contrato**, cantidad comprometida y aprobación de Compras + Comercial.
- No se borra el histórico: se conserva estado, motivo, fecha y usuario.

### Marcado manual

`config/productos.csv` admite:

```csv
EAN,Estado_Ciclo_Vida,Compra_Bloqueada,Sustituto_EAN,Fecha_EOL,Dias_Para_Vencer
759... ,EOL,SI,759... ,2026-09-30,120
```

### Liquidación

1. Detener compras.
2. FEFO (vence primero, sale primero).
3. Traslado a sucursales con demanda.
4. Devolución al proveedor si el contrato lo permite.
5. Descuento escalonado 10/20/30% a 30/60/90 días.
6. Donación o destrucción según regulación sanitaria.
7. Cierre cuando el stock llegue a cero.

### KPIs

| KPI | Fórmula | Meta |
|---|---|---|
| SKU EOL con OC | `OC con SKU bloqueado / total OC` | 0 |
| Valor obsoleto | `Valor stock obsoleto / valor inventario` | < 2% |
| Días para agotar EOL | `Stock / venta diaria plan` | < 90 |
| Recuperación | `Ingresos liquidación / valor a costo` | > 70% |
| SKU con sustituto | `EOL con sustituto / total EOL` | > 90% |

---

## 3. Control de volatilidad de precios

### Referencia

```text
P_ref = Σ(Precio_USD × Cantidad) / Σ(Cantidad)
```

Ventana: compras previas de 90 días (excluyendo los últimos 30 días), mismo EAN, proveedor y unidad, en USD. Si no hay datos, se usa la última compra o el costo actual.

### Clasificación

| Variación | Estado | Acción |
|---|---|---|
| ≤ 5% | `VERDE` | Compra normal |
| 5–10% | `AMARILLO` | Revisar justificación |
| 10–20% | `NARANJA` | Aprobación/negociación |
| > 20% | `ROJO` | **Bloquear OC** y negociar |
| Margen nuevo < mínimo | `MARGEN_BAJO` | **Bloquear** y repreciar |

Además, si el impacto anualizado supera `USD 2.000` y la variación ≥ 5%, se escala la aprobación.

### Fórmulas

```text
Var %       = (P_actual / P_ref − 1) × 100
Impacto     = (P_actual − P_ref) × demanda_anual
P_techo     = P_ref × (1 + max_increase_pct/100)
Margen_nuevo= (P_venta − P_actual) / P_venta × 100
P_venta_min = P_actual / (1 − margen_objetivo)
```

### Configuración

```yaml
price:
  reference_days: 90
  previous_window_days: 90
  recent_window_days: 30
  warning_pct: 5.0
  approval_pct: 10.0
  max_increase_pct: 20.0
  impact_threshold_usd: 2000.0
  block_above_ceiling: true

inventory:
  min_margin_pct: 5.0
```

### Aprobaciones

| Variación | Impacto | Aprobador |
|---|---|---|
| ≤ 5% | Cualquiera | Comprador |
| 5–10% | < USD 2.000 | Jefe de Compras |
| 10–20% o > USD 2.000 | — | Gerente de Compras |
| > 20% o margen bajo | Cualquiera | Gerencia General |

### KPIs

| KPI | Fórmula | Meta |
|---|---|---|
| Precio sobre techo | `OC con P > techo / total OC` | 0 |
| Variación >10% revisada | `SKU >10% con revisión / SKU >10%` | 100% |
| Ahorro negociado | `(P_cotizado − P_negociado) × Q` | 2–5% del gasto |
| IPP | `Σ(P_actual×Q) / Σ(P_base×Q) × 100` | Seguimiento mensual |
| Margen por SKU | `(P_venta − costo) / P_venta` | ≥ mínimo |

---

## 4. Flujo de decisión

```text
1. Snapshot: ACT, COM, LLE, OC abierta, ETA, ventas 90d, estado de vida, precio.
2. ¿Estado bloqueado (EOL/OBSOLETO/LIQUIDACION/EXCLUIDO/AGOTADO)?
   → NO OC. Reportar en EOL Y LIQUIDACIÓN.
3. Calcular d, SS, S*, Need con ACT−COM.
4. ¿Need ≤ 0? → NO OC (stock suficiente).
5. Evaluar precio:
   → ROJO o margen bajo: NO OC. Negociar/repreciar.
   → NARANJA/AMARILLO: generar OC con aprobación.
6. ¿LLE > 0?
   → block/confirmado: NO OC. Validar OC/ETA; excepción si no llega.
   → ignore: continuar (sin sumar LLE).
7. Ajustar mínimo, múltiplo y cobertura máxima.
8. Emitir OC con trazabilidad de precio, LLE y ciclo de vida.
```

---

## 5. Dónde se ve en el reporte

| Hoja | Contenido |
|---|---|
| `ÓRDENES SUGERIDAS` | Columnas nuevas: `Estado EOL`, `Compra Bloqueada`, `Stock OC (ACT−COM)`, `LLE`, `Estado LLE`, `Precio Ref.`, `Var. Precio %`, `Precio Techo`, `Estado Precio`, `Margen Nuevo %`, `Aprobación`. |
| `EOL Y LIQUIDACIÓN` | SKU bloqueados, stock, valor, motivo y acción. |
| `CONTROL DE PRECIOS` | Referencia, actual, variación, impacto, estado, margen y aprobador. |
| `ALERTAS CRÍTICAS` | `PRODUCTO BLOQUEADO EOL`, `LLE PENDIENTE / POSIBLE DUPLICIDAD`, `PRECIO FUERA DE TECHO`, `PRECIO REQUIERE APROBACIÓN`. |

## 6. Resultado de la corrida real (12/11/2026, con filtro de almacenes)

| Indicador | Valor |
|---|---:|
| Órdenes sugeridas | **180** |
| Unidades | **10.536** |
| Inversión | **$32.340** |
| SKU excluidos EOL/obsoletos | **40** |
| Valor de stock bloqueado | **$9.697** |
| SKU con LLE pendiente | **0** |
| Precios bloqueados | **41** |
| Precios por aprobar/revisar | **44** |

El corte anterior sin filtro de almacenes (136 órdenes / $18.271) incluía stock de recepción, tránsito y vencidos; al filtrar `CCS`, `ZUL`, `CONCCS` y `CONZUL` (compras con `REC`) el stock físico baja y las órdenes aumentan. La diferencia no es un cambio de regla: es la corrección de la base de inventario.

## 7. Limitaciones actuales

- `LLE` no tiene todavía OC/ETA vinculada desde Profit; por eso el default es `block`. Cuando exista esa relación, usar `validate` para bloquear solo tránsito confirmado.
- `EOL` manual requiere que Compras registre el estado; la detección automática cubre `OBSOLETO` y `LIQUIDACION`.
- `Dias_Para_Vencer` depende de que el dato de vencimiento esté cargado; si no existe, no se activa liquidación por vencimiento.
- La referencia de precio usa compras previas; para productos nuevos se marca `SIN_REFERENCIA` y requiere revisión.
- El margen se calcula con el precio promedio de venta de los últimos 365 días; no reemplaza un análisis de elasticidad.
