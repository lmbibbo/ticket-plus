# Spec Delta

## ADDED Requirements

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
