class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class IdentityRequired(DomainError):
    status_code = 422
    code = "identity_required"
