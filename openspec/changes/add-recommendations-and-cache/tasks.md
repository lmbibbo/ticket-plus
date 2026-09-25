# Tasks

## 1. Preparación e infraestructura

- [ ] 1.1 Crear la rama `feature/add-recommendations-and-cache` desde el estado actual y verificar con `git branch --show-current` que la rama activa es la esperada
- [ ] 1.2 Añadir la dependencia cliente de Redis y el doble de pruebas, y verificar con `python -c "import redis"` y la instalación de dependencias del proyecto
- [ ] 1.3 Definir en la configuración de la aplicación las variables de Redis, del proveedor, del secreto HMAC, del prefijo de claves y los TTL por clase, y verificar con una prueba que los valores por defecto coinciden con el diseño
- [ ] 1.4 Implementar el cliente Redis asíncrono con timeouts cortos, prefijo de entorno y cierre controlado, y verificar con una prueba que un Redis inaccesible produce una excepción tipada de caché no disponible
- [ ] 1.5 Configurar una instancia de Redis para las pruebas de integración y un doble en memoria para las pruebas unitarias, y verificar que la suite arranca con la variable de entorno documentada
- [ ] 1.6 Documentar en `README.md` las variables de entorno, el requisito de Redis y el flujo de reversión, y verificar que cada variable documentada existe en la configuración

## 2. Capa de caché reutilizable

- [ ] 2.1 Implementar la lectura y escritura genérica de entradas con TTL y límite de bytes por entrada, y verificar con pruebas unitarias de escritura, lectura, expiración y rechazo de valores demasiado grandes
- [ ] 2.2 Implementar el versionado de claves con un contador de versión y la construcción de nombres de clave para catálogo, populares, evento y recomendaciones, y verificar con pruebas que una versión nueva deja de servir la entrada anterior
- [ ] 2.3 Implementar el patrón cache-aside para que un acierto evite consultar el origen y un fallo consulte la fuente de verdad, y verificar con un origen instrumentado que no se invoca en el acierto y sí en el fallo
- [ ] 2.4 Implementar la degradación por caché no disponible que devuelve el valor del origen y registra el evento, y verificar con una prueba que la respuesta es correcta y se emite la métrica de degradación
- [ ] 2.5 Implementar el lock de recarga con token y espera breve para limitar la carga del origen ante una tormenta de fallos de caché, y verificar con una prueba concurrente que el origen recibe una sola llamada para la misma clave
- [ ] 2.6 Instrumentar la capa de caché con eventos estructurados y contadores de acierto, fallo, invalidación y latencia correlacionados con el `request_id`, y verificar con una prueba de captura de logs que no aparecen datos personales

## 3. Consultas de dominio e invalidación

- [ ] 3.1 Marcar como cacheables las lecturas de catálogo público, metadatos de evento publicado y lista de populares, y verificar con pruebas que cada una guarda y reutiliza su entrada
- [ ] 3.2 Implementar la invalidación posterior al commit al publicar, editar o retirar un evento, y verificar con pruebas que las lecturas posteriores ya no devuelven el valor anterior
- [ ] 3.3 Implementar la invalidación de listas de populares y referencias de recomendaciones al completar una compra, y verificar con una prueba de compra que la lista cacheada se descarta tras el commit
- [ ] 3.4 Verificar que la confirmación de compra sigue validando disponibilidad en la transacción aunque exista una lectura cacheada, y confirmar con una prueba de compra concurrente que nunca se venden unidades de más
- [ ] 3.5 Confirmar que las rutas de compra, pago, entradas, códigos QR y comprobaciones de disponibilidad transaccional no leen de la caché, y verificar con una prueba que fuerce la caché vacía y que el origen siga siendo consultado
- [ ] 3.6 Añadir la reconciliación que limpia el prefijo de claves al recuperar la disponibilidad de Redis, y verificar con una prueba que las entradas antiguas se eliminan tras simular una caída y recuperación

## 4. Adaptador del proveedor de recomendaciones

- [ ] 4.1 Definir los modelos Pydantic v2 de solicitud y respuesta del contrato propio con `subject_id`, contexto mínimo, `limit`, `event_id`, `score` y `reason`, y verificar con pruebas de validación de entradas y salidas válidas e inválidas
- [ ] 4.2 Implementar el cálculo del identificador seudónimo estable con HMAC-SHA256 y versión del secreto, y verificar con pruebas que el valor es estable, cambia al rotar el secreto y no contiene el identificador original
- [ ] 4.3 Implementar el cliente HTTP del proveedor con credencial de servicio, HTTPS, timeout máximo y un reintento acotado, y verificar con un doble de prueba que respeta el timeout y no reintenta más veces de lo permitido
- [ ] 4.4 Construir el contexto mínimo de la solicitud a partir del historial de compras y verificar con una prueba que no incluye email, nombre, tokens, pagos, sesiones ni códigos QR
- [ ] 4.5 Implementar el filtro de la respuesta que descarta eventos inexistentes, no publicados, duplicados o sin disponibilidad conservando el orden válido, y verificar con pruebas que la respuesta entregada cumple el contrato
- [ ] 4.6 Proveer un doble local de proveedor con el mismo contrato para desarrollo y pruebas, y verificar que responde con resultados válidos y con un payload inválido de forma controlada

## 5. Servicio de recomendaciones y API

- [ ] 5.1 Implementar el cálculo local de eventos populares sobre eventos publicados con unidades disponibles, ordenados por entradas vendidas con desempate determinista, y verificar con una prueba de datos que el orden es el esperado
- [ ] 5.2 Implementar el servicio de recomendaciones que resuelve caché, proveedor y fallback popular, y verificar con pruebas para acierto de caché, respuesta válida del proveedor, fallo del proveedor y ausencia de populares
- [ ] 5.3 Exponer `GET /api/v1/clients/me/recommendations` con límite validado entre uno y el máximo configurado, y verificar con pruebas que un límite inválido devuelve error de validación sin llamar al proveedor
- [ ] 5.4 Restringir la ruta a clientes activos y denegar visitantes y otros roles con un mensaje seguro, y verificar con pruebas de permisos que ninguna respuesta revela preferencias de otro cliente
- [ ] 5.5 Añadir la respuesta con `items`, `source` y `request_id`, y verificar con pruebas que `source` distingue `personalized` de `fallback` y que el estado vacío se devuelve como lista vacía sin error

## 6. Web de cliente

- [ ] 6.1 Añadir a la vista privada de Flask la sección de recomendaciones visible solo para clientes, y verificar con pruebas de plantilla que un visitante, proveedor o administrador no recibe la sección
- [ ] 6.2 Añadir el fragmento HTMX de carga asíncrona de recomendaciones, y verificar con una prueba de cliente que la petición parcial devuelve solo el contenido de la sección
- [ ] 6.3 Implementar los estados de carga, vacío, personalizada y fallback con su etiqueta de origen, y verificar con pruebas de renderizado que cada estado muestra el mensaje esperado y ninguna tarjeta cuando corresponde
- [ ] 6.4 Verificar que la web nunca muestra un error técnico del proveedor ni datos de otro cliente, y confirmar con una prueba que el fallback se etiqueta como populares

## 7. Verificación de integración

- [ ] 7.1 Ejecutar la suite completa de pruebas en orden y verificar que todas las pruebas de caché, proveedor, API y web pasan de forma aislada y en conjunto
- [ ] 7.2 Ejecutar una prueba de extremo a extremo con Redis y el doble de proveedor que recorra caché, invalidación y fallback, y verificar que las métricas de acierto, invalidación y degradación aparecen correlacionadas por `request_id`
- [ ] 7.3 Verificar la reversión documentada desactivando la bandera, revocando la credencial del proveedor y limpiando el prefijo de claves, y confirmar que la compra y el catálogo de la Etapa I siguen funcionando
