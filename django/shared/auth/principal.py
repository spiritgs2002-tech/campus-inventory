from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    tenant_id: int
    user_id: int
    family_id: str
    role: str
    username: str
    name: str
    must_change_password: bool
