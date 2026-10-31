from shared.auth.backends import authorize_claims
from shared.auth.tokens import decode_access
from shared.errors import BusinessError


class IdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.principal = None
        request.auth_error = None
        # Login/refresh inspect their own credentials, including rotated refresh tokens.
        if not request.path.startswith(("/api/v1/auth/", "/auth/", "/health/")):
            try:
                header = request.headers.get("Authorization", "")
                if header:
                    if not header.startswith("Bearer "):
                        raise BusinessError("UNAUTHENTICATED", "登录已失效", 401)
                    claims = decode_access(header[7:])
                else:
                    claims = request.session.get("identity")
                if claims:
                    request.principal = authorize_claims(claims)
            except BusinessError as error:
                request.auth_error = error
        return self.get_response(request)
