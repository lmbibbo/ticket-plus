# Identity Access Delta

## Purpose

Define registro, aprobación de proveedores, autenticación, renovación y revocación de sesiones, y permisos asociados a los tres roles de la ticketera.

## ADDED Requirements

### Requirement: Registro de usuarios
El sistema SHALL permitir el registro público de clientes y proveedores, y SHALL aplicar el flujo de aprobación requerido a las cuentas de proveedor.

#### Scenario: Registro válido como cliente
- **GIVEN** un visitante en un formulario de registro con datos válidos
- **WHEN** selecciona el rol `client` y envía el formulario
- **THEN** el sistema crea una cuenta activa con rol `client`

#### Scenario: Registro con rol no permitido
- **GIVEN** un visitante en un formulario de registro público
- **WHEN** intenta crear una cuenta con un rol no incluido en el registro público
- **THEN** el sistema rechaza la solicitud sin crear una cuenta

#### Scenario: Registro de proveedor pendiente
- **GIVEN** un visitante en un formulario de registro con datos válidos
- **WHEN** selecciona el rol `event-owner` y envía el formulario
- **THEN** el sistema crea una cuenta con rol `event-owner` y estado `pending`

### Requirement: Aprobación de proveedores
El sistema SHALL mantener las cuentas de proveedor en estado pendiente hasta que un administrador las apruebe.

#### Scenario: Administrador lista proveedores pendientes
- **GIVEN** un administrador autenticado
- **WHEN** abre la lista de proveedores pendientes
- **THEN** el sistema devuelve únicamente las cuentas de proveedor pendientes de aprobación

#### Scenario: Aprobación de proveedor
- **GIVEN** un administrador autenticado y una cuenta de proveedor pendiente
- **WHEN** el administrador confirma la aprobación
- **THEN** el sistema cambia el estado de la cuenta a `active` y permite su autenticación

#### Scenario: Aprobación por usuario no autorizado
- **GIVEN** un usuario autenticado con rol distinto de `admin`
- **WHEN** intenta aprobar una cuenta de proveedor
- **THEN** el sistema deniega la operación y no cambia el estado de la cuenta

### Requirement: Autenticación y validación de sesión
El sistema SHALL autenticar usuarios activos y SHALL mantener revocadas las sesiones de cuentas suspendidas o eliminadas.

#### Scenario: Inicio de sesión de cliente
- **GIVEN** un cliente activo con credenciales válidas
- **WHEN** envía el inicio de sesión
- **THEN** el sistema devuelve un token JWT de acceso con expiración y un refresh token de sesión

#### Scenario: Credenciales inválidas
- **GIVEN** un usuario que no tiene una cuenta activa con las credenciales enviadas
- **WHEN** envía el inicio de sesión
- **THEN** el sistema rechaza la solicitud con un error de autenticación genérico

#### Scenario: Credenciales de cuenta suspendida
- **GIVEN** una cuenta suspendida con credenciales previamente válidas
- **WHEN** intenta iniciar sesión
- **THEN** el sistema rechaza el inicio de sesión y no emite tokens

### Requirement: Renovación y revocación de sesión
El sistema SHALL renovar la sesión con un refresh token válido y SHALL impedir el uso de refresh tokens revocados.

#### Scenario: Renovación con sesión activa
- **GIVEN** un usuario con un refresh token válido y una sesión activa
- **WHEN** solicita renovar el token de acceso
- **THEN** el sistema emite un nuevo token de acceso asociado a la misma sesión

#### Scenario: Reutilización de refresh token revocado
- **GIVEN** un refresh token que fue revocado previamente
- **WHEN** se utiliza para solicitar un nuevo token de acceso
- **THEN** el sistema rechaza la renovación y no emite un token

#### Scenario: Cierre de sesión explícito
- **GIVEN** un usuario con una sesión activa
- **WHEN** confirma el cierre de sesión
- **THEN** el sistema revoca el refresh token, registra el evento y no permite nuevas renovaciones

#### Scenario: Uso de sesión de cuenta suspendida
- **GIVEN** una cuenta suspendida que conserva un token de acceso vigente
- **WHEN** intenta realizar una operación autenticada
- **THEN** el sistema rechaza la operación y revoca las sesiones activas de la cuenta

### Requirement: Autorización por rol
El sistema SHALL separar las capacidades de `admin`, `event-owner` y `client`, y SHALL denegar el acceso a recursos que no pertenezcan al actor o a su rol.

#### Scenario: Acceso administrativo
- **GIVEN** un usuario autenticado con rol `admin`
- **WHEN** solicita una operación administrativa autorizada
- **THEN** el sistema la ejecuta y registra la acción con la identidad del administrador

#### Scenario: Proveedor accede a un evento propio
- **GIVEN** un proveedor autenticado y un evento cuyo propietario es esa cuenta
- **WHEN** solicita la gestión del evento
- **THEN** el sistema permite la operación

#### Scenario: Proveedor accede a un evento ajeno
- **GIVEN** un proveedor autenticado y un evento perteneciente a otra cuenta
- **WHEN** solicita modificar o publicar ese evento
- **THEN** el sistema deniega la operación sin revelar datos sensibles del evento

#### Scenario: Cliente no accede a la gestión de proveedores
- **GIVEN** un usuario autenticado con rol `client`
- **WHEN** intenta aprobar proveedores o administrar eventos
- **THEN** el sistema deniega la operación

#### Scenario: Administrador accede a todos los eventos
- **GIVEN** un usuario autenticado con rol `admin`
- **WHEN** solicita la gestión de cualquier evento
- **THEN** el sistema permite la operación y registra el actor administrativo
