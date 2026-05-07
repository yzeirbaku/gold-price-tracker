import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.auth import require_api_key


def test_require_api_key_passes_when_header_matches_env() -> None:
    with patch.dict(os.environ, {"API_KEY": "secret"}):
        # Should not raise
        require_api_key(x_api_key="secret")


def test_require_api_key_rejects_missing_header() -> None:
    with patch.dict(os.environ, {"API_KEY": "secret"}):
        with pytest.raises(HTTPException) as exc:
            require_api_key(x_api_key=None)
        assert exc.value.status_code == 401


def test_require_api_key_rejects_wrong_header() -> None:
    with patch.dict(os.environ, {"API_KEY": "secret"}):
        with pytest.raises(HTTPException) as exc:
            require_api_key(x_api_key="wrong")
        assert exc.value.status_code == 401


def test_require_api_key_fails_closed_when_env_missing() -> None:
    # If API_KEY isn't set, every request must fail (don't allow open access)
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(HTTPException) as exc:
            require_api_key(x_api_key="anything")
        assert exc.value.status_code == 500
