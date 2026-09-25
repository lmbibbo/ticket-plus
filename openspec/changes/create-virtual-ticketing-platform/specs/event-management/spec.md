# Event Management Delta

## Purpose

Define el ciclo de vida de eventos y la administración de zonas, precios y capacidad para eventos publicados por proveedores o administradores.

## ADDED Requirements

### Requirement: Gestión del ciclo de vida del evento
El sistema SHALL permitir que proveedores y administradores creen eventos en estado `draft` y controlen su publicación y retiro del catálogo.

#### Scenario: Creación de evento por proveedor
- **GIVEN** un proveedor autenticado
- **WHEN** crea un evento con nombre, fechas y zonas válidas
- **THEN** el sistema guarda el evento con estado `draft` y lo asocia al proveedor

#### Scenario: Publicación de evento
- **GIVEN** un evento `draft` con zonas, precios y capacidad válidos
- **WHEN** el propietario o un administrador solicita publicarlo
- **THEN** el sistema cambia el estado a `published` y lo hace visible en el catálogo

#### Scenario: Retiro de evento por proveedor
- **GIVEN** un proveedor autenticado y un evento propio en estado `published`
- **WHEN** solicita retirar el evento
- **THEN** el sistema cambia el estado a `retired` y lo elimina de los listados públicos

### Requirement: Administración de zonas
El sistema SHALL permitir agregar y actualizar zonas de un evento, y SHALL impedir eliminar zonas que ya tengan inventario asignado.

#### Scenario: Agregar zona
- **GIVEN** un proveedor autenticado y un evento propio en estado `draft`
- **WHEN** agrega una zona con nombre y configuración válidos
- **THEN** el sistema la asocia al evento y la incluye en la gestión de disponibilidad

#### Scenario: Eliminar zona con inventario
- **GIVEN** una zona que ya tiene entradas asignadas
- **WHEN** un proveedor o administrador intenta eliminarla
- **THEN** el sistema rechaza la operación y conserva la zona y sus entradas

### Requirement: Precios por zona
El sistema SHALL mantener un precio no negativo por zona y SHALL indicar el precio en el detalle público de un evento publicado.

#### Scenario: Precio público
- **GIVEN** un evento publicado con zonas y precios configurados
- **WHEN** un visitante consulta el detalle del evento
- **THEN** el sistema muestra el precio vigente de cada zona sin exponer información interna de inventario

### Requirement: Capacidad y disponibilidad
El sistema SHALL controlar la capacidad total por zona y SHALL impedir que las asignaciones de entradas superen ese límite.

#### Scenario: Reserva de capacidad válida
- **GIVEN** una zona con inventario disponible
- **WHEN** una compra reserva una cantidad menor o igual al inventario disponible
- **THEN** el sistema confirma la reserva y reduce la disponibilidad

#### Scenario: Compra por encima de la capacidad
- **GIVEN** una zona sin inventario suficiente
- **WHEN** una compra intenta reservar más entradas de las disponibles
- **THEN** el sistema rechaza la compra completa sin asignar entradas parciales

### Requirement: Consulta del catálogo público
El sistema SHALL mostrar en el catálogo únicamente eventos `published` y SHALL permitir consultar el detalle de sus zonas, precios y disponibilidad.

#### Scenario: Catálogo de eventos publicados
- **GIVEN** eventos en estados `draft`, `published` y `retired`
- **WHEN** un visitante consulta el catálogo
- **THEN** el sistema muestra únicamente los eventos `published` con información pública válida

### Requirement: Autorización sobre eventos
El sistema SHALL permitir gestionar únicamente los eventos propios a los proveedores y SHALL reservar la gestión global a los administradores.

#### Scenario: Proveedor gestiona evento propio
- **GIVEN** un proveedor autenticado y un evento propio en estado editable
- **WHEN** solicita modificar sus zonas, precios o capacidad
- **THEN** el sistema aplica los cambios y conserva la pertenencia del evento

#### Scenario: Borrador no visible antes de publicar
- **GIVEN** un proveedor autenticado y un evento propio en estado `draft`
- **WHEN** un visitante consulta el catálogo público
- **THEN** el sistema no muestra el evento hasta que el proveedor lo publica

#### Scenario: Administrador gestiona cualquier evento
- **GIVEN** un administrador autenticado y un evento existente
- **WHEN** solicita editar o publicar el evento
- **THEN** el sistema permite la operación y registra la identidad administrativa
