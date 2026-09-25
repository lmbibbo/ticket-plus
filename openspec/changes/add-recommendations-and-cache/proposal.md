# Proposal

## Why

La Etapa I define la compra y el catálogo, pero cada cliente debe descubrir eventos por navegación manual y cada lectura repetida vuelve a consultar PostgreSQL y al proveedor externo. La Etapa II agrega recomendaciones personalizadas y una cache compartida para mejorar la experiencia del cliente sin convertir la cache en una fuente de verdad ni degradar las operaciones de compra.

## What Changes

- Se agrega un servicio de recomendaciones para clientes autenticados, integrado mediante un contrato HTTP propio y un adaptador configurable, sin acoplar el dominio a un proveedor concreto.
- El backend envía únicamente un identificador seudónimo del cliente y el contexto mínimo necesario; nunca envía email, credenciales, tokens, datos de pago ni cargas QR al proveedor.
- Se agrega un endpoint de recomendaciones para el cliente actual, con límite de resultados, eventos publicados válidos y una marca de origen `personalized` o `fallback`.
- Se agregan listas de respaldo de eventos populares calculadas localmente para degradar de forma controlada cuando el proveedor externo no responda, devuelva un contrato inválido o exceda el tiempo máximo.
- Se incorpora Redis como cache compartida, usando operaciones cache-aside para recomendaciones, catálogo público y eventos publicados, y con claves versionadas, TTL y límites de tamaño.
- Se invalidan o versionan las entradas afectadas al publicar, editar o retirar eventos y al registrar compras, sin almacenar en cache operaciones de compra, disponibilidad transaccional, sesiones ni códigos QR.
- La interfaz Flask muestra una sección de recomendaciones únicamente a clientes autenticados, con estado de carga, estado vacío y fallback popular sin exponer errores internos del proveedor ni de Redis.
- Se agregan logs estructurados y métricas de hit, miss, invalidación, latencia del proveedor y degradación, correlacionados con el `request_id` de la Etapa I.
- Quedan fuera de este cambio el entrenamiento de modelos, la recomendación en tiempo real, la publicidad, las notificaciones y el check-in.

## Capabilities

### New Capabilities

- `recommendations`: Recomendaciones personalizadas para clientes mediante un contrato de integración propio, con fallback popular, privacidad y comportamiento observable en la API.
- `http-caching`: Cache compartida con Redis para lecturas cacheable, TTL, invalidación por eventos de dominio, resistencia a indisponibilidad y protección de datos.

### Modified Capabilities

Ninguna. La Etapa I todavía no está archivada, por lo que esta etapa no declara deltas sobre capacidades existentes; sus puntos de integración se describen en `design.md` y `tasks.md`.

## Impact

- El backend FastAPI añadirá un adaptador de proveedor, un endpoint de recomendaciones, cálculo de populares y hooks de invalidación sobre publicación, edición, retiro y compra.
- El frontend Flask añadirá la vista de recomendaciones para clientes y el mapeo de estados de carga, vacío y fallback.
- Redis será una dependencia de infraestructura nueva, disponible para lectura y escritura desde el backend; la web seguirá hablando únicamente con FastAPI.
- PostgreSQL seguirá siendo la fuente de verdad para eventos, ventas y datos de dominio. Redis no almacenará credenciales, sesiones, pagos ni códigos QR y podrá vaciarse sin pérdida de información de negocio.
- La configuración de entorno incorporará URL y timeout del proveedor, credencial de servicio, prefijo de claves, TTL y límites de Redis.
- Se añadirán pruebas de contrato del proveedor, integración con Redis, invalidación, degradación, privacidad y observabilidad.

## Rollback Plan

1. Desactivar la función de recomendaciones mediante la bandera de configuración y retirar sus rutas de API y de la web, sin afectar el catálogo ni las compras de la Etapa I.
2. Retirar los hooks de invalidación y volver a leer directamente de PostgreSQL, verificando que el catálogo y el flujo de compra sigan siendo correctos.
3. Eliminar las claves con el prefijo de la Etapa II de Redis; no ejecutar migraciones destructivas ni usar Redis como fuente de verdad.
4. Revocar la credencial de servicio del proveedor externo y detener el tráfico de integraciones antes de revertir el despliegue.
5. Revertir la rama `feature/add-recommendations-and-cache` sin commits ni push directos a `main`, ejecutar las pruebas de humo de la Etapa I y confirmar que no quedan dependencias activas del proveedor o de Redis.
