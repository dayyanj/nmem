# Benchmark de Memoria de Agentes Múltiples en Salud

<!-- i18n:start -->
[English](../../benchmarks/healthcare-multi-agent.md) | [简体中文](../zh-hans/healthcare-multi-agent.md) | [日本語](../ja/healthcare-multi-agent.md) | [한국어](../ko/healthcare-multi-agent.md) | **Español** | [Português](../pt/healthcare-multi-agent.md) | [Français](../fr/healthcare-multi-agent.md) | [Deutsch](../de/healthcare-multi-agent.md) | [Русский](../ru/healthcare-multi-agent.md)
<!-- i18n:end -->


## Resumen Ejecutivo

Construimos una simulación de 180 días de una unidad hospitalaria con 4 agentes de IA (triage, tratamiento, alta y farmacia) que procesan 1,705 encuentros clínicos sintéticos de 200 pacientes. Cada agente escribe en el diario de nmem, y el sistema ejecuta consolidación diaria, dreamstate semanal y síntesis nocturna quincenal — comprimiendo 6 meses de operaciones clínicas en una ejecución de benchmark de 4,6 horas.

El benchmark prueba si la memoria cognitiva mejora las respuestas de los agentes con el tiempo en comparación con una línea base con el mismo LLM pero sin consolidación de memoria.

### Hallazgos Principales

1. **La revisión de creencias es la característica destacada de nmem.** Después de cambios en las guías, nmem obtiene 5.00/5 frente a la línea base 3.13/5 (+60%) — respuestas perfectas en todas las preguntas de revisión de creencias desde el día 120 en adelante.
2. **Mejora general con un modelo de 14B en hardware de consumo.** nmem obtiene 3.84/5 frente a la línea base 3.60/5 (+7%) en 205 evaluaciones, usando Qwen3-14B en una sola tarjeta gráfica RTX 4090.
3. **nmem gana 77 preguntas, pierde 47, empata 81.** Las victorias se concentran en la revisión de creencias (+1.54 promedio) y la recuperación directa (+0.36 promedio). Las pérdidas se concentran en el razonamiento temporal (-0.39 promedio), donde la capacidad limitada de razonamiento del modelo de 14B es probablemente el cuello de botella.
4. **La brecha se amplía con el tiempo.** Día 1: +0.20, Día 30: +0.29, Día 120: +0.43 — a medida que cambian más guías y se consolida el conocimiento, la ventaja de nmem crece.
5. **Todos los elementos se ejercen a gran escala.** 361 promociones de LTM, 56 fusiones de duplicados, 1,170 conflictos resueltos automáticamente, 11 patrones de dreamstate sintetizados, 6,171 enlaces de conocimiento — en un presupuesto de inferencia de $0 (vLLM local).

---

## Metodología

### Preguntas de Investigación

1. ¿Mejora la memoria cognitiva el conocimiento clínico de agentes múltiples durante 6 meses?
2. ¿nmem revisa correctamente las creencias cuando cambian las guías?
3. ¿El motor de consolidación (promoción, deduplicación, síntesis) produce recuperación mejorable?
4. ¿Este trabajo funciona en infraestructura modesta (modelo de 14B, una GPU)?

### Diseño Experimental

**Fuente de datos:** [Synthea](https://synthetichealth.github.io/synthea/) generador de pacientes sintéticos (Apache 2.0). Se generaron 1,159 pacientes, se seleccionaron los 200 con mayor densidad de encuentros para el período de simulación.

**Ventana de simulación:** 180 días (2025-01-01 a 2025-06-29)

**Agentes:**

| Agente | Rol | Procesos |
|-------|------|-----------|
| triage | Evaluación de urgencia | Todos los encuentros — signos vitales, queja principal, prioridad |
| tratamiento | Decisiones clínicas | Diagnósticos, procedimientos, resultados de laboratorio |
| discharge | Coordinación de cuidados | Planes de cuidados, seguimientos, seguimiento de readmisiones |
| pharmacy | Gestión de medicamentos | Prescripciones, alergias, interacciones de medicamentos |

**Escenarios inyectados** (eventos elaborados a mano que Synthea no puede generar):

| Día | Evento | Función Probada |
|-----|-------|----------------|
| 30 | Umbral de hipertensión reducido de 140/90 → 130/80 | Revisión de creencias |
| 45 | Recuerdo de metformina ER (contaminación NDMA) | Propagación entre agentes |
| 50 | Paciente diabético readmitido dentro de 30 días | Detección de patrones de readmisión |
| 75 | Terapia de segunda línea para diabetes: sulfonylureas → GLP-1 | Revisión de creencias |
| 90 | Prescripción de Bactrim a paciente con alergia a sulfa | Resolución de conflictos |
| 110 | Interacción Lisinopril+potasio: MODERADA → SEVERA | Revisión de creencias |
| 120 | Prescripción inicial de opioides: 7 días → 3 días | Cambio de política |
| 130 | Patrón de readmisión de viernes tercero | Detección de patrones |

**Ciclo diario de simulación:**