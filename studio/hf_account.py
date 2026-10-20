"""hf_account.py -- signing in to Hugging Face for the gated models (Stable Video Diffusion, SAM 3,
DINOv3), from Settings > Studio > Models (user, 2026-10-20).

The token is stored where `hf auth login` stores it (huggingface_hub.login: HF_HOME/token), so
the installer, the `hf` command and Studio share one sign-in. Studio itself never writes it to
config/ or the log. A *read* token is enough.

Every call here goes over the network: run them off the UI thread. No Qt here.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

TOKENS_PAGE = "https://huggingface.co/settings/tokens"


def token() -> Optional[str]:
    try:
        from huggingface_hub import get_token
        return get_token()
    except Exception:
        return None


def whoami() -> Optional[str]:
    """The signed-in account's name; None when there's no token. Raises when Hugging Face can't be
    reached or turns the token down."""
    t = token()
    if not t:
        return None
    from huggingface_hub import HfApi
    return str(HfApi().whoami(token=t).get("name") or "")


def sign_in(new_token: str) -> str:
    """Check `new_token` and store it for this PC; the account's name. Raises ValueError with what
    to say when Hugging Face refuses it."""
    new_token = (new_token or "").strip()
    if not new_token:
        raise ValueError("Paste a token first.")
    from huggingface_hub import HfApi, login
    try:
        name = str(HfApi().whoami(token=new_token).get("name") or "")
    except Exception as e:
        if "401" in str(e) or "invalid" in str(e).lower():
            raise ValueError("Hugging Face didn't accept that token. Copy it again from your token settings.") from e
        raise
    login(token=new_token, add_to_git_credential=False)
    logger.info("Signed in to Hugging Face as %s.", name)
    return name


def sign_out() -> None:
    from huggingface_hub import logout
    try:
        logout()
    except Exception as e:      # nothing stored (a token from HF_TOKEN only)
        logger.info("Hugging Face sign-out: %s", e)


def access(repo_id: str) -> str:
    """For a gated repo: "ok" (this account may download it), "request" (ask for access on its page,
    or wait for the approval), "signin" (no account on this PC), or "" (couldn't tell: offline)."""
    if not token():
        return "signin"
    from huggingface_hub import HfApi
    try:
        from huggingface_hub.errors import GatedRepoError
    except Exception:  # older hub
        from huggingface_hub.utils import GatedRepoError  # type: ignore
    try:
        HfApi().auth_check(repo_id)
        return "ok"
    except GatedRepoError:
        return "request"
    except Exception as e:
        logger.debug("Could not check access to %s: %s", repo_id, e)
        return ""
