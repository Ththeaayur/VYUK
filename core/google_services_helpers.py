"""Part of the core/google_services.py split (B25): google_services_helpers. Move only — see core/google_services.py for the facade."""



GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"


_LIBS_MISSING = (
    "Google sign-in isn't available in this build of VYUK — "
    "the Google sign-in part didn't make it into the package."
)


_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _short(exc: Exception) -> str:
    """A compact parenthetical for failure strings — enough for the
    GUI tooltip / log layer, short enough to speak around."""
    text = str(exc).strip() or type(exc).__name__
    return text[:120]
