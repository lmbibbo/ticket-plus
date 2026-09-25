# Recommendations Delta

## Purpose

Define recomendaciones personalizadas para clientes mediante un contrato de integración propio, con fallback popular, privacidad y estados observables en la API y la web.

## ADDED Requirements

### Requirement: Recomendaciones para clientes autenticados
El sistema SHALL devolver recomendaciones únicamente para una sesión de cliente activa y SHALL limitar la cantidad a un máximo configurable.

#### Scenario: Cliente recibe recomendaciones
- **GIVEN** un cliente autenticado con al menos un evento elegible
- **WHEN** solicita recomendaciones con un límite válido
- **THEN** el sistema devuelve eventos publicados, distintos entre sí, ordenados por relevancia y como máximo esa cantidad

#### Scenario: Visitante sin sesión
- **GIVEN** un visitante no autenticado
- **WHEN** solicita recomendaciones
- **THEN** el sistema deniega el acceso y no devuelve una lista personalizada

#### Scenario: Rol no cliente
- **GIVEN** un usuario autenticado con rol distinto de `client`
- **WHEN** solicita recomendaciones
- **THEN** el sistema deniega el acceso sin revelar preferencias de ningún cliente

#### Scenario: Límite inválido
- **GIVEN** un cliente autenticado
- **WHEN** solicita un límite menor que uno o superior al máximo permitido
- **THEN** el sistema rechaza la solicitud con un error de validación y no consulta al proveedor

### Requirement: Integración con un contrato propio
El sistema SHALL integrar el proveedor mediante un contrato de entrada y salida estable, validado antes de usarse, y SHALL enviar un identificador seudónimo en lugar de datos personales.

#### Scenario: Solicitud válida al proveedor
- **GIVEN** un cliente elegible y un proveedor disponible
- **WHEN** el sistema solicita recomendaciones
- **THEN** envía el identificador seudónimo y el contexto mínimo, y recibe una lista con identificador de evento, puntuación y motivo

#### Scenario: Identificador seudónimo estable
- **GIVEN** el mismo cliente en dos solicitudes dentro del período de vigencia
- **WHEN** el sistema prepara ambas solicitudes al proveedor
- **THEN** utiliza el mismo identificador seudónimo y no incluye email, nombre, credenciales, tokens, pagos ni códigos QR

#### Scenario: Respuesta con eventos inválidos
- **GIVEN** un proveedor que devuelve un evento inexistente, no publicado o duplicado
- **WHEN** el sistema valida la respuesta
- **THEN** descarta los elementos inválidos, conserva el orden válido y no expone el evento rechazado

### Requirement: Degradación a eventos populares
El sistema SHALL calcular una lista local de eventos populares y SHALL usarla cuando el proveedor no responda, exceda el tiempo máximo o devuelva un contrato inválido.

#### Scenario: Proveedor no disponible
- **GIVEN** un cliente autenticado y un proveedor inaccesible
- **WHEN** solicita recomendaciones
- **THEN** el sistema devuelve eventos populares publicados con origen `fallback` y no expone el error del proveedor

#### Scenario: Respuesta inválida del proveedor
- **GIVEN** un proveedor que responde con un payload que no cumple el contrato
- **WHEN** el sistema valida la respuesta
- **THEN** descarta la respuesta, registra el fallo con el `request_id` y usa la lista popular

#### Scenario: Sin eventos populares
- **GIVEN** un cliente sin historial y ningún evento popular elegible
- **WHEN** solicita recomendaciones y el proveedor no devuelve resultados
- **THEN** el sistema devuelve una lista vacía con estado de resultado vacío, sin error

### Requirement: Presentación en la web de cliente
El sistema SHALL mostrar la sección de recomendaciones solo a clientes autenticados y SHALL representar de forma segura los estados de carga, vacío, personalizada y fallback.

#### Scenario: Cliente ve la sección
- **GIVEN** un cliente autenticado
- **WHEN** abre su vista privada
- **THEN** el sistema muestra recomendaciones y su origen sin exponer información de otros clientes

#### Scenario: Sección no autorizada
- **GIVEN** un visitante, proveedor o administrador
- **WHEN** abre la vista de recomendaciones
- **THEN** el sistema no muestra la sección o deniega el acceso con un mensaje seguro

#### Scenario: Estado vacío
- **GIVEN** un cliente sin recomendaciones disponibles
- **WHEN** abre la vista privada
- **THEN** el sistema muestra un estado vacío comprensible y ninguna tarjeta de evento

#### Scenario: Fallback visible
- **GIVEN** un cliente que recibe recomendaciones `fallback`
- **WHEN** se muestra la sección
- **THEN** el sistema etiqueta el contenido como populares y no muestra un mensaje de error técnico

### Requirement: Privacidad y control de sesión
El sistema SHALL invalidar el acceso a recomendaciones cuando la sesión del cliente se cierre o se suspenda, y SHALL evitar que la cache compartida mezcle preferencias entre cuentas.

#### Scenario: Sesión revocada
- **GIVEN** un cliente con una sesión de recomendaciones activa
- **WHEN** su sesión se cierra o su cuenta se suspende
- **THEN** el sistema deja de servir recomendaciones para esa sesión y exige una nueva autenticación

#### Scenario: Aislamiento entre clientes
- **GIVEN** dos clientes activos con historiales diferentes
- **WHEN** cada uno solicita recomendaciones
- **THEN** el sistema entrega resultados asociados a su propia sesión y no expone preferencias del otro cliente
