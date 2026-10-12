"""Shared exceptions."""


class FailSafeTriggered(RuntimeError):
    """The user moved the cursor into a screen corner to abort automation."""

    def __init__(self, detail: str = "") -> None:
        message = (
            "Fail-safe triggered: the mouse cursor is in a corner of the primary screen, "
            "which aborts all automated input. Move the cursor away from the corner to continue"
            " (or disable it with safety.failsafe=false)."
        )
        super().__init__(f"{message} {detail}".strip())
