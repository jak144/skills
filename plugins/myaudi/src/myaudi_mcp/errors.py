"""Errors the server turns into readable tool errors."""


class AudiError(Exception):
    """Something went wrong talking to the myAudi services."""


class AuthenticationError(AudiError):
    """The login was refused, or needs the user to act first (wrong password, new terms, ...)."""


class LoginFlowError(AudiError):
    """The login pages did not look as expected: Audi probably changed them."""


class RateLimitError(AudiError):
    """The myAudi backend refuses requests because too many were sent."""


class VehicleSelectionError(AudiError):
    """No vehicle, or more than one, matches what was asked for."""
