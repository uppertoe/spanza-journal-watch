from urllib.parse import urljoin

import requests
from django.conf import settings


class PlankaAPIError(Exception):
    pass


class PlankaClient:
    DEFAULT_TIMEOUT_SECONDS = 15

    def __init__(self):
        self.base_url = (getattr(settings, "PLANKA_BASE_URL", "") or "").strip().rstrip("/")
        self.api_key = (getattr(settings, "PLANKA_API_KEY", "") or "").strip()
        self.access_token = (getattr(settings, "PLANKA_ACCESS_TOKEN", "") or "").strip()
        self.timeout = int(getattr(settings, "PLANKA_TIMEOUT_SECONDS", self.DEFAULT_TIMEOUT_SECONDS))

    @property
    def configured(self):
        return bool(self.base_url and (self.api_key or self.access_token))

    def _headers(self):
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _request(self, method, path, *, params=None, json=None):
        if not self.base_url:
            raise PlankaAPIError("PLANKA_BASE_URL is not configured.")
        if not (self.api_key or self.access_token):
            raise PlankaAPIError("Configure PLANKA_API_KEY or PLANKA_ACCESS_TOKEN.")

        url = urljoin(f"{self.base_url}/", f"api/{path.lstrip('/')}")
        response = requests.request(
            method=method,
            url=url,
            headers=self._headers(),
            params=params,
            json=json,
            timeout=self.timeout,
        )

        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                detail = payload.get("message") or payload.get("code") or str(payload)
            except Exception:
                detail = response.text
            raise PlankaAPIError(f"Planka API {response.status_code}: {detail}")

        if not response.content:
            return {}

        return response.json()

    def create_project(self, name):
        payload = self._request("POST", "/projects", json={"type": "private", "name": name})
        return payload["item"]

    def create_board(self, project_id, name="Reviews", position=65536):
        payload = self._request(
            "POST",
            f"/projects/{project_id}/boards",
            json={"position": position, "name": name},
        )
        return payload["item"]

    def create_list(self, board_id, name, position, list_type="active"):
        payload = self._request(
            "POST",
            f"/boards/{board_id}/lists",
            json={"type": list_type, "position": position, "name": name},
        )
        return payload["item"]

    def create_custom_field_group(self, board_id, name="Journal Watch Review Card", position=65536):
        payload = self._request(
            "POST",
            f"/boards/{board_id}/custom-field-groups",
            json={"position": position, "name": name},
        )
        return payload["item"]

    def create_custom_field(self, custom_field_group_id, name, position, show_on_front=False):
        payload = self._request(
            "POST",
            f"/custom-field-groups/{custom_field_group_id}/custom-fields",
            json={"position": position, "name": name, "showOnFrontOfCard": show_on_front},
        )
        return payload["item"]

    def get_board(self, board_id):
        payload = self._request("GET", f"/boards/{board_id}")
        return payload.get("item", {}), payload.get("included", {})

    def move_card(self, card_id, list_id, position=65536):
        payload = self._request("PATCH", f"/cards/{card_id}", json={"listId": list_id, "position": position})
        return payload.get("item", {})
