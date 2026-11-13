# Modelos de demanda: clásico, machine learning y red neuronal

## Objetivo

Evaluar si un modelo **global** —que aprende de todos los productos a la vez— mejora el pronóstico de demanda frente al enfoque clásico por producto (media móvil, suavizado, Croston, TSB) en el portafolio real de Droguería Emmanuelle.

## Datos y unidad de análisis

- Ventas de Profit 2023–2026 (artículos de mercancía).
- Panel semanal producto × semana (semana terminada en domingo).
- Objetivo: **demanda de las próximas 4 semanas** (≈30 días).
- Validación temporal: últimos **6 meses**; entrenamiento con todo lo anterior (con purga de 4 semanas para evitar fuga de información).

## Variables

| Grupo | Variables |
|-------|-----------|
| Rezagos | `lag1`, `lag2`, `lag4`, `lag8`, `lag13`, `lag26` |
| Nivel y tendencia | medias móviles 4/8/13/26, `trend` (MA4/MA13) |
| Intermitencia | `zero13`, `zero_run`, desviación 8 semanas |
| Estacionalidad | seno/coseno de semana del año, mes |
| Producto | media, desviación, CV, zero-share, precio, línea, categoría |

## Modelos

1. **Baselines:** media móvil 4 y 8 semanas proyectada a 4 semanas.
2. **Machine learning:** `HistGradientBoostingRegressor` (250 iteraciones, profundidad 6, regularización L2).
3. **Aprendizaje profundo:** `MLPRegressor` con dos capas ocultas (64, 32), ReLU, Adam, early stopping y calibración de sesgo.
4. **Híbrido:** red neuronal para SKU con volumen > 10 u/mes; media móvil 8 para la cola de baja rotación.

## Resultados (últimos 6 meses)

| Modelo | WAPE | Sesgo | Hit ±25% | MAE |
|--------|------|-------|----------|-----|
| MA4 × 4 | 101,0% | −2,8% | 14,8% | 5,46 |
| MA8 × 4 | 97,0% | −2,9% | 16,8% | 5,25 |
| Gradient Boosting | 116,0% | +5,4% | 18,6% | 6,28 |
| Red neuronal (MLP) | 101,9% | −9,2% | 18,1% | 5,52 |
| **Híbrido (MLP + MA8)** | **96,2%** | −4,4% | 17,4% | 5,21 |

### Desempeño por volumen

| Tramo (u/mes) | SKU | WAPE ML | WAPE DL | WAPE MA4 | WAPE MA8 | WAPE híbrido |
|---------------|-----|---------|---------|----------|----------|--------------|
| < 10 | 534 | 154,4% | 145,9% | 134,7% | 132,5% | 140,2% |
| 10–25 | 235 | 82,5% | 82,5% | 95,5% | 88,6% | 88,1% |
| 25–50 | 71 | 84,0% | 82,5% | 101,5% | 93,8% | 90,9% |
| 50–100 | 37 | 78,8% | 69,9% | 84,4% | 78,4% | 71,4% |
| 100–250 | 21 | 83,5% | 77,1% | 84,2% | 74,0% | 67,0% |
| > 250 | 4 | 60,2% | 49,1% | 60,3% | 52,0% | 49,1% |

### Variables más relevantes

| Variable | Importancia (permutación) |
|----------|---------------------------|
| `prod_mean` | 0,855 |
| `mean4` | 0,798 |
| `zero_run` | 0,576 |
| `prod_cv` | 0,455 |
| `prod_zero` | 0,444 |
| `prod_std` | 0,442 |
| `mean8` | 0,160 |
| `std8` | 0,149 |

## Conclusión

**El ML/DL no supera de forma material a una media móvil en este portafolio.**

- El híbrido mejora **0,8 puntos de WAPE** frente a MA8 (96,2% vs 97,0%), con mayor sesgo negativo (−4,4% vs −2,9%).
- La red neuronal sola es peor que MA8 (101,9%).
- Gradient Boosting es el peor (116,0%): sobreajusta la cola de baja rotación.
- En SKU de volumen alto (>100 u/mes) el híbrido sí mejora 5–7 puntos; en la cola <10 u/mes empeora.

**Por eso el motor clásico se mantiene como predeterminado** y el ML se ofrece como alternativa monitoreada, no como reemplazo. La mayor ganancia no está en cambiar de algoritmo sino en mejorar los datos: quiebres, lead times y stock real.

## Cuándo usar cada motor

| Situación | Motor recomendado |
|-----------|-------------------|
| SKU de alto volumen (>100 u/mes) | Híbrido MLP |
| SKU de volumen medio (25–100) | Híbrido o clásico |
| SKU de baja rotación (<10) | Clásico (MA8/Croston) |
| Producto nuevo (<6 meses) | Reglas / promedio |
| Portafolio completo | Clásico |

## Próximos pasos para mejorar el ML

1. **Etiquetar quiebres:** si `stock = 0`, la venta es censurada; el modelo debe aprender demanda latente, no venta observada.
2. **Lead time real** por proveedor como variable.
3. **Promociones y precios** (elasticidad).
4. **Embeddings de producto** (categoría/molecula) en una red más profunda.
5. **Modelo jerárquico:** pronóstico por molécula/categoría y desagregación por SKU.
6. **Reentrenamiento mensual** y monitoreo de deriva.

## Reproducir

Desde la interfaz web:

1. Abra `http://127.0.0.1:5050`.
2. Vaya a **Modelos ML / DL**.
3. Pulse **Entrenar y comparar**.

O desde Python:

```python
from data_processing.profit_connection import ProfitConnection
from data_processing.profit_loader import ProfitLoader
from models.global_ml import run_experiment

data = ProfitLoader(ProfitConnection()).load_all("2023-01-01", "2026-12-31")
result = run_experiment(data)
print(result.metrics)
```
