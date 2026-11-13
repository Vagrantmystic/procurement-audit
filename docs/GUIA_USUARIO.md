# Guía rápida para el equipo de compras

Esta guía explica cómo usar el reporte semanal en menos de 10 minutos.

---

## 1. Dos formas de operar

### Interfaz web (recomendada)

1. Haga doble clic en **`run_ui.bat`**.
2. Abra `http://127.0.0.1:5050`.
3. Elija una pestaña:
   - **Auditoría 2026**: ¿compramos bien? ¿qué nos falta?
   - **Órdenes de compra**: genera el Excel con las órdenes.
   - **Modelos ML / DL**: compara modelos.
   - **Resumen**: últimos resultados.

La interfaz consulta Profit directamente; no necesita archivos Excel.

### Modo Excel

1. Coloque los archivos actualizados en `data/raw/`:
   - `ventas.xlsx`
   - `catalogo.xlsx`
   - `stock.xlsx`
2. Haga doble clic en **`run.bat`** (o ejecute `python main.py`).
3. Espere ~20 segundos. Se abre el Excel automáticamente.

Si el Excel no se abre, búsquelo en la carpeta `outputs/`.

---

## 2. Rutina semanal sugerida

### Paso 1 — Revisar el RESUMEN
Mire los tres números grandes:

- **Órdenes sugeridas** y **monto de inversión**: ¿está dentro del presupuesto?
- **Alertas críticas**: ¿hay quiebres que requieren llamada inmediata?

### Paso 2 — Atender las CRÍTICAS
En **ÓRDENES SUGERIDAS**, filtre `Prioridad = CRÍTICA`.

Son productos que se agotan **antes** de que llegue el pedido. Acción: contactar al proveedor hoy.

### Paso 3 — Revisar las ALTAS
Filtre `Prioridad = ALTA`. Programar el pedido de la semana.

Cada línea trae una **justificación**. Ejemplo:

> Cobertura actual 7 d (lead time 30 d); demanda 37 d: 378 u; stock: 76 u; stock de seguridad: 221 u.

Si el proveedor no aparece o el costo dice “estimado”, revise la hoja **CATÁLOGO FALTANTE**.

### Paso 4 — Revisar ALERTAS CRÍTICAS
| Alerta | Qué hacer |
|--------|-----------|
| STOCK CRÍTICO / QUIEBRE | Comprar urgente |
| EXCESO DE INVENTARIO | Suspender compras, evaluar traslado/liquidación |
| POSIBLE QUIEBRE | Verificar físicamente si hay stock |
| CAMBIO DE TENDENCIA | Investigar con ventas antes de comprar |
| SIN VENTAS / DESCONTINUACIÓN | Confirmar si el producto sigue vigente |
| CATÁLOGO INCOMPLETO | Asignar proveedor y costo |
| INVENTARIO NO INFORMADO | Cargar stock real |

### Paso 5 — Enviar pedidos
Agrupe por **Proveedor** (hoja RESUMEN o columna Proveedor). La columna **Cajas** indica cuántos bultos pedir.

### Paso 6 — Archivar
Guarde el Excel en la carpeta del mes. No borre el archivo: es la evidencia de la decisión.

---

## 3. Cómo leer las columnas clave

| Columna | Significado |
|---------|-------------|
| **Prioridad** | Urgencia de compra (CRÍTICA → BAJA) |
| **Stock Actual** | Inventario disponible; vacío = no informado |
| **Demanda 30d** | Pronóstico de unidades para 30 días |
| **Demanda Cobertura** | Demanda hasta que llegue el pedido (lead time + revisión) |
| **Stock Seguridad** | Colchón para absorber error de pronóstico |
| **Punto Pedido** | Nivel que dispara la reposición |
| **Cant. Sugerida** | Unidades a comprar, ya redondeadas al múltiplo |
| **Cajas** | Cantidad / múltiplo del proveedor |
| **Días Cobertura** | Para cuántos días alcanza el stock actual |
| **Tendencia %** | Variación de ventas 30 d vs 30 d previos |
| **Confianza** | 0 a 1; menor confianza = revisar manualmente |
| **Justificación** | Explicación del cálculo |

---

## 4. Ajustar parámetros

### Lead time de un proveedor
Edite `config/proveedores.csv`:

```csv
Proveedor,Lead_Time_Dias,Dias_Revision,Minimo_Pedido,Multiplo_Compra,...
VALMORCA,30,7,24,24,...
```

### Múltiplo o stock mínimo de un producto
Cree/edite `config/productos.csv`:

```csv
EAN,Multiplo_Compra,Stock_Minimo,Stock_Maximo,Lead_Time,Activo
7591020005319,36,48,720,30,SI
```

### Nivel de servicio
En `config/parameters.yaml`:

```yaml
inventory:
  service_level: 0.95   # 0.90, 0.95, 0.975, 0.99
```

Mayor nivel de servicio = más stock de seguridad y más inversión.

### Política si no hay stock informado
```yaml
inventory:
  unknown_stock_policy: minimum   # minimum | zero | skip
```

- `minimum`: sugiere solo la demanda del lead time (recomendado).
- `zero`: asume stock 0 y pide el objetivo completo (más agresivo).
- `skip`: no genera orden; solo alerta.

---

## 5. Preguntas frecuentes

**¿Por qué hay productos sin stock informado?**
Porque `stock.xlsx` no contiene su EAN. El sistema no inventa el stock: sugiere la demanda del lead time y lo marca. Cargue el inventario completo para prioridades exactas.

**¿Por qué la cantidad es 24 si la demanda es de 3 unidades?**
Porque el múltiplo mínimo de compra es 24. Si el producto es de baja rotación, el sistema evita la orden automática y genera la alerta “COMPRA MÍNIMA EXCEDE COBERTURA”.

**¿Por qué el costo es distinto al del proveedor?**
Si el producto no está en el catálogo, el costo se estima desde el precio de venta (margen 15%). La justificación lo indica. Complete el catálogo para costos reales.

**¿Por qué el pronóstico tiene error alto?**
La demanda es intermitente (la mitad de los periodos no tiene venta). El sistema controla el sesgo y usa stock de seguridad; no promete precisión imposible. Vea `docs/ACEPTACION.md`.

**¿Puedo simular un mes pasado?**
Sí: `python main.py --as-of 30/09/2025`. Sirve para comparar contra lo que realmente se compró.

**¿Dónde está el detalle del cálculo?**
En la columna Justificación y en `docs/METODOLOGIA.md`.

---

## 6. Contacto y soporte

- Revise `outputs/logs/ejecucion_*.log` ante cualquier error.
- Los archivos de configuración están versionados; no los borre.
- Para cambios de alcance (nuevas fuentes, caducidades, Power BI), contacte al equipo de desarrollo.

---

## 7. Auditoría de compras

La auditoría responde dos preguntas de gerencia: **¿compramos bien?** y **¿qué nos falta?**

### Cómo ejecutarla

**Interfaz:** pestaña **Auditoría 2026** → elija año y fecha de corte → **Ejecutar auditoría**.

**Línea de comandos:**

```bash
python auditoria.py --year 2026 --as-of 12/11/2026
```

Genera `auditoria_compras_2026_*.xlsx` y un dashboard HTML.

### Cómo leerla

1. **Veredicto:** empiece por los hallazgos. El sistema no oculta los problemas.
2. **Quiebres y faltantes:** son los SKU que hoy no tienen stock ni tránsito. Priorícelos por venta.
3. **Sobrecompra:** son SKU con más de 180 días de cobertura. Congele la compra y evalúe liquidación o traslado.
4. **Proveedores:** revise concentración (top 5 = 70%) y variación de costo.
5. **Precios y margen:** productos con margen negativo o alza de costo >15%.
6. **Metodología:** umbrales y supuestos, para que el número sea auditable.

### Regla de oro

- Si el SKU está en **QUIEBRE** → comprar o verificar tránsito.
- Si está en **SOBRECOMPRA** → no comprar; revisar stock/tránsito.
- Si está en **SIN VENTA** → no comprar; evaluar salida.
- Si está en **SIN COMPRA 2026** pero vende → verificar si el stock inicial lo cubre.

### Seguridad

La auditoría es **solo lectura**. No modifica facturas, stock ni maestros en Profit.

---

## 8. Filtro de almacenes

### Para qué sirve

Evita que almacenes de recepción, tránsito, vencidos, proyección o ajuste contaminen el stock, los quiebres y la sobrecompra.

### Cómo cambiarlo

1. Abra la interfaz.
2. Vaya a **Configuración**.
3. En **Almacenes para análisis y OC**, marque los almacenes de venta/stock.
4. Deje **Modo de compras** en `Seleccionados + recepción (recomendado)`.
5. Pulse **Guardar almacenes**.

El cambio se guarda en `config/warehouse_selection.json` y aplica a la próxima ejecución de auditoría, órdenes y módulo de ventas.

### Regla importante

- **Ventas y stock** usan solo los almacenes marcados.
- **Compras** incluyen además `REC` (recepción) para conservar el costo real. Si excluyera `REC`, se perdería la mayor parte del historial de compras.
- La hoja **PARÁMETROS** del Excel y el footer del dashboard muestran los almacenes usados.

### Cómo verificar

En el Excel de órdenes, hoja **PARÁMETROS**, busque **“ALMACENES UTILIZADOS (TRAZABILIDAD)”**. Deben aparecer los códigos y nombres.

---

## 9. Módulo de Ventas (BI)

### Abrir

`http://127.0.0.1:5050/ventas`

### Qué muestra

- **Venta neta** en USD y Bs.
- **Margen bruto** y porcentaje.
- **Ticket promedio**, clientes activos y SKU vendidos.
- **Laboratorio #1** del período.
- Serie diaria, top laboratorios, top líneas terapéuticas, top productos y top clientes.
- Dona de participación por laboratorio.
- Heatmap de venta por día de semana y mes.

### Filtros

- Rango de fechas (por defecto 90 días).
- Almacenes (reutiliza la selección del punto 8).
- Laboratorio real: se carga desde `docs/PRODUCTOS POR LABORATORIO.md`.
- Línea terapéutica: clasificación farmacológica separada (ANSIOLITICO, ANTIBIOTICO, etc.).
- Categoría.
- Los filtros se mantienen al navegar dentro del módulo.

> **Importante:** `saLinea` es la línea terapéutica, no el laboratorio. El laboratorio real se obtiene del archivo maestro y se cruza por código; los SKU sin match aparecen como `SIN_LABORATORIO`.

### Drill-down

1. En **Detalle de clientes**, haga clic en un cliente.
2. Verá su ficha, laboratorios comprados, productos comprados y serie mensual.
3. Cierre con **Cerrar** o seleccione otro cliente.

### Exportar

- **Exportar Excel:** genera un libro multi-hoja con KPIs, series, laboratorios, líneas terapéuticas, productos, clientes, `Detalle_Cliente_Laboratorio` y gráficas nativas.
- **Exportar PDF:** captura los gráficos visibles (Chart.js → Base64) y genera un PDF con KPIs, gráficas y el detalle de clientes con sus laboratorios. El backend usa WeasyPrint o, en Windows sin GTK, Edge/Chromium headless.
- **PDF por cliente:** en el drill-down, pulse **Exportar PDF** para descargar la ficha del cliente, los laboratorios comprados y los productos comprados.
- Ambos respetan los filtros activos de fecha, almacén, laboratorio, línea terapéutica y categoría.

> En `Detalle_Cliente_Laboratorio` el laboratorio proviene de la dimensión maestra. `SIN_LABORATORIO` se conserva: no se filtra ni se inventa.

### Notas

- El margen se calcula con el **costo ponderado de compras de los últimos 365 días**. No es un costo contable por factura.
- Si un SKU no tiene compra registrada, su margen puede quedar fuera de la cobertura.
- La primera consulta en frío tarda ~7 s; luego queda cacheada 15 minutos y las siguientes cargas son <1 s.
- Los gráficos usan Chart.js por CDN; si no hay internet, las tablas siguen funcionando.

### Seguridad

El módulo es **solo lectura**. No modifica ventas, clientes, stock ni precios en Profit.

---

## 10. Nuevas reglas de OC

La hoja **ÓRDENES SUGERIDAS** ahora incluye:

- **Stock OC (ACT−COM):** es el inventario que realmente se usa para decidir. El tránsito (`LLE`) no se suma.
- **LLE:** si hay unidades por llegar, la OC se bloquea para no duplicar el pedido. Estado `LLE_PENDIENTE`.
- **Estado EOL / Compra Bloqueada:** productos obsoletos, EOL o en liquidación no generan OC.
- **Precio Ref., Var. Precio %, Precio Techo, Estado Precio:** control de aumentos. Si el precio supera el techo o el margen cae por debajo del mínimo, la OC se bloquea.
- **Aprobación / Aprobador:** indica si la compra requiere autorización.

Hojas nuevas:

- **EOL Y LIQUIDACIÓN:** SKU bloqueados, stock inmovilizado, motivo y acción.
- **CONTROL DE PRECIOS:** referencia, precio actual, variación, impacto y aprobador.

Si un producto aparece en `LLE_PENDIENTE`, **no lo compre de inmediato**: primero valide la orden de compra o la fecha de llegada. Si el tránsito no es real, solicite una excepción.

Si un producto aparece en `EOL Y LIQUIDACIÓN`, **no lo compre**: aplicar liquidación, traslado o devolución.
