# Ticket Sales Delta

## Purpose

Define la compra idempotente con pago simulado, la asignación transaccional de entradas y la entrega de entradas virtuales QR sin duplicados ni sobreventa.

## ADDED Requirements

### Requirement: Creación de compra
El sistema SHALL permitir que un cliente autenticado crear un pedido para un evento publicado, seleccionando zonas y cantidades válidas.

#### Scenario: Pedido válido
- **GIVEN** un cliente autenticado y un evento publicado con disponibilidad
- **WHEN** envía un pedido con zonas y cantidades permitidas
- **THEN** el sistema registra el pedido y lo asocia al cliente y al evento

### Requirement: Pago simulado
El sistema SHALL validar el pago mediante un proveedor simulado y SHALL rechazar la compra cuando la simulación no sea aprobada.

#### Scenario: Pago aprobado
- **GIVEN** un pedido pendiente con datos de pago simulados válidos
- **WHEN** el sistema procesa el pago
- **THEN** el sistema registra el resultado aprobado y continúa con la asignación de entradas

#### Scenario: Pago rechazado
- **GIVEN** un pedido pendiente
- **WHEN** la simulación de pago devuelve un resultado rechazado
- **THEN** el sistema no asigna entradas y conserva el pedido sin activar

### Requirement: Transacción de venta
El sistema SHALL asignar todas las entradas del pedido de forma atómica y SHALL registrar un resultado de venta único.

#### Scenario: Venta creada
- **GIVEN** un pedido con pago aprobado y disponibilidad suficiente
- **WHEN** el sistema completa la asignación
- **THEN** crea las entradas, marca el pedido como vendido y no crea un segundo resultado de venta

#### Scenario: Venta duplicada
- **GIVEN** un pedido que ya tiene un resultado de venta
- **WHEN** el sistema intenta completar nuevamente la asignación
- **THEN** devuelve el resultado existente sin crear entradas adicionales

#### Scenario: Inventario insuficiente
- **GIVEN** un pedido con pago aprobado pero sin inventario suficiente
- **WHEN** el sistema intenta completar la asignación
- **THEN** revierte la operación completa y no deja entradas ni cambios parciales

### Requirement: Idempotencia de compras
El sistema SHALL aceptar una clave de idempotencia por cliente y SHALL devolver el mismo resultado cuando una solicitud se reintenta con la misma clave y los mismos datos.

#### Scenario: Reintento exitoso
- **GIVEN** un cliente que ya completó una compra con una clave de idempotencia
- **WHEN** reenvía la misma solicitud con esa clave y los mismos datos
- **THEN** el sistema devuelve el pedido y las entradas existentes sin crear duplicados

#### Scenario: Reutilización con datos distintos
- **GIVEN** una clave de idempotencia utilizada por un cliente
- **WHEN** el cliente la reutiliza con datos de compra diferentes
- **THEN** el sistema rechaza la solicitud y no modifica la compra original

### Requirement: Numeración de entradas
El sistema SHALL asignar números secuenciales a las entradas de cada zona y SHALL comenzar la numeración en 1 sin reutilizar números durante la vigencia del evento.

#### Scenario: Primera entrada de zona
- **GIVEN** una zona sin entradas asignadas
- **WHEN** el sistema asigna la primera entrada
- **THEN** el número asignado es 1

#### Scenario: Entradas consecutivas
- **GIVEN** una zona con las entradas 1 y 2 asignadas
- **WHEN** el sistema asigna otra entrada
- **THEN** el número asignado es 3 y no duplica un número existente

### Requirement: Propiedad de entradas
El sistema SHALL asociar cada entrada al cliente que la compró y SHALL restringir su consulta al propietario o a un administrador autorizado.

#### Scenario: Cliente lista sus entradas
- **GIVEN** un cliente con entradas compradas
- **WHEN** solicita la lista de sus entradas
- **THEN** el sistema devuelve únicamente las entradas asociadas a su cuenta

#### Scenario: Cliente consulta entrada ajena
- **GIVEN** un cliente autenticado
- **WHEN** solicita una entrada asociada a otra cuenta
- **THEN** el sistema deniega el acceso y no revela sus datos

### Requirement: Entrada virtual QR
El sistema SHALL emitir una entrada virtual con un código QR verificable y SHALL impedir que el código se use para validar check-in en este alcance.

#### Scenario: Emisión de entrada virtual
- **GIVEN** una venta completada
- **WHEN** el cliente consulta una entrada de su pedido
- **THEN** el sistema muestra evento, zona, número y un código QR asociado a esa entrada

### Requirement: Concurrencia de compras
El sistema SHALL mantener el inventario consistente ante compras concurrentes y SHALL rechazar toda solicitud que exceda la capacidad disponible.

#### Scenario: Compras concurrentes por zona
- **GIVEN** una zona con una capacidad determinada y dos compras concurrentes que en conjunto la superan
- **WHEN** ambas solicitudes intentan reservar inventario
- **THEN** solo una completa la reserva y la otra recibe un error de disponibilidad sin asignación parcial

#### Scenario: Reserva concurrente por zona
- **GIVEN** una zona con capacidad disponible para una sola entrada
- **WHEN** dos solicitudes concurrentes intentan adquirir esa entrada
- **THEN** el sistema asigna exactamente una entrada y rechaza la segunda sin crear un número duplicado
