"""Google OAuth for the Photos Picker API (installed-app loopback flow). The user brings credentials.json."""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/photospicker.mediaitems.readonly"]


def config_dir() -> Path:
    env = os.environ.get("PHOTOSORTER_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "photosorter"
    return Path.home() / ".config" / "photosorter"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def token_path() -> Path:
    return config_dir() / "token.json"


def write_private(path: Path, text: str) -> None:
    """Create with mode 0600 from the start (no window where it is world-readable). Windows ignores the mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        os.chmod(path, 0o600)  # also tightens a file that already existed with wider rights
    except OSError:
        pass


def authorize(credentials_file: Path | None = None) -> Path:
    """One-time browser consent; stores the token. Returns the token path."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    cred = credentials_file or credentials_path()
    if not cred.is_file():
        for alt in (Path("credentials.json"),):
            if alt.is_file():
                cred = alt
                break
        else:
            raise SystemExit(f"credentials.json not found. Put it at {credentials_path()} (see README, Google Cloud setup) "
                             "or pass --credentials PATH.")
    flow = InstalledAppFlow.from_client_secrets_file(str(cred), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    write_private(token_path(), creds.to_json())
    return token_path()


def load_credentials():
    """Valid, refreshed credentials from the stored token."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tp = token_path()
    if not tp.is_file():
        raise SystemExit("Not signed in to Google yet: run `photosorter auth-google` first.")
    creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
    if not creds.valid:
        if creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:  # message may hold server detail; keep only the type
                raise SystemExit(f"Google sign-in expired or was revoked ({type(e).__name__}): run `photosorter auth-google` again.") from None
            write_private(tp, creds.to_json())
        else:
            raise SystemExit("Google sign-in expired: run `photosorter auth-google` again.")
    return creds
