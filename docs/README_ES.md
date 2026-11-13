# Sistema de Compras, AuditorÃ­a y PronÃ³stico
### DroguerÃ­a Emmanuelle Â· Profit 2K12 + Excel

**VersiÃ³n 3.4** Â· ConexiÃ³n Profit de solo lectura Â· 44 pruebas automatizadas

Herramienta para **comprar mejor**: se conecta directamente a Profit 2K12, audita las compras del aÃ±o, pronostica demanda, controla precios y genera Ã³rdenes de compra priorizadas. Incluye una interfaz web local para operar todo sin tocar cÃ³digo.

> **Corte de referencia:** 12/11/2026 Â· 2.985 artÃ­culos Â· 2.082 facturas de venta Â· 649 documentos de compra (215 de mercancÃ­a).

---

## Arranque rÃ¡pido

```bash
pip install -r requirements.txt

# 1. Ã“rdenes desde Profit (recomendado)
python main.py --source profit --as-of 12/11/2026

# 2. AuditorÃ­a de compras 2026
python auditoria.py --year 2026 --as-of 12/11/2026

# 3. Interfaz web local
run_ui.bat
# o: python ui/app.py  ->  http://127.0.0.1:5050
# BI de ventas: http://127.0.0.1:5050/ventas
```

TambiÃ©n funciona con Excel:

```bash
python main.py --sales data/raw/ventas.xlsx ^
               --catalog data/raw/catalogo.xlsx ^
               --stock data/raw/stock.xlsx
```

---

## Requisitos

| Requisito | Detalle |
|---|---|
| Python | 3.11+ (probado en 3.14.7) |
| SQL Server | ODBC Driver 17 for SQL Server |
| Profit 2K12 | Base `EMPRESA_A`, usuario con permiso de **solo lectura** |
| LibrerÃ­as | `pandas`, `numpy`, `openpyxl`, `PyYAML`, `pyodbc`, `scikit-learn`, `Flask`, `pyarrow`, `xlsxwriter`, `weasyprint` |
| Opcional | Microsoft Excel para abrir los reportes; internet para Chart.js (el BI muestra tablas si no hay CDN) |

La conexiÃ³n se configura en `config/database.local.yaml` (no versionado) o con variables `PROFIT_DB_*`. La plantilla estÃ¡ en `config/database.example.yaml`.

---

## Dos modos de operaciÃ³n

| Modo | Comando | CuÃ¡ndo usarlo |
|---|---|---|
| **Profit** | `python main.py --source profit` | OperaciÃ³n real: ventas, compras, stock y trÃ¡nsito en vivo |
| **Excel** | `python main.py --sales ... --catalog ... --stock ...` | Pruebas, simulaciones o empresas sin conexiÃ³n directa |

En modo Profit se aplican todas las reglas: `ACT âˆ’ COM`, bloqueo por `LLE`, ciclo de vida EOL y control de precios. En modo Excel, el control de precios queda en `SIN_REFERENCIA` si no hay historial de compras, y el ciclo de vida se calcula con ventas + stock.

---

## QuÃ© resuelve

| MÃ³dulo | Pregunta que responde | Salida |
|---|---|---|
| **AuditorÃ­a** | Â¿Compramos bien? Â¿QuÃ© nos falta? | Excel de 10 hojas + dashboard HTML |
| **Ã“rdenes** | Â¿QuÃ© debo comprar esta semana? | Excel de 11 hojas con prioridades, EOL y precios |
| **Ventas** | Â¿QuÃ© se vende, a quiÃ©n y con quÃ© margen? | BI `/ventas` + API JSON + Excel |
| **Almacenes** | Â¿QuÃ© almacenes entran al anÃ¡lisis? | Selector persistente + filtro en ventas, stock, compras y OC |
| **Reglas OC** | Â¿Con quÃ© criterio se decide? | `ACTâˆ’COM`, LLE, EOL, precio techo y aprobaciones |
| **ML / DL** | Â¿Un modelo global mejora el pronÃ³stico? | ComparaciÃ³n validada contra media mÃ³vil |
| **Interfaz** | Â¿CÃ³mo lo opero sin scripts? | Web local en `127.0.0.1:5050` |

---

## Filtro de almacenes

Profit 2K12 **no tiene una columna `activo` en `saAlmacen`**. La tabla trae banderas operativas (`noventa`, `nocompra`, `materiales`, `produccion`, `alm_temp`). El sistema usa una selecciÃ³n explÃ­cita y validada contra los cÃ³digos existentes.

| Concepto | ImplementaciÃ³n |
|---|---|
| Almacenes de venta/stock | `CCS`, `ZUL`, `CONCCS`, `CONZUL` (configurables) |
| RecepciÃ³n para compras | `REC` se incluye en compras para conservar el costo real |
| Fuente de stock | `saStockAlmacen` (tiene `co_alma`); `v_saStockActual` no permite filtrar por almacÃ©n |
| Persistencia | `config/warehouse_selection.json` (no versionado) |
| Selector | PestaÃ±a **ConfiguraciÃ³n** de la interfaz |
| Trazabilidad | Hoja `PARÃMETROS` del Excel + footer del dashboard + auditorÃ­a |

```yaml
warehouse:
  selection_file: "config/warehouse_selection.json"
  selected: [CCS, ZUL, CONCCS, CONZUL]
  purchase_extra: [REC]
  purchase_mode: "selected_plus_extra"   # selected_only
  stock_source: "saStockAlmacen"
```

**Impacto del filtro:** el stock fÃ­sico pasÃ³ de 75.831 a **67.637 unidades** y el `LLE` de 87.876 a **177 unidades** al excluir `REC`, `TRF`, `VENCID`, `PROY` y `MSALUD`. Esto corrige la contaminaciÃ³n de KPIs y cambia las Ã³rdenes: el corte filtrado del 12/11/2026 generÃ³ **180 Ã³rdenes, 10.536 unidades y $32.340**, frente a 136/$18.271 del corte anterior sin filtro.

---

## Resultados de la auditorÃ­a 2026

> Corte histÃ³rico **23/10/2026, sin filtro de almacenes**. El corte filtrado del 12/11/2026 se resume al final de esta secciÃ³n.

| Indicador | Valor | Lectura |
|---|---:|---|
| Compra de mercancÃ­a | **$411.376** | 132.654 unidades |
| Venta de mercancÃ­a | **$531.072** | 133.044 unidades |
| Costo de venta | $341.881 | margen bruto **35,6%** |
| Compra / venta en unidades | **99,7%** | el monto global estÃ¡ equilibrado |
| Inventario + trÃ¡nsito | **$344.198** | 362 dÃ­as de venta |
| Quiebres reales | **249 SKU** | $69.597 de venta anual en esos SKU |
| SKU sobrecomprados | **338** | $253.077 de capital en exceso |
| Sin venta con stock | **351 SKU** | $25.497 inmovilizados |
| Vendieron sin compra 2026 | **375 SKU** | $87.030 |
| Proveedores | 32 | top 5 = 70,4% (HHI 0,107) |
| Compras por usuario | 2 | NMORA 99,3% / SOPOR 0,7% |
| VariaciÃ³n de costo USD | +12,3% mediana | 165 productos > +15% |

**Veredicto:** en agregado se comprÃ³ bien â€”se comprÃ³ 99,7% de lo vendido y el margen es sanoâ€”, pero la **asignaciÃ³n por producto es deficiente**: hay mucho capital en SKU que no rotan o estÃ¡n sobrecomprados y, al mismo tiempo, 249 quiebres reales. El problema no es â€œcuÃ¡ntoâ€ se compra, sino **quÃ©** y **cuÃ¡ndo**.

**Corte filtrado 12/11/2026:** compra **$452.770**, venta **$561.770**, margen **35,0%**, **347 quiebres**, **304 SKU sobrecomprados**, **$116.823** de capital en exceso y **40 SKU EOL/obsoletos**. Las diferencias frente al corte histÃ³rico se explican por excluir almacenes de recepciÃ³n, trÃ¡nsito, vencidos y ajuste.

El detalle completo, con listas de SKU y acciones, estÃ¡ en [`docs/AUDITORIA_2026.md`](docs/AUDITORIA_2026.md).

### Impacto econÃ³mico potencial

| Palanca | Base filtrada | Mejora conservadora | Potencial |
|---|---:|---:|---:|
| Capital en exceso | $116.823 | reducciÃ³n 15â€“20% | **$17.500 â€“ $23.400** de caja |
| Stock obsoleto | $9.697 | recuperaciÃ³n 40â€“50% | **$3.900 â€“ $4.800** |
| Quiebres | $117.688 venta anual | recuperaciÃ³n 10â€“20% | **$4.100 â€“ $8.200** de margen |
| Precio de compra | $452.770 | ahorro 1â€“2% | **$4.500 â€“ $9.100** |
| Tiempo del comprador | 0,5 FTE | 30â€“50% menos | **$3.000 â€“ $6.000** |

**Beneficio anual conservador:** $15.000â€“$25.000, mÃ¡s una liberaciÃ³n de caja Ãºnica de $20.000â€“$28.000 si se ejecuta la liquidaciÃ³n y se corrige la sobrecompra. Es potencial, no garantizado: depende de que Compras ejecute las decisiones.

---

## Reglas de OC implementadas

### 1. Base `ACT âˆ’ COM`

```text
Inventario_OC = max(0, ACT âˆ’ COM)
LLE = 0 en la fÃ³rmula
```

- `ACT`: stock fÃ­sico actual.
- `COM`: comprometido/ventas pendientes.
- `LLE`: stock por llegar; **nunca se suma** como disponible.

`Stock_Disponible = ACT âˆ’ COM + LLE` se conserva solo para reportes histÃ³ricos. La decisiÃ³n usa `Stock_Usado_OC`.

### 2. `LLE` como bloqueo, no como stock

Si hay trÃ¡nsito, la OC automÃ¡tica se bloquea para evitar duplicidad:

| SituaciÃ³n | Resultado |
|---|---|
| `LLE > 0`, polÃ­tica `block` (default) | `LLE_PENDIENTE`; validar OC/ETA |
| `LLE > 0` con OC/ETA confirmada | `CUBIERTO_POR_TRANSITO` |
| `LLE` sin confirmar o vencido | `LLE_NO_CONFIABLE`; escalar |
| `LLE = 0` | CÃ¡lculo normal con `ACT âˆ’ COM` |

PolÃ­tica configurable: `block`, `validate` o `ignore`.

### 3. Ciclo de vida EOL

| Estado | Regla | Â¿Compra? |
|---|---|---|
| `ACTIVO` | Vende y no cumple otro estado | SÃ­ |
| `EN_OBSERVACION` | â‰¥ 90 dÃ­as sin venta, sin stock inmovilizado | Revisar |
| `OBSOLETO` | â‰¥ 180 dÃ­as sin venta **y** stock > 0 | **No** |
| `LIQUIDACION` | Vence en â‰¤ 180 dÃ­as y tiene stock | **No** |
| `EOL` | Manual/proveedor/mercado | **No** |
| `AGOTADO` | EOL/obsoleto sin stock | **No** |
| `EXCLUIDO` | DecisiÃ³n comercial/regulatoria | **No** |

Los estados bloqueados tienen `Compra_Bloqueada = true`, `Punto_Pedido = 0`, `SS = 0` y no generan OC automÃ¡tica. La excepciÃ³n es un pedido firme con aprobaciÃ³n de Compras y Comercial.

Se pueden marcar manualmente en `config/productos.csv`:

```csv
EAN,Estado_Ciclo_Vida,Compra_Bloqueada,Sustituto_EAN,Fecha_EOL,Dias_Para_Vencer
759...,EOL,SI,759...,2026-09-30,120
```

### 4. Control de precios

```text
P_ref  = promedio ponderado USD de compras previas (90 dÃ­as)
Var %  = (P_actual / P_ref âˆ’ 1) Ã— 100
P_techo = P_ref Ã— (1 + 20%)
```

| VariaciÃ³n | Estado | AcciÃ³n |
|---|---|---|
| â‰¤ 5% | `VERDE` | Compra normal |
| 5â€“10% | `AMARILLO` | Revisar justificaciÃ³n |
| 10â€“20% | `NARANJA` | AprobaciÃ³n/negociaciÃ³n |
| > 20% | `ROJO` | **Bloquear OC** |
| Margen nuevo < 5% | `MARGEN_BAJO` | **Bloquear** y repreciar |

Si el impacto anual supera **USD 2.000**, se escala la aprobaciÃ³n. El Excel muestra referencia, precio actual, variaciÃ³n, techo, margen, aprobador y si bloquea la OC.

**DocumentaciÃ³n completa:** [`docs/REGLAS_OC.md`](docs/REGLAS_OC.md)
**Base RAG machine-readable:** [`config/reglas_oc.yaml`](config/reglas_oc.yaml) â€” 15 reglas, 12 KPIs y flujo de decisiÃ³n.

---

## AuditorÃ­a: quÃ© incluye

- **Resumen ejecutivo** con veredicto y hallazgos.
- **Productos** (1.314 SKU): compra, venta, margen, stock, trÃ¡nsito, cobertura, ratio compra/venta, exceso y faltante.
- **Quiebres y faltantes** priorizados por venta.
- **Sobrecompra y capital inmovilizado** con exceso en USD.
- **Proveedores**: compra, share, tickets, SKU, variaciÃ³n de costo, devoluciones.
- **CategorÃ­as**: compra vs venta, margen, stock, trÃ¡nsito.
- **Compradores**: concentraciÃ³n de la gestiÃ³n.
- **Precios y margen**: variaciÃ³n 2025â†’2026 y productos con margen negativo.
- **Mensual**: compra vs venta y estacionalidad.
- **MetodologÃ­a y calidad de datos**.

```bash
python auditoria.py --year 2026 --as-of 12/11/2026
```

Salidas: `outputs/auditoria_compras_2026_*.xlsx` y `.html`.

---

## Ã“rdenes desde Profit

El pipeline toma de Profit:

| Dato | Tabla / vista |
|---|---|
| Compras | `saFacturaCompra` + `saFacturaCompraReng` |
| Ventas | `saFacturaVenta` + `saFacturaVentaReng` |
| ArtÃ­culos | `saArticulo` + lÃ­nea/sub-lÃ­nea/categorÃ­a |
| Proveedores | `saProveedor`, `saArtProveedorReng` |
| Stock / trÃ¡nsito / comprometido | `saStockAlmacen` (`ACT`, `LLE`, `COM`, por `co_alma`) |

Con las reglas activas y el filtro de almacenes, la corrida del 12/11/2026 generÃ³:

| Indicador | Valor |
|---|---:|
| Ã“rdenes sugeridas | **180** |
| Unidades | **10.536** |
| InversiÃ³n | **$32.340** |
| SKU excluidos EOL/obsoletos | **40** |
| Stock bloqueado EOL | **$9.697** |
| SKU con LLE pendiente | **0** |
| Precios bloqueados | **41** |
| Precios por revisar/aprobar | **44** |

El filtro de almacenes eliminÃ³ `REC`, `TRF`, `VENCID`, `PROY` y `MSALUD` del stock, por eso el `LLE` baja a cero y las Ã³rdenes aumentan frente al corte histÃ³rico sin filtro (136 Ã³rdenes / $18.271).

### Hojas del reporte de Ã³rdenes (11)

1. `RESUMEN`
2. `Ã“RDENES SUGERIDAS`
3. `EOL Y LIQUIDACIÃ“N`
4. `CONTROL DE PRECIOS`
5. `ALERTAS CRÃTICAS`
6. `ANÃLISIS ABC-XYZ`
7. `DETALLE PRODUCTOS`
8. `PRECISIÃ“N MODELOS`
9. `PARÃMETROS`
10. `CATÃLOGO FALTANTE`
11. `DATOS Y CALIDAD`

```bash
python main.py --source profit --as-of 12/11/2026 --no-open
```

---

## Machine learning y aprendizaje profundo

Se implementÃ³ un modelo **global** (todos los productos a la vez) con:

- **Gradient Boosting** (`HistGradientBoostingRegressor`).
- **Red neuronal profunda** (`MLPRegressor`, 2 capas ocultas) con calibraciÃ³n de sesgo.
- **HÃ­brido**: red neuronal para SKU de volumen medio/alto y media mÃ³vil para la cola de baja rotaciÃ³n.
- Variables: lags, medias mÃ³viles, desviaciÃ³n, intermitencia, tendencia, estacionalidad, precio, lÃ­nea y categorÃ­a.

ValidaciÃ³n temporal de 6 meses:

| Modelo | WAPE | Sesgo | Hit Â±25% |
|---|---:|---:|---:|
| MA4 Ã— 4 | 101,0% | âˆ’2,8% | 14,8% |
| MA8 Ã— 4 | 97,0% | âˆ’2,9% | 16,8% |
| Gradient Boosting | 116,0% | +5,4% | 18,6% |
| Red neuronal (MLP) | 101,9% | âˆ’9,2% | 18,1% |
| **HÃ­brido (MLP + MA8)** | **96,2%** | âˆ’4,4% | 17,4% |

**ConclusiÃ³n honesta:** el ML/DL no supera de forma material a una media mÃ³vil en este portafolio intermitente; el hÃ­brido mejora solo 0,8 puntos de WAPE. Por eso el motor clÃ¡sico sigue siendo el predeterminado y el ML se ofrece como alternativa monitoreada. La mayor ganancia vendrÃ¡ de registrar quiebres, lead times y stock real, no de cambiar de algoritmo. Detalle en [`docs/ML.md`](docs/ML.md).

---

## Interfaz web

```bash
run_ui.bat
```

Abre `http://127.0.0.1:5050` con:

- **Resumen** con Ãºltimo resultado.
- **AuditorÃ­a**: aÃ±o, fecha de corte, ejecutar y descargar.
- **Ã“rdenes**: nivel de servicio, lead time, generar Excel.
- **Modelos ML/DL**: entrenar y comparar.
- **ConfiguraciÃ³n**: parÃ¡metros vigentes y estado de la conexiÃ³n.

La pestaÃ±a de Ã³rdenes muestra Ã³rdenes, unidades, inversiÃ³n, **excluidos EOL**, **LLE pendientes** y **precios por aprobar**, ademÃ¡s de las tablas de exclusiÃ³n y precios.

La interfaz no expone credenciales: el navegador solo recibe mÃ©tricas y nombres de archivo. La conexiÃ³n se abre en el servidor local.

---

## MÃ³dulo de Ventas (BI interno)

Abre `http://127.0.0.1:5050/ventas`.

| SecciÃ³n | Contenido |
|---|---|
| Resumen 90d | Venta neta USD/Bs, margen, ticket promedio, clientes, SKU y laboratorio #1 |
| GrÃ¡ficos | Serie diaria, top laboratorios, top productos, top clientes, dona por laboratorio y heatmap dÃ­a de semana Ã— mes |
| Drill-down | Cliente â†’ laboratorios â†’ productos, con ficha, Ãºltima compra y serie mensual |
| Filtros | Fechas, almacenes, laboratorio y categorÃ­a; persisten en la sesiÃ³n |
| ExportaciÃ³n | Excel multi-hoja (`xlsxwriter`) y PDF con grÃ¡ficas (`WeasyPrint`; fallback Edge/Chromium) |

### Endpoints

| Ruta | DescripciÃ³n |
|---|---|
| `/ventas/api/resumen` | KPIs 90d y comparaciÃ³n vs. perÃ­odo anterior |
| `/ventas/api/laboratorios` | Ranking por laboratorio real |
| `/ventas/api/lineas` | Ranking por lÃ­nea terapÃ©utica (dimensiÃ³n separada) |
| `/ventas/api/productos` | Top productos paginado |
| `/ventas/api/clientes` | Top clientes paginado |
| `/ventas/api/cliente/<cod>` | Drill-down del cliente |
| `/ventas/api/filtros` | Laboratorios, categorÃ­as y sublÃ­neas |
| `/ventas/api/export` | Excel multi-hoja del perÃ­odo filtrado |
| `/ventas/api/export/pdf` | PDF con KPIs, grÃ¡ficas y detalle cliente-laboratorio (POST) |
| `/ventas/api/cliente/<cod>/pdf` | PDF individual del cliente: ficha, laboratorios y productos |
| `/ventas/api/almacenes` | Almacenes disponibles y selecciÃ³n |

ParÃ¡metros: `?desde=YYYY-MM-DD&hasta=YYYY-MM-DD&almacen=CCS,ZUL&laboratorio=BAYER&linea=ANSIOLITICO&categoria=MEDICAMENTOS`.

**DimensiÃ³n de laboratorio:** `saLinea` es la **lÃ­nea terapÃ©utica**, no el laboratorio. El laboratorio real se carga desde [docs/PRODUCTOS POR LABORATORIO.md](<docs/PRODUCTOS POR LABORATORIO.md>), se normaliza el cÃ³digo y se cruza con `EAN`/`co_art`; los no encontrados se muestran como `SIN_LABORATORIO`. En el corte actual la cobertura es **96,5% de la venta neta** y **97,7% de los cÃ³digos**.

### ExportaciÃ³n

- **Excel:** botÃ³n **Exportar Excel** â†’ `ventas_<desde>_<hasta>.xlsx`, generado con `xlsxwriter`. Hojas: `RESUMEN`, `SERIE_DIARIA`, `LABORATORIOS`, `LINEAS_TERAPEUTICAS`, `PRODUCTOS`, `CLIENTES`, **`Detalle_Cliente_Laboratorio`**, `DETALLE_VENTAS` y `GRAFICAS`.
  - `Detalle_Cliente_Laboratorio` usa **`Laboratorio`** de la dimensiÃ³n maestra, no `Linea_Terapeutica`; conserva las filas `SIN_LABORATORIO`.
- **PDF:** botÃ³n **Exportar PDF** â†’ captura los canvas de Chart.js con `toBase64Image()`, envÃ­a imÃ¡genes + KPIs por `POST /ventas/api/export/pdf` y el backend renderiza el template `export_pdf.html` con **WeasyPrint** (si estÃ¡ disponible) o **Edge/Chromium headless** como fallback.
- **PDF por cliente:** en el drill-down, botÃ³n **Exportar PDF** â†’ descarga la ficha del cliente, laboratorios comprados y productos comprados.
- Ambos respetan los filtros de fecha, almacÃ©n, laboratorio, lÃ­nea terapÃ©utica y categorÃ­a.

### ImplementaciÃ³n

- `src/analytics/ventas_analytics.py`: KPIs, agrupaciones, drill-down, cache TTL 15 min.
- `src/data_processing/laboratorio_loader.py`: parsing, normalizaciÃ³n y cruce de la dimensiÃ³n maestra de laboratorio; cache `outputs/dim_laboratorio.parquet`.
- `src/reporting/ventas_export.py`: Excel multi-hoja con `xlsxwriter` y hoja `Detalle_Cliente_Laboratorio`.
- `src/reporting/ventas_pdf.py`: PDF con WeasyPrint y fallback Edge/Chromium.
- `ui/templates/ventas/export_pdf.html`: plantilla limpia para el PDF.
- `ui/ventas_bp.py`: blueprint Flask.
- `ui/templates/ventas/`: `base_ventas.html`, `resumen_90d.html`, `por_laboratorio.html`, `por_producto.html`, `por_cliente.html`, `drilldown_cliente.html`.
- `ui/static/js/ventas_charts.js`: Chart.js + heatmap CSS.
- `ui/static/css/ventas.css`: layout del mÃ³dulo.

**Nota tÃ©cnica:** `saFacturaVentaReng` no tiene una columna de costo (`costocotizado` no existe en esta instalaciÃ³n). El margen se calcula con el costo unitario ponderado de compras de los Ãºltimos 365 dÃ­as, filtrado por los almacenes de compra. Si no hay costo, el margen se reporta como no cubierto.

**Rendimiento:** el perÃ­odo de 90 dÃ­as se cachea 15 minutos; la primera consulta en frÃ­o tarda ~7 s y las siguientes <1 s. Al iniciar la interfaz se precalienta el perÃ­odo por defecto. El objetivo es <3 s en caliente; si se requiere <3 s en frÃ­o, se puede activar `ventas.parallel_queries` en instalaciones donde SQL Server lo permita.

**Chart.js** se carga por CDN; si no hay internet, el mÃ³dulo muestra las tablas y un aviso de grÃ¡ficos no disponibles.

---

## Seguridad de la conexiÃ³n

- Solo se permiten sentencias **SELECT / WITH**; el conector rechaza `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `EXEC`, etc.
- ConexiÃ³n con `autocommit` y `ApplicationIntent=ReadOnly`.
- La contraseÃ±a vive en `config/database.local.yaml`, excluido por `.gitignore`; tambiÃ©n puede usar variables `PROFIT_DB_SERVER`, `PROFIT_DB_NAME`, `PROFIT_DB_USER`, `PROFIT_DB_PASSWORD`.
- No se imprime ni se envÃ­a al navegador.

---

## ConfiguraciÃ³n

| Archivo | QuÃ© controla |
|---|---|
| `config/database.local.yaml` | ConexiÃ³n Profit (local, no versionar) |
| `config/database.example.yaml` | Plantilla de conexiÃ³n |
| `config/parameters.yaml` | Horizontes, ABC/XYZ, servicio, `inventory`, `lifecycle`, `price`, `warehouse` y `ventas` |
| `config/warehouse_selection.json` | Almacenes seleccionados en la UI (local, no versionado) |
| `docs/PRODUCTOS POR LABORATORIO.md` | DimensiÃ³n maestra de laboratorio real (cÃ³digo â†’ laboratorio) |
| `config/reglas_oc.yaml` | Reglas, KPIs y flujo para el RAG |
| `config/proveedores.csv` | Lead time, mÃ­nimo, mÃºltiplo y descuentos |
| `config/productos.csv` | Overrides por EAN, estado EOL y bloqueo manual |

### ParÃ¡metros clave

```yaml
inventory:
  oc_base: "ACT"
  use_committed_in_oc: true
  lle_policy: "block"          # block | validate | ignore
  max_coverage_days: 120
  min_margin_pct: 5.0

lifecycle:
  observation_days: 90
  obsolete_days: 180
  liquidation_days: 180
  block_states: [EOL, OBSOLETO, LIQUIDACION, EXCLUIDO, AGOTADO]

warehouse:
  selected: [CCS, ZUL, CONCCS, CONZUL]
  purchase_extra: [REC]
  purchase_mode: "selected_plus_extra"
  stock_source: "saStockAlmacen"

ventas:
  default_days: 90
  max_days: 366
  cache_ttl_seconds: 900
  parallel_queries: false

price:
  reference_days: 90
  warning_pct: 5.0
  approval_pct: 10.0
  max_increase_pct: 20.0
  impact_threshold_usd: 2000.0
```

Para la auditorÃ­a:

```yaml
audit:
  target_coverage_days: 90
  critical_coverage_days: 15
  low_coverage_days: 30
  overbuy_coverage_days: 180
  default_lead_time_days: 30
```

---

## Estructura

```text
â”œâ”€â”€ main.py                     # pipeline de Ã³rdenes (Excel o Profit)
â”œâ”€â”€ auditoria.py                # auditorÃ­a de compras
â”œâ”€â”€ run.py / run.bat            # lanzador de Ã³rdenes (Excel)
â”œâ”€â”€ run_profit.bat              # lanzador de Ã³rdenes desde Profit
â”œâ”€â”€ run_auditoria.bat           # lanzador de auditorÃ­a
â”œâ”€â”€ run_ui.bat                  # interfaz web (compras + ventas)
â”œâ”€â”€ ui/
â”‚   â”œâ”€â”€ app.py                  # app Flask principal
â”‚   â”œâ”€â”€ ventas_bp.py            # blueprint /ventas
â”‚   â”œâ”€â”€ templates/
â”‚   â”‚   â”œâ”€â”€ index.html          # dashboard de compras
â”‚   â”‚   â”œâ”€â”€ config.html         # selector de almacenes
â”‚   â”‚   â””â”€â”€ ventas/             # resumen, laboratorio, producto, cliente y drill-down
â”‚   â””â”€â”€ static/
â”‚       â”œâ”€â”€ app.js
â”‚       â”œâ”€â”€ css/ventas.css
â”‚       â””â”€â”€ js/ventas_charts.js
â”œâ”€â”€ config/
â”‚   â”œâ”€â”€ database.local.yaml     # credenciales (ignorado)
â”‚   â”œâ”€â”€ database.example.yaml   # plantilla
â”‚   â”œâ”€â”€ parameters.yaml
â”‚   â”œâ”€â”€ reglas_oc.yaml          # reglas/KPIs/flujo para el RAG
â”‚   â”œâ”€â”€ warehouse_selection.json # selecciÃ³n local (ignorado)
â”‚   â”œâ”€â”€ proveedores.csv
â”‚   â””â”€â”€ productos.csv
â”œâ”€â”€ src/
â”‚   â”œâ”€â”€ data_processing/        # loader, Profit, warehouse_filter, laboratorio_loader
â”‚   â”œâ”€â”€ analytics/              # ABC/XYZ + ventas_analytics
â”‚   â”œâ”€â”€ models/                 # pronÃ³stico clÃ¡sico + ML/DL global
â”‚   â”œâ”€â”€ recommendation/         # Ã³rdenes, precios y alertas
â”‚   â”œâ”€â”€ audit/                  # auditorÃ­a de compras
â”‚   â”œâ”€â”€ reporting/              # Excel/dashboards, ventas_export, ventas_pdf
â”‚   â””â”€â”€ utils/
â”œâ”€â”€ tests/                      # 44 pruebas
â”œâ”€â”€ docs/
â”‚   â”œâ”€â”€ AUDITORIA_2026.md
â”‚   â”œâ”€â”€ PROFIT.md
â”‚   â”œâ”€â”€ REGLAS_OC.md
â”‚   â”œâ”€â”€ ML.md
â”‚   â””â”€â”€ GUIA_USUARIO.md
â””â”€â”€ outputs/
```

---

## Pruebas

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

**44 pruebas** que cubren:

- NormalizaciÃ³n, ABC/XYZ y segmentaciÃ³n.
- CÃ¡lculo de Ã³rdenes y mÃºltiplos.
- GeneraciÃ³n de Excel y dashboard.
- ConexiÃ³n Profit de solo lectura.
- ConversiÃ³n USD/BS.
- Reglas `ACT âˆ’ COM`, bloqueo `LLE`, EOL, techo de precio y margen bajo.
- Filtro de almacenes, inyecciÃ³n de `IN (...)`, KPIs 90d, drill-down y cache de ventas.

---

## Entregables de ejemplo

- [AuditorÃ­a 2026 filtrada Â· Excel](outputs/auditoria_compras_2026_20261112_093016.xlsx)
- [AuditorÃ­a 2026 filtrada Â· Dashboard](outputs/auditoria_compras_2026_20261112_093016.html)
- [Ã“rdenes desde Profit con filtro de almacenes](outputs/ordenes_compra_profit_20261112_093711.xlsx)
- [Dashboard de compras con filtro](outputs/dashboard_20261112_093711.html)
- [Ventas 90d Â· Excel con laboratorio real](outputs/ventas_2026-08-15_2026-11-12.xlsx)
- [Ventas 90d Â· PDF con grÃ¡ficas y detalle cliente-laboratorio](outputs/ventas_2026-08-15_2026-11-12.pdf)
- [Ejemplo PDF por cliente Â· FARMA MARKET 24](outputs/cliente_C1326.pdf)
- [Reporte del modo Excel](outputs/ordenes_compra_20261023_115239.xlsx)

---

## Limitaciones y siguientes pasos

1. **TrÃ¡nsito (`LLE`)**: no se suma a la OC; por defecto bloquea la emisiÃ³n hasta validar la orden/ETA. Si el trÃ¡nsito no refleja Ã³rdenes reales, las Ã³rdenes sugeridas cambiarÃ¡n. Validar con el equipo de compras.
2. **Costo**: es el promedio ponderado de compras 2026; si no hay compra, la Ãºltima compra histÃ³rica.
3. **CategorÃ­as**: `NO APLICA` y servicios deben depurarse en Profit.
4. **Quiebres histÃ³ricos**: no existen; el sistema detecta el sÃ­ntoma (venta sin stock), no la causa.
5. **Lead time real**: aÃºn no se calcula desde orden de compra a recepciÃ³n; se usa el configurado.
6. **EOL manual**: la detecciÃ³n automÃ¡tica cubre obsolescencia y vencimiento; el aviso del proveedor debe registrarse en `config/productos.csv`.
7. **Precios sin historial**: los productos sin compras previas quedan en `SIN_REFERENCIA` y requieren revisiÃ³n.
8. **ML/DL**: mejora marginal; se recomienda activarlo solo para SKU de volumen alto y reentrenar mensualmente.
9. **Almacenes**: `saAlmacen` no tiene columna `activo`; la selecciÃ³n es explÃ­cita y se valida contra los cÃ³digos. `v_saStockActual` no tiene `co_alma`; el stock filtrado usa `saStockAlmacen`.
10. **Ventas**: el margen se calcula con costo de compras, no con un costo contable por factura; los SKU sin compra quedan fuera de la cobertura de costo. El heatmap usa dÃ­a de semana Ã— mes porque `fec_emis` no guarda hora.
11. **Rendimiento BI**: la primera consulta en frÃ­o tarda ~7 s; queda cacheada 15 min y la interfaz precalienta el perÃ­odo por defecto. Para <3 s en frÃ­o se requiere optimizaciÃ³n SQL adicional o habilitar consultas paralelas en una instalaciÃ³n compatible.

**Plan de 30 dÃ­as:** depurar catÃ¡logo/proveedor de los SKU activos sin asignar, cargar quiebres, separar comprador/aprobador, congelar compras de los 304 SKU sobrecomprados del corte filtrado, liquidar los 40 EOL/obsoletos y reponer los 347 quiebres detectados.

---

