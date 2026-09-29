# Tasks

Plan de ejecución por fases. Cada fase termina en un comportamiento verificable,
no en un porcentaje de tareas completadas. Las fases se ejecutan en orden y cada
una es un punto de corte conible: al cerrarla el sistema corre y se puede
demostrar lo que hace.

Los identificadores de tarea (`1.1`, `2.3`, ...) son los del plan por secciones
original y no cambian, para que proposal, design y specs sigan siendo
trazables.

## Criterio de orden

Se adelanta solo lo que es caro de retrospectar y se aplaza lo que es barato de
meter despues.

| Decisión | Por que se fija temprano |
|---|---|
| `request_id` en ambos saltos (2.1, 2.4) | retrofit = tocar cada linea de log y cada prueba |
| `application/problem+json` (2.3) | retrofit = cambiar cada respuesta y cada plantilla |
| JWT con sesion persistida (3.5) | retrofit = invalida todos los tokens vigentes |
| Almacen de sesion en servidor (6.1) | retrofit = rediseñar el contrato de cookie |

El esquema de `orders` y `tickets` (5.1) tambien adelanta, porque la restriccion
de no borrar una zona con entradas (4.3) solo es verificable si la tabla existe.
Lo que se aplaza es el bloqueo por zona (5.4): la tabla `zone_sequences` no depende
de la estrategia de bloqueo, asi que equivocarse en ella cuesta un revert de
codigo y no una migracion de datos.

## Mapa seccion original -> fase

| Seccion | Fase |
|---|---|
| 1. Base del proyecto y flujo Git | F0 (1.1), F1 (1.2-1.5) |
| - Preparacion de entorno (nuevas) | F0 (F0.1-F0.4) |
| 2. Contratos HTTP y observabilidad | F1 (2.1-2.4), F2 (2.5), F6 - |
| 3. Persistencia e identidad y acceso | F2 (3.1-3.7, 3.9), F3 (3.8) |
| 4. Gestion de eventos | F3 (4.1-4.3), F4 (4.4-4.6) |
| 5. Ventas, concurrencia y entradas | F3 (5.1), F4 (5.2, 5.3, 5.5-5.7), F5 (5.4, 5.8) |
| 6. Base web, sesiones y acceso | F2 (6.1-6.3), F3 (6.4), F4 (6.5) |
| 7. Flujos web de dominio | F3 (7.2, 7.3), F4 (7.1, 7.4, 7.5), F6 (7.6, 7.7) |
| 8. Integracion y operacion | F6 (8.1-8.4) |

---

## F0. Desbloqueo

Sin codigo de aplicacion. Prepara el terreno: sin la CLI de openspec y sin
PostgreSQL ninguna otra tarea puede empezar.

- [x] F0.1 Instalar la CLI con `npm install -g @fission-ai/openspec@latest` y verificar con `openspec --version`
- [x] F0.2 Confirmar con `openspec list --json` que el proyecto resuelve la raiz `openspec/` y que aparecen `create-virtual-ticketing-platform` y `add-recommendations-and-cache`
- [x] F0.3 Ejecutar `openspec status --change create-virtual-ticketing-platform --json` y comprobar que los cuatro artefactos estan completos
- [x] F0.4 Levantar PostgreSQL de desarrollo y pruebas en Docker, con puerto publicado y credenciales en `.env` (ignorado por git)
- [x] 1.1 Crear o cambiar a la rama `feature/create-virtual-ticketing-platform` y verificar con `git branch --show-current` que no se está trabajando en `main`

**Demo:** `openspec list` muestra la Etapa I activa, y `docker ps` muestra la base
levantada. Nada mas: esta fase no produce comportamiento observable por si misma.

**Nota:** la Etapa II (`add-recommendations-and-cache`) queda parked aqui. Su
propio proposal declara que no escribe deltas sobre capacidades existentes
porque la Etapa I no esta archivada, asi que el orden I -> II es obligatorio.

---

## F1. Espina observable

Objetivo: las dos aplicaciones existen, se hablan y toda peticion queda
correlacionada. Sin dominio todavia.

Dibuja de 1.2 a 1.5 y de 2.1 a 2.4. La rama ya existe desde F0.

- [ ] 1.2 Crear la aplicación Python de backend con FastAPI, Pydantic v2, SQLAlchemy 2 y Alembic, y verificar con `python -c "import fastapi, pydantic, sqlalchemy, alembic"` que sus módulos están disponibles
- [ ] 1.3 Crear la aplicación Python frontend con Flask 3 y Jinja2, preparar el recurso estático de HTMX y verificar con `python -c "import flask, jinja2"` y una prueba de cliente que la aplicación arranca
- [ ] 1.4 Configurar Ruff, mypy y pytest en ambos proyectos, y verificar con `ruff check .`, `mypy backend web` y `pytest --collect-only` que las herramientas descubren el código sin errores
- [ ] 1.5 Configurar una instancia PostgreSQL de pruebas y fixtures de pytest con aislamiento por test, y verificar que una prueba de conexión y una prueba de rollback pasan
- [ ] 2.1 Implementar en FastAPI el middleware de `X-Request-ID` para generarlo, conservarlo y devolverlo, y verificar con pruebas HTTP que un identificador válido se preserva y uno ausente se genera
- [ ] 2.2 Implementar logs JSON del backend con los campos permitidos de método, ruta, estado, duración, correlación y usuario, y verificar capturando logs que nunca contengan contraseñas, cookies, tokens, pagos ni cargas QR
- [ ] 2.3 Implementar errores `application/problem+json` con códigos de negocio estables y validación Pydantic v2, y verificar mediante pruebas de API para 4xx y 5xx que el esquema es consistente
- [ ] 2.4 Implementar en Flask el mismo identificador de petición, la propagación a FastAPI y los registros JSON equivalentes, y verificar con una prueba que ambos saltos comparten `request_id`

**Demo:** una peticion al navegador contra Flask produce dos lineas JSON, una de
cada proceso, con el mismo `request_id`; y una peticion sin `X-Request-ID` recibe
uno generado que vuelve en la respuesta. Un 404 de FastAPI llega a Flask como
`problem+json` con su codigo de negocio.

---

## F2. Persona real

Objetivo: una persona se registra, entra y ve algo suyo. La aprobacion de
proveedores todavia no existe, asi que el unico camino probado es el de cliente.

Dibuja de 2.5, de 3.1 a 3.7, de 3.9 y de 6.1 a 6.3.

- [ ] 3.1 Configurar el motor SQLAlchemy 2, la sesión por petición y la conexión transaccional a PostgreSQL, y verificar con una prueba de commit y rollback que el estado de la base no se filtra entre requests
- [ ] 3.2 Implementar `users` y `auth_sessions` con email normalizado único, roles, estados, hash de refresh token, expiración, rotación y revocación, y verificar con pruebas de restricciones e índices
- [ ] 3.3 Crear la revisión inicial de Alembic para identidad y verificar con `alembic upgrade head` y `alembic downgrade base` sobre una base PostgreSQL vacía
- [ ] 3.4 Implementar registro público, normalización de email y hash Argon2id, y verificar con pruebas que un cliente queda activo, un proveedor queda pendiente y un rol no permitido se rechaza
- [ ] 3.5 Implementar JWT Ed25519 de corta duración y refresh tokens opacos almacenados como hash con rotación y detección de reutilización, y verificar con pruebas de expiración, renovación, revocación y reutilización
- [ ] 3.6 Exponer login, refresh y logout bajo `/api/v1/auth`, y verificar mediante pruebas de contrato que solo las cuentas activas emiten tokens y que logout elimina la sesión
- [ ] 3.7 Implementar dependencias FastAPI para `admin`, `event-owner` y `client` y ownership de recursos, y verificar con pruebas matriz que cada rol y acceso entre recursos quedan permitidos o denegados correctamente
- [ ] 2.5 Implementar el cliente web agrupado hacia FastAPI, el mapeo de `problem+json` a formularios o fragmentos y el timeout recuperable, y verificar con pruebas del cliente para éxito, 4xx, 5xx y timeout
- [ ] 6.1 Configurar Flask con Blueprints, plantillas Jinja2, HTMX y un almacén de sesiones del lado servidor aislado del esquema de dominio, y verificar que el navegador solo recibe un identificador opaco en cookie `HttpOnly`, `Secure` y `SameSite`
- [ ] 6.2 Implementar layout base, navegación por rol, cierre de sesión y protección de vistas, y verificar con pruebas Flask que cada rol ve sus enlaces y una vista ajena devuelve acceso denegado
- [ ] 6.3 Implementar formularios web de registro, login, logout y renovación de sesión mediante la API, y verificar con pruebas de navegador que un cliente accede, un proveedor pendiente no publica y logout elimina las credenciales locales
- [ ] 3.9 Ejecutar la suite de identidad contra PostgreSQL y verificar que cubre registro, aprobación, autenticación, renovación, revocación, suspensión y autorización sin usar SQLite

**Demo:** en el navegador, un visitante se registra, inicia sesion y ve su pagina
de cuenta. Un correo duplicado devuelve un error de campo en el formulario, no un
500. El refresh rota el token y un refresh reutilizado se detecta. Cerrar sesion
borra el estado del servidor. La cookie del navegador es opaca e insensible al
contenido.

---

## F3. Dos roles

Objetivo: el administrador y el proveedor empiezan a colaborar. Un evento con
zona existe, y su zona ya no se puede borrar.

Dibuja de 3.8, de 4.1 a 4.3, de 5.1, de 6.4 y de 7.2 a 7.3.

- [ ] 3.8 Implementar administración de usuarios para aprobar proveedores y suspender cuentas con revocación inmediata de sesiones, y verificar con pruebas API que las operaciones-no administrativas no cambian estado
- [ ] 4.1 Implementar modelos, índices y restricciones para `events`, `zones` y `zone_sequences`, y verificar con pruebas de migración y de integridad que un evento no puede repetir zona y una zona tiene una sola secuencia
- [ ] 5.1 Implementar modelos, índices y restricciones para `orders` y `tickets`, incluida la unicidad `(client_id, idempotency_key)` y `(zone_id, number)`, y verificar las restricciones con pruebas de PostgreSQL
- [ ] 4.2 Implementar creación, edición, publicación y retiro de eventos con validación de datos y ownership, y verificar con pruebas de API los estados `draft`, `published` y `retired`
- [ ] 4.3 Implementar administración de zonas con capacidad y precio decimal no negativo, y verificar que una zona con entradas asignadas no puede eliminarse
- [ ] 6.4 Implementar tokens CSRF y validación de formularios en servidor para operaciones de cambio de estado, y verificar que una solicitud sin token o con datos inválidos no modifica recursos
- [ ] 7.2 Implementar la vista de administración de usuarios para aprobar proveedores y suspender cuentas, y verificar que las acciones exigen rol `admin` y reflejan el estado devuelto por la API
- [ ] 7.3 Implementar vistas de proveedor para crear, editar, publicar y retirar sus eventos y zonas, y verificar que un proveedor no puede operar sobre eventos ajenos

**Demo:** un administrador abre la lista de usuarios, aprueba a un proveedor
pendiente y ese proveedor ya puede entrar. El proveedor crea un evento en `draft`,
le agrega una zona con capacidad y precio, y lo publica. Un formulario enviado
sin token CSRF no cambia nada. Una zona con una entrada de prueba no se puede
borrar.

---

## F4. El camino del dinero

Objetivo: un cliente encuentra un evento, compra y ve su entrada. La venta es
correcta pero todavia no es concurrente; eso es F5.

Dibuja de 4.4 a 4.6, de 5.2, 5.3 y 5.5 a 5.7, de 6.5 y de 7.1, 7.4 y 7.5.

> **Estado intermedio deliberado.** El flujo de venta que entrega esta fase
> comprueba capacidad y asigna numeros, pero no bloquea zonas. Esa es la tarea 5.4
> en F5. No es un olvido: F5 es donde se cierra y donde se demuestra con
> `pytest -m concurrency`. Si 5.4 se implementa aqui, F5 se queda sin su
> verificacion.

- [ ] 4.4 Implementar el cálculo de disponibilidad por zona sin exponer inventario interno, y verificar con pruebas de casos límite que la capacidad nunca se excede
- [ ] 4.5 Implementar el catálogo y detalle público de eventos bajo `/api/v1/events`, y verificar que solo devuelve eventos publicados con zonas, precios y disponibilidad válidos
- [ ] 5.2 Implementar creación de pedido y proveedor de pago simulado con resultados aprobado y rechazado, y verificar mediante pruebas que un rechazo conserva el pedido sin activar y no asigna tickets
- [ ] 5.3 Implementar idempotencia mediante clave, hash canónico de la solicitud y restricción única, y verificar que un reintento idéntico devuelve el resultado existente y una clave reutilizada con otros datos devuelve conflicto
- [ ] 5.5 Implementar `zone_sequences` para asignar números consecutivos desde 1 por zona sin reutilizarlos, y verificar con pruebas de primera compra, compra posterior y zona independiente
- [ ] 5.6 Emitir entradas virtuales con evento, zona, número, propietario, nonce y payload QR firmado, y verificar que el payload se entrega al propietario y no se registra en texto claro
- [ ] 5.7 Implementar listado y detalle de entradas con ownership de cliente y autorización administrativa, y verificar que un cliente no puede leer una entrada ajena
- [ ] 7.1 Implementar catálogo y detalle público en Flask mediante la API, y verificar con pruebas Jinja que solo aparecen eventos publicados y sus datos públicos
- [ ] 7.4 Implementar el flujo de compra de cliente con selección de cantidades, `Idempotency-Key` y representación del resultado, y verificar que un reintento no crea entradas adicionales
- [ ] 7.5 Implementar la vista privada de entradas con números y códigos QR, y verificar que un cliente solo ve las propias y que el QR no se imprime en logs
- [ ] 4.6 Ejecutar las pruebas de eventos contra PostgreSQL y verificar los flujos de proveedor, administrador y visitante sin divergencias entre permisos de API y ownership
- [ ] 6.5 Ejecutar la suite web base con `pytest web`, verificando cookies, sesión, CSRF, navegación por rol y representación de errores sin exponer respuestas internas

**Demo:** un cliente abre el catalogo, ve el evento publicado con su
disponibilidad, compra dos entradas de una zona y llega a una vista privada con
los numeros y el QR. Recargar la compra con la misma `Idempotency-Key` devuelve las
mismas entradas y no crea otras. Un pago simulado rechazado deja el pedido sin
activar y sin entradas. Un cliente no puede abrir la entrada de otro.

---

## F5. La garantia dificil

Objetivo: que la venta sea correcta con dos compras compitiendo. Son dos tareas
y son las que justifican el diseno completo.

Dibuja de 5.4 y 5.8.

- [ ] 5.4 Implementar la venta atómica bloqueando zonas en orden determinista y comprobando capacidad antes de insertar, y verificar que un fallo de una zona revierte todo el pedido sin asignaciones parciales
- [ ] 5.8 Implementar pruebas de concurrencia con dos compras compitiendo por la última capacidad y ejecutar `pytest -m concurrency` contra PostgreSQL, verificando que solo una venta completa y no hay números duplicados

**Demo:** dos clientes compran la ultima entrada de una zona a la vez. Solo una
compra completa; la otra recibe `inventory_unavailable`. La zona queda con una
sola entrada, su numero es el esperado y no se reutiliza en la compra siguiente.
Un pedido de dos zonas donde una falla no deja entradas parciales en ninguna.

---

## F6. Endurecer y operar

Objetivo: la calidad que se puede aplazar sin costo de retrospection, y la
operacion completa.

Dibuja de 7.6, 7.7 y de 8.1 a 8.4.

- [ ] 7.6 Implementar fragmentos HTMX para paginación, actualización de listas y errores recuperables, y verificar que las respuestas parciales conservan sesión y no incluyen HTML ajeno
- [ ] 7.7 Verificar navegación por teclado, etiquetas y semántica en las vistas principales, y ejecutar las pruebas de accesibilidad automatizadas disponibles sin errores
- [ ] 8.1 Levantar FastAPI, Flask y PostgreSQL en un entorno de integración y verificar con pruebas de humo que las rutas públicas, autenticadas y administrativas responden desde un mismo origen
- [ ] 8.2 Ejecutar un flujo completo de registro, aprobación, publicación, compra y entrega de entradas, y verificar la respuesta de cada salto con el mismo `request_id` y sin secretos en la salida
- [ ] 8.3 Aplicar `alembic upgrade head` en la base de staging y ejecutar `pytest` completo, verificando que la suite pasa con el esquema final y que no requiere SQLite
- [ ] 8.4 Ensayar en un entorno descartable el plan de rollback de `proposal.md`, incluyendo detener tráfico, revocar sesiones y claves, y comprobar health checks antes de limpiar los datos de prueba

**Demo:** la navegacion por teclado recorre el flujo completo sin perderse, un
fragmento HTMX actualiza una lista sin recargar la pagina, y el ensayo de
rollback revierte el despliegue en un entorno descartable sin tocar datos
conservados.

---

## Al cerrar la Etapa I

`/opsx-sync` para verter las delta specs en `openspec/specs/`, y despues
`/opsx-archive` para archivar el cambio. Recien ahi `add-recommendations-and-cache`
puede declarar deltas sobre capacidades existentes, y su punto de entrada son
las tareas de esta fase: el catalogo publico (4.5) que va a cachear y el
`request_id` (2.1, 2.4) al que se correlacionan sus metricas.
