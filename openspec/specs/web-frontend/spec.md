# Web Frontend Specification

## Purpose

Define la interfaz web renderizada en servidor para descubrir eventos, autenticarse y operar la ticketera según los permisos de cada rol.

## Requirements

### Requirement: Catálogo público de eventos
El sistema SHALL mostrar en una vista pública los eventos publicados que tengan zonas, precios y disponibilidad visible, sin exponer controles administrativos.

#### Scenario: Visitante consulta el catálogo
- **GIVEN** un visitante no autenticado
- **WHEN** solicita la página principal de eventos
- **THEN** el sistema muestra únicamente eventos publicados con información pública de zonas, precios y disponibilidad

#### Scenario: Visitante no ve controles de gestión
- **GIVEN** un visitante no autenticado
- **WHEN** visualiza el detalle de un evento publicado
- **THEN** el sistema no muestra acciones para crear, editar o publicar eventos

### Requirement: Navegación según el rol
El sistema SHALL adaptar navegación, páginas y acciones disponibles al rol de la sesión activa y SHALL denegar acceso a vistas no autorizadas.

#### Scenario: Cliente autenticado
- **GIVEN** un usuario activo con rol `client`
- **WHEN** inicia sesión
- **THEN** el sistema muestra el catálogo y sus entradas, pero no muestra administración de usuarios ni gestión de eventos ajenos

#### Scenario: Proveedor autenticado
- **GIVEN** un usuario activo con rol `event-owner`
- **WHEN** inicia sesión
- **THEN** el sistema muestra la creación y gestión de sus propios eventos y no expone las operaciones reservadas al administrador

#### Scenario: Administrador autenticado
- **GIVEN** un usuario activo con rol `admin`
- **WHEN** inicia sesión
- **THEN** el sistema muestra las vistas de administración de usuarios y eventos

#### Scenario: Usuario intenta abrir una vista no autorizada
- **GIVEN** un usuario autenticado
- **WHEN** solicita una vista reservada a otro rol
- **THEN** el sistema deniega el acceso sin revelar datos ni controles de la vista solicitada

### Requirement: Formularios con validación del servidor
El sistema SHALL validar en el servidor los formularios de registro, inicio de sesión, gestión, compra y aprobación, y SHALL mostrar errores asociados a cada dato inválido.

#### Scenario: Registro válido
- **GIVEN** un visitante en el formulario de registro
- **WHEN** envía datos válidos para una cuenta de cliente
- **THEN** el sistema crea la cuenta, inicia la sesión y muestra la vista de cliente

#### Scenario: Registro de proveedor pendiente
- **GIVEN** un visitante en el formulario de registro
- **WHEN** solicita crear una cuenta de proveedor con datos válidos
- **THEN** el sistema crea una cuenta pendiente de aprobación e informa que todavía no puede publicar eventos

#### Scenario: Formulario con errores
- **GIVEN** un usuario en un formulario web
- **WHEN** envía un campo obligatorio vacío o un valor inválido
- **THEN** el sistema vuelve a mostrar el formulario con un error asociado al campo y sin crear ni modificar recursos

### Requirement: Flujos de gestión por rol
El sistema SHALL proporcionar flujos web para que los administradores gestionen usuarios y eventos, y para que los proveedores gestionen los eventos que les pertenecen.

#### Scenario: Administrador aprueba un proveedor
- **GIVEN** un administrador en la lista de proveedores pendientes
- **WHEN** confirma la aprobación de una cuenta pendiente
- **THEN** el sistema muestra la cuenta como activa y permite que su propietario inicie sesión

#### Scenario: Administrador suspende un usuario
- **GIVEN** un administrador en el detalle de un usuario activo
- **WHEN** confirma la suspensión de la cuenta
- **THEN** el sistema revoca sus sesiones activas y muestra la cuenta como suspendida

#### Scenario: Proveedor publica un evento
- **GIVEN** un proveedor activo en el detalle de un evento propio con zonas, precios y capacidad válidos
- **WHEN** confirma la publicación
- **THEN** el evento cambia a `published` y pasa a estar visible en el catálogo público

### Requirement: Flujo de compra y entradas
El sistema SHALL permitir que un cliente autenticado compre desde la página de un evento y consulte sus entradas únicamente en su vista privada.

#### Scenario: Compra desde la interfaz
- **GIVEN** un cliente autenticado en el detalle de un evento publicado
- **WHEN** selecciona cantidades que respetan la disponibilidad y confirma la compra
- **THEN** el sistema registra un único pedido, asigna las entradas y muestra sus números y códigos QR

#### Scenario: Cliente consulta sus entradas
- **GIVEN** un cliente con entradas compradas
- **WHEN** abre su vista de entradas
- **THEN** el sistema muestra los eventos, zonas, números y códigos QR que le pertenecen

#### Scenario: Cliente intenta consultar entradas ajenas
- **GIVEN** un cliente autenticado
- **WHEN** intenta abrir una entrada pertenece a otra cuenta
- **THEN** el sistema deniega el acceso sin revelar sus datos

### Requirement: Interacciones parciales y manejo de fallos
El sistema SHALL actualizar fragmentos independientes sin abandonar la sesión y SHALL representar de forma segura los errores de validación y disponibilidad.

#### Scenario: Respuesta parcial exitosa
- **GIVEN** un usuario en una página de lista paginada
- **WHEN** solicita la siguiente página
- **THEN** el sistema conserva la sesión y actualiza únicamente el fragmento de resultados

#### Scenario: Evento queda sin inventario
- **GIVEN** un cliente en el resumen de una compra
- **WHEN** confirma después de que la disponibilidad cambió
- **THEN** el sistema no cobra ni asigna entradas y muestra un mensaje actualizado de indisponibilidad

#### Scenario: Backend no disponible
- **GIVEN** cualquier usuario autenticado
- **WHEN** una operación web no puede completar una solicitud al servicio de dominio
- **THEN** el sistema conserva la sesión, no expone detalles internos y muestra un error recuperable

### Requirement: Accesibilidad y navegación segura
El sistema SHALL entregar contenido semántico, controles etiquetados y navegación por teclado, y SHALL cambiar la sesión mediante un mecanismo seguro.

#### Scenario: Uso de teclado
- **GIVEN** una página de autenticación o gestión de eventos
- **WHEN** una persona navega exclusivamente con teclado
- **THEN** puede alcanzar y activar los controles principales en un orden comprensible

#### Scenario: Cierre de sesión
- **GIVEN** un usuario autenticado
- **WHEN** confirma el cierre de sesión
- **THEN** el sistema elimina las credenciales locales, revoca la sesión aplicable y vuelve a una vista pública

### Requirement: HTTP errors preserve their real status
The web system SHALL answer HTTP-level errors (unknown route, method not allowed
and other routing failures) with the status code that corresponds to the failure,
SHALL return a safe JSON problem body with the fields `code`, `title`, `status`,
`detail` and `request_id`, and SHALL reserve the `500 internal_error` response
for unexpected failures. Neither response SHALL expose internal details such as
tracebacks, file paths or configuration values.

#### Scenario: Unknown route
- **GIVEN** a visitor requests a URL that no route matches
- **WHEN** the web application processes the request
- **THEN** it responds with status `404`, a problem body whose `code` is
  `not_found` and whose `status` is `404`, the correlation `request_id`, and no
  internal detail

#### Scenario: Method not allowed for an existing route
- **GIVEN** a client requests a path that exists but with a method the route does
  not accept
- **WHEN** the web application processes the request
- **THEN** it responds with status `405` and a problem body whose `status` is
  `405`, without leaking internal details

#### Scenario: Unexpected failure is still a server error
- **GIVEN** a request whose handling raises a non-HTTP programming error
- **WHEN** the web application processes the request
- **THEN** it responds with status `500` and a problem body whose `code` is
  `internal_error`, exposing no traceback or internal data
