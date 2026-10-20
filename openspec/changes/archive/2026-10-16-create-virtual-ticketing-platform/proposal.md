# Proposal

## Why

El repositorio solo documenta la intención de crear una ticketera, pero no existe un sistema que permita operar eventos, vender entradas virtuales sin sobreventa ni duplicados y auditar cada petición. Se necesita una base segura y verificable, con una API Python y una interfaz web servidor-renderizada para los tres roles.

## What Changes

- Se crea un backend Python con FastAPI y una aplicación frontend Flask separados, ambos conectados a una API REST versionada.
- Se incorpora autenticación con JWT de acceso y refresh tokens revocables, junto con autorización para los roles `admin`, `event-owner` y `client`.
- Se construye una interfaz web renderizada en servidor para que cada rol vea únicamente las operaciones permitidas: administración de usuarios, gestión de eventos, catálogo, compra y consulta de entradas.
- Se permite que un cliente se registre inicialmente, que un administrador apruebe cuentas de `event-owner` y que los administradores gestionen usuarios y eventos.
- Se permite que los proveedores publiquen y gestionen eventos, zonas, precios, capacidad y disponibilidad; los clientes pueden consultar y comprar eventos publicados.
- Se implementa la compra con pago simulado, idempotencia de solicitud y asignación transaccional para impedir pedidos o entradas duplicados y sobreventa.
- Cada zona mantiene una numeración secuencial independiente; cada entrada virtual incluye evento, zona, número, propietario y payload QR verificable.
- Se agrega una bitácora HTTP estructurada en backend y frontend, con correlación de petición, resultado, duración y datos sensibles omitidos o redactados.
- Quedan fuera de este cambio el cobro de dinero real, reembolsos, la validación de check-in, las notificaciones y las aplicaciones SPA separadas.

## Capabilities

### New Capabilities

- `identity-access`: Registro, aprobación de proveedores, autenticación JWT, renovación y revocación de sesiones, y autorización por rol.
- `event-management`: Ciclo de vida, zonas, precios, capacidad y consulta de eventos administrados por proveedores o administradores.
- `ticket-sales`: Compra idempotente con pago simulado, asignación concurrente sin duplicados ni sobreventa, entradas virtuales QR y consulta del cliente.
- `web-frontend`: Interfaz Flask renderizada en servidor para catálogo, autenticación, administración, gestión de eventos, compras y entradas según el rol.
- `http-observability`: Bitácora HTTP estructurada y correlacionada en frontend y backend, con tiempos, resultados y protección de datos sensibles.

### Modified Capabilities

Ninguna.

## Impact

- Se inicializarán dos aplicaciones Python separadas: backend FastAPI y frontend Flask, con configuración y contratos de integración propios.
- La base de datos PostgreSQL almacenará usuarios, refresh tokens, eventos, zonas, inventario, pedidos y entradas; solo el backend accederá a las tablas de dominio, mientras Flask mantendrá un almacén de sesión web aislado sin consultar datos de dominio.
- La API REST añadirá autenticación, administración de usuarios, gestión de eventos, catálogo público, compras y consulta de entradas.
- Flask renderizará las vistas y delegará las operaciones de dominio al backend, sin duplicar reglas de negocio ni acceso directo a datos.
- El sistema de logging producirá registros estructurados y correlacionados hacia la salida de ambas aplicaciones; no se integra un proveedor externo de observabilidad.
- No habrá cambios de compatibilidad porque no existen capacidades, código de aplicación ni contratos previos.

## Rollback Plan

1. Retirar el tráfico de la aplicación web y detener el backend antes de desplegar una versión anterior o retirar el cambio greenfield.
2. Revertir la rama y el despliegue de `feature/create-virtual-ticketing-platform` sin aplicar commits directamente a `main`.
3. En una base de datos descartable, usar `alembic downgrade` para volver al esquema anterior. En una base con datos, conservar el backup y no ejecutar una reversión destructiva: detener el servicio y aplicar una corrección compatible.
4. Revocar sesiones activas, claves de firma JWT y claves de QR, y rotarlas antes de reiniciar cualquier versión conservada.
5. Verificar mediante comprobaciones de salud y pruebas de humo que frontend y backend regresen a un estado consistente; confirmar que ninguna reversión confunde datos de prueba con datos de producción.
