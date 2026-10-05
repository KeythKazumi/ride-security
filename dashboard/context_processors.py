import os

from django.conf import settings

_MAIN_CSS = settings.BASE_DIR / "static" / "css" / "main.css"


def static_version(request):
    """Expose the stylesheet's mtime so its URL changes whenever the file does.

    Browsers otherwise hold on to a cached main.css after a deploy or an edit
    and render new markup with old rules.
    """
    try:
        version = int(os.stat(_MAIN_CSS).st_mtime)
    except OSError:
        version = 0
    return {"static_version": version}
