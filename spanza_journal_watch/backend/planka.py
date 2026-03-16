from urllib.parse import urljoin

import requests
from django.conf import settings


class PlankaAPIError(Exception):
    pass


class PlankaClient:
    DEFAULT_TIMEOUT_SECONDS = 15

    def __init__(self, *, base_url=None, api_key=None, access_token=None, timeout=None):
        default_base_url = (getattr(settings, "PLANKA_BASE_URL", "") or "").strip().rstrip("/")
        default_api_key = (getattr(settings, "PLANKA_API_KEY", "") or "").strip()
        default_access_token = (getattr(settings, "PLANKA_ACCESS_TOKEN", "") or "").strip()
        default_timeout = int(getattr(settings, "PLANKA_TIMEOUT_SECONDS", self.DEFAULT_TIMEOUT_SECONDS))

        self.base_url = (base_url if base_url is not None else default_base_url).strip().rstrip("/")
        self.api_key = (api_key if api_key is not None else default_api_key).strip()
        self.access_token = (access_token if access_token is not None else default_access_token).strip()
        self.timeout = int(timeout if timeout is not None else default_timeout)

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

    def _request(self, method, path, *, params=None, json=None, data=None, files=None, requires_auth=True):
        if not self.base_url:
            raise PlankaAPIError("PLANKA_BASE_URL is not configured.")
        if requires_auth and not (self.api_key or self.access_token):
            raise PlankaAPIError("Configure PLANKA_API_KEY or PLANKA_ACCESS_TOKEN.")

        url = urljoin(f"{self.base_url}/", f"api/{path.lstrip('/')}")
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._headers(),
                params=params,
                json=json,
                data=data,
                files=files,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise PlankaAPIError(f"Could not connect to Planka at {self.base_url}: {error}") from error

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

        try:
            return response.json()
        except ValueError as error:
            raise PlankaAPIError(f"Planka API returned a non-JSON response for {path}: {error}")

    def get_bootstrap(self):
        try:
            payload = self._request("GET", "/bootstrap", requires_auth=False)
        except PlankaAPIError as error:
            if "404" in str(error):
                return {}
            raise
        return payload.get("item", {})

    def create_access_token(self, email_or_username, password):
        payload = self._request(
            "POST",
            "/access-tokens",
            json={"emailOrUsername": email_or_username, "password": password},
            requires_auth=False,
        )
        return payload.get("item")

    def exchange_access_token_with_oidc(self, code, nonce):
        last_error = None
        for path in ("/access-tokens/exchange-using-oidc", "/access-tokens/exchange-with-oidc"):
            try:
                payload = self._request(
                    "POST",
                    path,
                    json={"code": code, "nonce": nonce},
                    requires_auth=False,
                )
                return payload.get("item")
            except PlankaAPIError as error:
                last_error = error
                if "404" not in str(error):
                    raise

        raise PlankaAPIError(
            "This Planka instance does not expose OIDC token exchange. Use username/password mode."
        ) from last_error

    def get_current_user(self):
        payload = self._request("GET", "/users/me")
        return payload.get("item", {})

    def create_user_api_key(self, user_id):
        payload = self._request("POST", f"/users/{user_id}/api-key")
        included = payload.get("included", {}) or {}
        api_key = included.get("apiKey")
        if not api_key:
            raise PlankaAPIError("Planka API did not return an API key.")
        return api_key

    def create_project(self, name):
        payload = self._request("POST", "/projects", json={"type": "private", "name": name})
        return payload["item"]

    def update_project_name(self, project_id, name):
        payload = self._request("PATCH", f"/projects/{project_id}", json={"name": name})
        return payload.get("item", {})

    def update_project_background(self, project_id, *, background_type="image", background_image_id=None):
        payload = {
            "backgroundType": background_type,
        }
        if background_image_id:
            payload["backgroundImageId"] = background_image_id

        result = self._request("PATCH", f"/projects/{project_id}", json=payload)
        return result.get("item", {})

    def upload_project_background_image(
        self, project_id, file_obj, filename="background.webp", content_type="image/webp"
    ):
        payload = self._request(
            "POST",
            f"/projects/{project_id}/background-images",
            files={"file": (filename, file_obj, content_type)},
        )
        return payload.get("item", {})

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

    def create_card(self, list_id, name, description="", position=65536, card_type="story"):
        payload = self._request(
            "POST",
            f"/lists/{list_id}/cards",
            json={
                "type": card_type,
                "position": position,
                "name": name,
                "description": description,
            },
        )
        return payload.get("item", {})

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
