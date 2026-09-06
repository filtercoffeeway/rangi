class ErrNotFound(Exception):
    def __init__(self, msg: str = "not found"):
        super().__init__(msg)


class ErrNotAuthorized(Exception):
    """The mandate exists but is not in a state that permits dialing.
    Dialing on a stale mandate is the failure this prevents.
    """

    def __init__(self, msg: str = "mandate is not authorized"):
        super().__init__(msg)


class ErrExpired(Exception):
    def __init__(self, msg: str = "mandate has expired"):
        super().__init__(msg)
