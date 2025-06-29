from fastapi import HTTPException, status

class ResourceNotFound(HTTPException):
    def __init__(self, detail: str = "Resource not found."):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

class OperationFailed(HTTPException):
    def __init__(self, detail: str = "Operation failed."):
        super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)

class BadRequest(HTTPException):
    def __init__(self, detail: str = "Bad request."):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

class Unauthorized(HTTPException):
    def __init__(self, detail: str = "Not authenticated."):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

class Forbidden(HTTPException):
    def __init__(self, detail: str = "Not authorized to perform this action."):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
