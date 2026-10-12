from __future__ import annotations

import keyring

SERVICE_NAME = "zotero-local-bilingual"


class SecretStore:
    def set_api_key(self, profile_id: str, api_key: str) -> None:
        keyring.set_password(SERVICE_NAME, profile_id, api_key)

    def get_api_key(self, profile_id: str) -> str | None:
        return keyring.get_password(SERVICE_NAME, profile_id)

    def delete_api_key(self, profile_id: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, profile_id)
        except keyring.errors.PasswordDeleteError:
            pass
