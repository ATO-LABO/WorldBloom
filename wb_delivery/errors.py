class DeliveryError(Exception):
    """A stable error code suitable for state handling; no API bodies or secrets."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(code + (": " + detail if detail else ""))
