import bcrypt

from app.application.ports import PasswordHasher


class BcryptHasher(PasswordHasher):
    def __init__(self, rounds: int = 12) -> None:
        self._rounds = rounds
        self._dummy = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds))

    def hash(self, password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(self._rounds)).decode()

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode(), password_hash.encode())
        except ValueError:
            return False

    def verify_dummy(self, password: str) -> None:
        bcrypt.checkpw(password.encode()[:72], self._dummy)
