# Tasks

## 1. Base del proyecto y flujo Git

- [ ] 1.1 Crear o cambiar a la rama `feature/create-virtual-ticketing-platform` y verificar con `git branch --show-current` que no se está trabajando en `main`
- [ ] 1.2 Crear la aplicación Python de backend con FastAPI, Pydantic v2, SQLAlchemy 2 y Alembic, y verificar con `python -c "import fastapi, pydantic, sqlalchemy, alembic"` que sus módulos están disponibles
- [ ] 1.3 Crear la aplicación Python frontend con Flask 3 y Jinja2, preparar el recurso estático de HTMX y verificar con `python -c "import flask, jinja2"` y una prueba de cliente que la aplicación arranca
- [ ] 1.4 Configurar Ruff, mypy y pytest en ambos proyectos, y verificar con `ruff check .`, `mypy backend web` y `pytest --collect-only` que las herramientas descubren el código sin errores
- [ ] 1.5 Configurar una instancia PostgreSQL de pruebas y fixtures de pytest con aislamiento por test, y verificar que una prueba de conexión y una prueba de rollback pasan

## 2. Contratos HTTP y observabilidad

- [ ] 2.1 Implementar en FastAPI el middleware de `X-Request-ID` para generarlo, conservarlo y devolverlo, y verificar con pruebas HTTP que un identificador válido se preserva y uno ausente se genera
- [ ] 2.2 Implementar logs JSON del backend con los campos permitidos de método, ruta, estado, duración, correlación y usuario, y verificar capturando logs que nunca contengan contraseñas, cookies, tokens, pagos ni cargas QR
- [ ] 2.3 Implementar errores `application/problem+json` con códigos de negocio estables y validación Pydantic v2, y verificar mediante pruebas de API para 4xx y 5xx que el esquema es consistente
- [ ] 2.4 Implementar en Flask el mismo identificador de petición, la propagación a FastAPI y los registros JSON equivalentes, y verificar con una prueba que ambos saltos comparten `request_id`
- [ ] 2.5 Implementar el cliente web agrupado hacia FastAPI, el mapeo de `problem+json` a formularios o fragmentos y el timeout recuperable, y verificar con pruebas del cliente para éxito, 4xx, 5xx y timeout

## 3. Persistencia e identidad y acceso

- [ ] 3.1 Configurar el motor SQLAlchemy 2, la sesión por petición y la conexión transaccional a PostgreSQL, y verificar con una prueba de commit y rollback que el estado de la base no se filtra entre requests
- [ ] 3.2 Implementar `users` y `auth_sessions` con email normalizado único, roles, estados, hash de refresh token, expiración, rotación y revocación, y verificar con pruebas de restricciones e índices
- [ ] 3.3 Crear la revisión inicial de Alembic para identidad y verificar con `alembic upgrade head` y `alembic downgrade base` sobre una base PostgreSQL vacía
- [ ] 3.4 Implementar registro público, normalización de email y hash Argon2id, y verificar con pruebas que un cliente queda activo, un proveedor queda pendiente y un rol no permitido se rechaza
- [ ] 3.5 Implementar JWT Ed25519 de corta duración y refresh tokens opacos almacenados como hash con rotación y detección de reutilización, y verificar con pruebas de expiración, renovación, revocación y reutilización
- [ ] 3.6 Exponer login, refresh y logout bajo `/api/v1/auth`, y verificar mediante pruebas de contrato que solo las cuentas activas emiten tokens y que logout elimina la sesión
- [ ] 3.7 Implementar dependencias FastAPI para `admin`, `event-owner` y `client` y ownership de recursos, y verificar con pruebas matriz que cada rol y acceso entre recursos quedan permitidos o denegados correctamente
- [ ] 3.8 Implementar administración de usuarios para aprobar proveedores y suspender cuentas con revocación inmediata de sesiones, y verificar con pruebas API que las operaciones-no administrativas no cambian estado
- [ ] 3.9 Ejecutar la suite de identidad contra PostgreSQL y verificar que cubre registro, aprobación, autenticación, renovación, revocación, suspensión y autorización sin usar SQLite

## 4. Gestión de eventos

- [ ] 4.1 Implementar modelos, índices y restricciones para `events`, `zones` y `zone_sequences`, y verificar con pruebas de migración y de integridad que un evento no puede repetir zona y una zona tiene una sola secuencia
- [ ] 4.2 Implementar creación, edición, publicación y retiro de eventos con validación de datos y ownership, y verificar con pruebas de API los estados `draft`, `published` y `retired`
- [ ] 4.3 Implementar administración de zonas con capacidad y precio decimal no negativo, y verificar que una zona con entradas asignadas no puede eliminarse
- [ ] 4.4 Implementar el cálculo de disponibilidad por zona sin exponer inventario interno, y verificar con pruebas de casos límite que la capacidad nunca se excede
- [ ] 4.5 Implementar el catálogo y detalle público de eventos bajo `/api/v1/events`, y verificar que solo devuelve eventos publicados con zonas, precios y disponibilidad válidos
- [ ] 4.6 Ejecutar las pruebas de eventos contra PostgreSQL y verificar los flujos de proveedor, administrador y visitante sin divergencias entre permisos de API y ownership

## 5. Ventas, concurrencia y entradas

- [ ] 5.1 Implementar modelos, índices y restricciones para `orders` y `tickets`, incluida la unicidad `(client_id, idempotency_key)` y `(zone_id, number)`, y verificar las restricciones con pruebas de PostgreSQL
- [ ] 5.2 Implementar creación de pedido y proveedor de pago simulado con resultados aprobado y rechazado, y verificar mediante pruebas que un rechazo conserva el pedido sin activar y no asigna tickets
- [ ] 5.3 Implementar idempotencia mediante clave, hash canónico de la solicitud y restricción única, y verificar que un reintento idéntico devuelve el resultado existente y una clave reutilizada con otros datos devuelve conflicto
- [ ] 5.4 Implementar la venta atómica bloqueando zonas en orden determinista y comprobando capacidad antes de insertar, y verificar que un fallo de una zona revierte todo el pedido sin asignaciones parciales
- [ ] 5.5 Implementar `zone_sequences` para asignar números consecutivos desde 1 por zona sin reutilizarlos, y verificar con pruebas de primera compra, compra posterior y zona independiente
- [ ] 5.6 Emitir entradas virtuales con evento, zona, número, propietario, nonce y payload QR firmado, y verificar que el payload se entrega al propietario y no se registra en texto claro
- [ ] 5.7 Implementar listado y detalle de entradas con ownership de cliente y autorización administrativa, y verificar que un cliente no puede leer una entrada ajena
- [ ] 5.8 Implementar pruebas de concurrencia con dos compras compitiendo por la última capacidad y ejecutar `pytest -m concurrency` contra PostgreSQL, verificando que solo una venta completa y no hay números duplicados

## 6. Base web, sesiones y acceso

- [ ] 6.1 Configurar Flask con Blueprints, plantillas Jinja2, HTMX y un almacén de sesiones del lado servidor aislado del esquema de dominio, y verificar que el navegador solo recibe un identificador opaco en cookie `HttpOnly`, `Secure` y `SameSite`
- [ ] 6.2 Implementar layout base, navegación por rol, cierre de sesión y protección de vistas, y verificar con pruebas Flask que cada rol ve sus enlaces y una vista ajena devuelve acceso denegado
- [ ] 6.3 Implementar formularios web de registro, login, logout y renovación de sesión mediante la API, y verificar con pruebas de navegador que un cliente accede, un proveedor pendiente no publica y logout elimina las credenciales locales
- [ ] 6.4 Implementar tokens CSRF y validación de formularios en servidor para operaciones de cambio de estado, y verificar que una solicitud sin token o con datos inválidos no modifica recursos
- [ ] 6.5 Ejecutar la suite web base con `pytest web`, verificando cookies, sesión, CSRF, navegación por rol y representación de errores sin exponer respuestas internas

## 7. Flujos web de dominio

- [ ] 7.1 Implementar catálogo y detalle público en Flask mediante la API, y verificar con pruebas Jinja que solo aparecen eventos publicados y sus datos públicos
- [ ] 7.2 Implementar la vista de administración de usuarios para aprobar proveedores y suspender cuentas, y verificar que las acciones exigen rol `admin` y reflejan el estado devuelto por la API
- [ ] 7.3 Implementar vistas de proveedor para crear, editar, publicar y retirar sus eventos y zonas, y verificar que un proveedor no puede operar sobre eventos ajenos
- [ ] 7.4 Implementar el flujo de compra de cliente con selección de cantidades, `Idempotency-Key` y representación del resultado, y verificar que un reintento no crea entradas adicionales
- [ ] 7.5 Implementar la vista privada de entradas con números y códigos QR, y verificar que un cliente solo ve las propias y que el QR no se imprime en logs
- [ ] 7.6 Implementar fragmentos HTMX para paginación, actualización de listas y errores recuperables, y verificar que las respuestas parciales conservan sesión y no incluyen HTML ajeno
- [ ] 7.7 Verificar navegación por teclado, etiquetas y semántica en las vistas principales, y ejecutar las pruebas de accesibilidad automatizadas disponibles sin errores

## 8. Integración y operación

- [ ] 8.1 Levantar FastAPI, Flask y PostgreSQL en un entorno de integración y verificar con pruebas de humo que las rutas públicas, autenticadas y administrativas responden desde un mismo origen
- [ ] 8.2 Ejecutar un flujo completo de registro, aprobación, publicación, compra y entrega de entradas, y verificar la respuesta de cada salto con el mismo `request_id` y sin secretos en la salida
- [ ] 8.3 Aplicar `alembic upgrade head` en la base de staging y ejecutar `pytest` completo, verificando que la suite pasa con el esquema final y que no requiere SQLite
- [ ] 8.4 Ensayar en un entorno descartable el plan de rollback de `proposal.md`, incluyendo detener tráfico, revocar sesiones y claves, y comprobar health checks antes de limpiar los datos de prueba
