from __future__ import annotations

import sys
import keyring

SERVICE_NAME = "zotero-local-bilingual"


class SecretStore:
    def __init__(self):
        self._keyring = None

    def _store(self):
        if self._keyring is None:
            if sys.platform == "darwin":
                from keyring.backends.macOS import Keyring
                self._keyring = Keyring()
            elif sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring
                self._keyring = WinVaultKeyring()
            else:
                raise RuntimeError("当前系统尚未配置受支持的安全凭据存储")
        return self._keyring

    def set_api_key(self, profile_id: str, api_key: str) -> None:
        self._store().set_password(SERVICE_NAME, profile_id, api_key)

    def get_api_key(self, profile_id: str) -> str | None:
        return self._store().get_password(SERVICE_NAME, profile_id)

    def delete_api_key(self, profile_id: str) -> None:
        store = self._store()
        if store.get_password(SERVICE_NAME, profile_id) is None:
            return
        # A denied or locked Keychain must not look like a successful deletion.
        store.delete_password(SERVICE_NAME, profile_id)
