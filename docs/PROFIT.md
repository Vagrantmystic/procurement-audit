# IntegraciÃ³n con Profit 2K12

## ConexiÃ³n

| ParÃ¡metro | Valor |
|-----------|-------|
| Motor | Microsoft SQL Server 2019 Express |
| Driver | ODBC Driver 17 for SQL Server |
| Base | `EMPRESA_A` |
| Modo | **solo lectura** |
| Archivo local | `config/database.local.yaml` (excluido de git) |

La conexiÃ³n se puede configurar por archivo o por variables de entorno:

```bash
PROFIT_DB_SERVER=...
PROFIT_DB_NAME=EMPRESA_A
PROFIT_DB_USER=profit
PROFIT_DB_PASSWORD=...
PROFIT_DB_DRIVER="{ODBC Driver 17 for SQL Server}"
```

> La contraseÃ±a nunca se imprime, no se envÃ­a al navegador y no se registra en los logs.

## Reglas de solo lectura

`ProfitConnection` rechaza cualquier sentencia que no empiece por `SELECT` o `WITH` y bloquea palabras como `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `EXEC`, `MERGE`, `TRUNCATE`, `GRANT`. Tampoco permite mÃºltiples sentencias en una consulta. La conexiÃ³n se abre con `autocommit` y `ApplicationIntent=ReadOnly`.

## Esquema utilizado

| Dato | Tabla / vista | Campos clave |
|------|---------------|--------------|
| Cabecera de compra | `saFacturaCompra` | `doc_num`, `fec_emis`, `co_prov`, `co_mone`, `tasa`, `total_bruto`, `total_neto`, `anulado`, `co_us_in`, `co_sucu_in` |
| RenglÃ³n de compra | `saFacturaCompraReng` | `doc_num`, `reng_num`, `co_art`, `total_art`, `cost_unit`, `reng_neto`, `monto_desc`, `co_uni`, `co_alma` |
| Cabecera de venta | `saFacturaVenta` | `doc_num`, `fec_emis`, `co_cli`, `co_mone`, `tasa`, `total_bruto`, `anulado` |
| RenglÃ³n de venta | `saFacturaVentaReng` | `doc_num`, `reng_num`, `co_art`, `total_art`, `prec_vta`, `reng_neto` |
| ArtÃ­culos | `saArticulo` | `co_art`, `art_des`, `tipo`, `co_lin`, `co_subl`, `co_cat`, `stock_min`, `stock_max` |
| LÃ­nea / sublÃ­nea / categorÃ­a | `saLineaArticulo`, `saSubLinea`, `saCatArticulo` | descripciones |
| Proveedores | `saProveedor` | `co_prov`, `prov_des`, `inactivo` |
| Proveedor por artÃ­culo | `saArtProveedorReng` | `co_art`, `co_prov` |
| Stock | `saStockAlmacen` | `co_art`, `co_alma`, `tipo`, `stock` |
| Almacenes | `saAlmacen` | `co_alma`, `des_alma`, `noventa`, `nocompra`, `alm_temp` |
| Devoluciones | `saDevolucionProveedor` + `...Reng` | devoluciones a proveedor |

### Tipos de stock

`saStockAlmacen` devuelve tres tipos por artÃ­culo y almacÃ©n:

- `ACT`: stock fÃ­sico actual.
- `LLE`: unidades por llegar (trÃ¡nsito / pedidos pendientes).
- `COM`: comprometido.

El sistema calcula:

```text
Stock_Disponible = ACT âˆ’ COM + LLE     # reporte histÃ³rico
Inventario_OC    = max(0, ACT âˆ’ COM)   # decisiÃ³n de compra
```

El `LLE` no se suma para la OC: se usa como bandera de pedido pendiente para evitar duplicidad. Para la auditorÃ­a se reporta el stock fÃ­sico (`ACT`) y el trÃ¡nsito (`LLE`) por separado, porque el trÃ¡nsito puede estar sobreestimado o corresponder a Ã³rdenes antiguas.

## Filtro por almacÃ©n

`saAlmacen` **no tiene columna `activo`**. Tiene banderas `noventa`, `nocompra`, `materiales`, `produccion` y `alm_temp`, pero la decisiÃ³n operativa se toma con una selecciÃ³n explÃ­cita:

- `selected`: almacenes de venta/stock (por defecto `CCS`, `ZUL`, `CONCCS`, `CONZUL`).
- `purchase_extra`: almacÃ©n de recepciÃ³n `REC`, que se incluye en compras para conservar el costo real.
- `purchase_mode`: `selected_plus_extra` (recomendado) o `selected_only`.

`v_saStockActual` **no tiene `co_alma`**, por lo que no sirve para filtrar stock. El sistema usa `saStockAlmacen` con `WHERE co_alma IN (...)`.

La selecciÃ³n se guarda en `config/warehouse_selection.json` (no versionado) y se muestra en la hoja `PARÃMETROS` y en los dashboards. Ventas y compras se filtran por `saFacturaVentaReng.co_alma` y `saFacturaCompraReng.co_alma`, respectivamente.

## Laboratorio real vs. lÃ­nea terapÃ©utica

`saLinea` (y `saLineaArticulo.lin_des`) es la **lÃ­nea terapÃ©utica**: ANSIOLITICO, ANTIBIOTICO, ANALGESICO, etc. **No es el laboratorio.**

El laboratorio real se toma de `docs/PRODUCTOS POR LABORATORIO.md`, se normaliza el cÃ³digo (dÃ­gitos, sin puntos/guiones) y se cruza con `saArticulo.co_art` / `EAN`. Los SKU sin match se agrupan en `SIN_LABORATORIO` para no perder venta. La cobertura del cruce se reporta en la respuesta de `/ventas/api/resumen` y en el log.

## ConversiÃ³n a USD

Profit guarda los importes en **bolÃ­vares** y la tasa Bs/USD en la cabecera (`tasa`). Para documentos `co_mone = 'USD'`:

```text
Importe_USD = reng_neto / tasa_documento
```

Para documentos `co_mone = 'BS'` (tasa = 1), se usa la tasa mensual de referencia (promedio de documentos USD del mes). En 2026 solo una minorÃ­a de lÃ­neas de compra venÃ­an en BS; su impacto en el total es marginal.

El costo unitario es:

```text
Costo_Unit_USD = Importe_USD / total_art
```

## Unidades

En 2026 se verificÃ³ que, para todos los artÃ­culos con compra y venta, la unidad de compra coincide con la de venta (`co_uni`). Por eso las cantidades son comparables sin factores de conversiÃ³n. Si en el futuro aparecen artÃ­culos comprados en caja y vendidos en unidad, habrÃ¡ que usar `saArtUnidad.equivalencia`.

## ExtracciÃ³n

`ProfitLoader` devuelve DataFrames normalizados:

- `purchases`: `DocNum`, `Fecha`, `Proveedor`, `Comprador`, `EAN`, `Descripcion`, `Cantidad`, `CostoUnitario`, `Importe`, `Importe_USD`, lÃ­nea, sublinea, categorÃ­a, etc.
- `sales`: equivalente con cliente y precio.
- `products`, `suppliers`, `stock`, `product_supplier`, `returns`, `rates`.

El histÃ³rico por defecto para Ã³rdenes es de 3 aÃ±os; para la auditorÃ­a se extrae el aÃ±o auditado y el anterior (para variaciÃ³n de precios).

## Rendimiento

- ExtracciÃ³n de 3 aÃ±os: ~30 s.
- AuditorÃ­a + Excel + dashboard: ~35 s.
- Ã“rdenes desde Profit: ~55 s.
- ML/DL: ~10 s.

## Limitaciones

- No se calcula lead time real desde orden de compra a recepciÃ³n; se usa el configurado.
- No hay historial de quiebres; el sistema detecta ventas con stock cero, no la causa.
- El trÃ¡nsito (`LLE`) no se suma a la OC; por defecto bloquea la emisiÃ³n hasta validar la orden/ETA. Validar con compras.
- Los artÃ­culos tipo servicio (`tipo = 'S'`) se excluyen de la auditorÃ­a de mercancÃ­a.
- La auditorÃ­a es de solo lectura: no modifica nada en Profit.
