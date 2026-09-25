# HTTP Caching Delta

## Purpose

Define una cache compartida para lecturas cacheable, con TTL, invalidación por cambios de dominio, degradación segura y protección de datos sensibles.

## ADDED Requirements

### Requirement: Cache de lecturas cacheable
El sistema SHALL cachear de forma cache-aside las recomendaciones, el catálogo público y los metadatos de eventos publicados, y SHALL usar PostgreSQL como fuente de verdad cuando no exista una entrada válida.

#### Scenario: Acierto de cache
- **GIVEN** una clave de cache válida para una lectura cacheable
- **WHEN** un cliente solicita esa lectura
- **THEN** el sistema responde con el valor cacheado dentro de su ventana de frescura sin consultar la fuente de origen

#### Scenario: Fallo de cache
- **GIVEN** una lectura cacheable sin entrada válida
- **WHEN** un cliente solicita esa lectura
- **THEN** el sistema consulta la fuente de verdad, responde correctamente y puede guardar el resultado para la siguiente lectura

#### Scenario: Cache no disponible
- **GIVEN** una cache compartida inaccesible
- **WHEN** un cliente solicita una lectura cacheable
- **THEN** el sistema consulta PostgreSQL, entrega el resultado correcto y registra la degradación sin mostrar un error al usuario

### Requirement: TTL, versionado y límites
El sistema SHALL aplicar TTL configurable a cada clase de entrada, SHALL versionar las claves y SHALL evitar que los valores cacheados superen los límites de tamaño definidos.

#### Scenario: Entrada expira
- **GIVEN** una entrada con TTL vencido
- **WHEN** un cliente solicita la lectura asociada
- **THEN** el sistema la considera ausente, consulta la fuente de verdad y no devuelve el valor vencido

#### Scenario: Cambio de versión
- **GIVEN** una entrada de una versión anterior de clave
- **WHEN** el sistema invalida esa versión
- **THEN** las lecturas posteriores no obtienen el valor anterior aunque la clave física siga existiendo

#### Scenario: Límite de tamaño
- **GIVEN** un conjunto de entradas dentro del presupuesto configurado
- **WHEN** el sistema escribe una entrada que supera el límite permitido
- **THEN** la rechaza o la reduce según la política configurada sin afectar las entradas existentes

### Requirement: Invalidación por cambios de dominio
El sistema SHALL invalidar las entradas afectadas al publicar, editar o retirar un evento, y SHALL invalidar o versionar las listas de recomendaciones y populares al completar una compra.

#### Scenario: Publicación o edición
- **GIVEN** un evento `draft` con una entrada de catálogo cacheada
- **WHEN** el evento se publica o se edita
- **THEN** el sistema invalida la entrada de ese evento, la lista de catálogo y las referencias de recomendaciones afectadas

#### Scenario: Retiro
- **GIVEN** un evento `published` visible en una entrada cacheada
- **WHEN** el evento se retira
- **THEN** el sistema deja de servirlo en lecturas posteriores y actualiza las listas cacheadas afectadas

#### Scenario: Compra completada
- **GIVEN** una compra que reduce la disponibilidad y cambia la popularidad
- **WHEN** la venta se completa
- **THEN** el sistema invalida las lecturas de inventario y las listas popular o de recomendaciones que puedan quedar obsoletas

### Requirement: Datos que nunca se cachean
El sistema SHALL excluir de la cache credenciales, sesiones, respuestas de autenticación, operaciones de compra, pagos, cargas QR y comprobaciones de disponibilidad transaccional.

#### Scenario: Consulta de compra
- **GIVEN** un cliente que consulta el detalle de un pedido o sus entradas
- **WHEN** el sistema atiende la solicitud
- **THEN** lee el dato autorizado desde la fuente de verdad y no lo devuelve desde una entrada compartida

#### Scenario: Compra concurrente
- **GIVEN** una zona con capacidad limitada
- **WHEN** un cliente intenta comprar
- **THEN** el sistema valida la disponibilidad en la transacción de venta aunque exista una lectura cacheada del catálogo

### Requirement: Concurrencia de invalidación
El sistema SHALL evitar que una invalidación pierda cambios posteriores y SHALL limitar las llamadas al origen cuando varias solicitudes simultáneas coincidan en una entrada ausente.

#### Scenario: Invalidaciones superpuestas
- **GIVEN** un evento que cambia mientras se atienden varias lecturas
- **WHEN** llegan una invalidación y una lectura concurrente
- **THEN** el sistema garantiza que una lectura posterior a la invalidación no reciba el valor anterior de forma indefinida

#### Scenario: Tormenta de misses
- **GIVEN** varias solicitudes simultáneas para la misma clave ausente
- **WHEN** el sistema procesa el lote
- **THEN** coordina la recarga para que el origen no reciba una llamada independiente por cada solicitud

### Requirement: Privacidad de la cache
El sistema SHALL separar las entradas por cliente mediante identificadores seudónimos y SHALL evitar que una entrada de un cliente se sirva a otro, incluso si comparten datos públicos.

#### Scenario: Aislamiento de claves
- **GIVEN** dos clientes activos
- **WHEN** uno solicita recomendaciones cacheadas
- **THEN** la clave incluye su identidad seudónima y el otro cliente no puede obtener ni esa clave ni su resultado

#### Scenario: Expiración de datos personales
- **GIVEN** una entrada que contiene un identificador seudónimo
- **WHEN** expira su TTL o se invalida por cambio de versión
- **THEN** el sistema la elimina según la política de Redis y conserva solo referencias no sensibles para diagnóstico

### Requirement: Observabilidad de la cache
El sistema SHALL registrar métricas y eventos estructurados de acierto, fallo, invalidación, TTL, degradación y latencia de origen, correlacionados con el `request_id`.

#### Scenario: Métrica de acierto
- **GIVEN** una lectura servida desde cache
- **WHEN** el sistema completa la petición
- **THEN** incrementa el contador de aciertos y registra el `request_id` sin datos de cliente

#### Scenario: Métrica de degradación
- **GIVEN** una cache o un origen temporalmente indisponible
- **WHEN** el sistema sirve la respuesta sin cache
- **THEN** incrementa el contador de degradación y deja un mensaje operativo que no contiene tokens ni cargas QR
