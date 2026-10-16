"""Codigos de negocio estables del contrato `application/problem+json`.

El texto visible puede localizarse o cambiarse; el `code` no. Flask lo usa
para decidir que formulario o fragmento mostrar, asi que un cambio aqui es un
cambio de contrato.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Codigos que viajan en el campo `code` de un problema."""

    # Validacion y transporte
    VALIDATION_ERROR = "validation_error"
    MALFORMED_REQUEST = "malformed_request"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    #: La peticion es valida pero no aplica al estado actual del recurso:
    #: aprobar un proveedor que ya esta activo, suspender una cuenta dada de
    #: baja. Es 409 y no 422 porque el problema no son los datos, sino cuando se
    #: envia la peticion.
    INVALID_STATE_TRANSITION = "invalid_state_transition"

    # Autenticacion y autorizacion
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"

    # Identidad
    EMAIL_ALREADY_REGISTERED = "email_already_registered"
    ACCOUNT_NOT_ACTIVE = "account_not_active"
    INVALID_CREDENTIALS = "invalid_credentials"
    # No es una credencial: es el codigo estable que la web usa para distinguir
    # un refresh reutilizado de un refresh simplemente expirado.
    REFRESH_TOKEN_REUSED = "refresh_token_reused"  # noqa: S105
    SESSION_REVOKED = "session_revoked"

    # Eventos
    EVENT_NOT_PUBLISHABLE = "event_not_publishable"
    ZONE_HAS_TICKETS = "zone_has_tickets"
    FORBIDDEN_NOT_OWNER = "forbidden_not_owner"

    # Ventas
    INVENTORY_UNAVAILABLE = "inventory_unavailable"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    PAYMENT_DECLINED = "payment_declined"

    # Infraestructura
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    INTERNAL_ERROR = "internal_error"
