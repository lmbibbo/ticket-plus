# HTTP Observability Delta

## Purpose

Define la bitácora HTTP estructurada y correlacionada de las aplicaciones web y API para diagnosticar peticiones sin exponer credenciales ni datos sensibles.

## ADDED Requirements

### Requirement: Correlación de petición web y API
El sistema SHALL compartir un identificador de correlación entre cada petición web y las llamadas que esa página realiza a la API.

#### Scenario: Petición con identificador entrante
- **GIVEN** una petición web con un `request_id` válido
- **WHEN** la aplicación recibe la petición
- **THEN** conserva el identificador, lo incluye en la respuesta y lo utiliza en sus registros

#### Scenario: Propagación hacia la API
- **GIVEN** una petición web que necesita obtener datos del backend
- **WHEN** la aplicación web reenvía la operación a la API
- **THEN** propaga el mismo `request_id` para permitir la correlación de ambos saltos

#### Scenario: Falta un identificador entrante
- **GIVEN** una petición web sin `request_id`
- **WHEN** la aplicación recibe la petición
- **THEN** genera un identificador, lo devuelve en la respuesta y lo utiliza en todos los registros relacionados

### Requirement: Registro estructurado de respuesta
El sistema SHALL registrar una línea estructurada al completar cada petición, incluyendo método, ruta, estado, duración e identificador de correlación.

#### Scenario: Petición completada correctamente
- **GIVEN** una petición web o de API que termina con estado 2xx
- **WHEN** la aplicación completa la respuesta
- **THEN** registra el resultado y la duración bajo el mismo identificador de correlación

#### Scenario: Petición completada con error
- **GIVEN** una petición web o de API que termina con estado 4xx o 5xx
- **WHEN** la aplicación completa la respuesta
- **THEN** registra la misma información de resultado y duración, incluido el código de error

### Requirement: Protección de datos sensibles
El sistema SHALL omitir o redactar contraseñas, tokens, cookies de sesión, códigos de pago y cargas QR en los registros HTTP.

#### Scenario: Solicitud con credenciales
- **GIVEN** una petición que contiene contraseñas, tokens o cookies
- **WHEN** la aplicación genera sus registros
- **THEN** ningún valor de autenticación aparece en texto claro

#### Scenario: Respuesta con datos sensibles
- **GIVEN** una respuesta que contiene códigos de pago o cargas QR
- **WHEN** la aplicación genera sus registros
- **THEN** registra solo referencias no sensibles y conserva el valor original únicamente en la entrega autorizada

### Requirement: Identificación y trazabilidad
El sistema SHALL asociar cada registro con una identidad autenticada cuando exista y SHALL preservar el identificador de correlación en errores y fallos de integración.

#### Scenario: Petición autenticada
- **GIVEN** una petición realizada por un usuario autenticado
- **WHEN** la aplicación registra su resultado
- **THEN** incluye el identificador del usuario sin incluir su contraseña ni token

#### Scenario: Fallo entre web y API
- **GIVEN** una llamada de la aplicación web a la API que no puede completarse
- **WHEN** la aplicación registra el error
- **THEN** conserva el identificador de correlación y registra un mensaje operativo sin datos internos sensibles
