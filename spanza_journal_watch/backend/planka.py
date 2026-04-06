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

    def get_project(self, project_id):
        payload = self._request("GET", f"/projects/{project_id}")
        return payload.get("item", {})

    def create_project(self, name):
        payload = self._request("POST", "/projects", json={"type": "shared", "name": name})
        return payload["item"]

    def update_project_name(self, project_id, name):
        payload = self._request("PATCH", f"/projects/{project_id}", json={"name": name})
        return payload.get("item", {})

    def make_project_shared(self, project_id):
        """Remove owner restriction so admins and added members can access the project."""
        payload = self._request(
            "PATCH",
            f"/projects/{project_id}",
            json={"ownerProjectManagerId": None},
        )
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

    def update_list(self, list_id, *, name=None, color=None, position=None):
        payload = {}
        if name is not None:
            payload["name"] = name
        if color is not None:
            payload["color"] = color
        if position is not None:
            payload["position"] = position
        if not payload:
            return {}

        result = self._request("PATCH", f"/lists/{list_id}", json=payload)
        return result.get("item", {})

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

    def create_custom_field_value(self, card_id, custom_field_group_id, custom_field_id, content=""):
        payload = self._request(
            "PATCH",
            (
                f"/cards/{card_id}/custom-field-values/"
                f"customFieldGroupId:{custom_field_group_id}:customFieldId:{custom_field_id}"
            ),
            json={"content": str(content or "")},
        )
        return payload.get("item", {})

    def create_label(self, board_id, name, color="berry-red", position=65536):
        payload = self._request(
            "POST",
            f"/boards/{board_id}/labels",
            json={"name": name, "color": color, "position": position},
        )
        return payload.get("item", {})

    def update_label(self, label_id, *, name=None, color=None, position=None):
        payload = {}
        if name is not None:
            payload["name"] = name
        if color is not None:
            payload["color"] = color
        if position is not None:
            payload["position"] = position
        if not payload:
            return {}

        result = self._request("PATCH", f"/labels/{label_id}", json=payload)
        return result.get("item", {})

    def add_label_to_card(self, card_id, label_id):
        payload = self._request("POST", f"/cards/{card_id}/card-labels", json={"labelId": label_id})
        return payload.get("item", {})

    def get_board(self, board_id):
        payload = self._request("GET", f"/boards/{board_id}")
        return payload.get("item", {}), payload.get("included", {})

    def move_card(self, card_id, list_id, position=65536):
        payload = self._request("PATCH", f"/cards/{card_id}", json={"listId": list_id, "position": position})
        return payload.get("item", {})

    def get_card(self, card_id):
        payload = self._request("GET", f"/cards/{card_id}")
        return payload.get("item", {})

    def get_card_members(self, card_id):
        """Return (memberships, users_by_id) for a card.

        memberships  — list of cardMembership dicts {userId, cardId, ...}
        users_by_id  — dict mapping userId → user dict (email, name, etc.)

        Note: the card endpoint only includes users referenced by certain card
        fields (e.g. creatorUserId), not necessarily card members.  If any
        member userId is absent from included.users we fall back to list_users().
        """
        payload = self._request("GET", f"/cards/{card_id}")
        included = payload.get("included") or {}
        memberships = included.get("cardMemberships") or []
        users = included.get("users") or []
        users_by_id = {str(u.get("id") or ""): u for u in users if u.get("id")}

        member_ids = {str(m.get("userId") or "") for m in memberships if m.get("userId")}
        missing = member_ids - set(users_by_id)
        if missing:
            for u in self.list_users():
                uid = str(u.get("id") or "")
                if uid in missing:
                    users_by_id[uid] = u

        return memberships, users_by_id

    def get_card_description_editor_ids(self, card_id):
        """Return user IDs who edited the card description, most-recent first."""
        payload = self._request("GET", f"/cards/{card_id}/actions")
        actions = payload.get("items") or []
        editor_ids = []
        for action in actions:
            if action.get("type") != "updateCard":
                continue
            data = action.get("data") or {}
            card_data = data.get("card") or {}
            if "description" not in card_data:
                continue
            uid = str(action.get("userId") or "").strip()
            if uid and uid not in editor_ids:
                editor_ids.append(uid)
        return editor_ids

    def delete_card(self, card_id):
        self._request("DELETE", f"/cards/{card_id}")
        return True

    # User management

    def list_users(self):
        payload = self._request("GET", "/users")
        return payload.get("items", [])

    def find_user_by_email(self, email):
        email = (email or "").strip().lower()
        for user in self.list_users():
            if (user.get("email") or "").strip().lower() == email:
                return user
        return None

    def create_user(self, email, name):
        import re
        import secrets

        raw_prefix = email.split("@")[0].lower()
        username = re.sub(r"[^a-z0-9_.]", "_", raw_prefix).strip("_.") or "user"
        username = username[:48]

        payload = self._request(
            "POST",
            "/users",
            json={
                "email": email,
                "name": name,
                "password": secrets.token_urlsafe(32),
                "username": username,
                "role": "boardUser",
            },
        )
        return payload.get("item", {})

    def create_user_via_db(self, email, name, role="boardUser"):
        """Create a Planka user directly in the database.

        Required when OIDC_ENFORCED=true blocks POST /api/users.
        The user will be linked to their OIDC identity on first SSO login
        via Planka's get-or-create-one-with-oidc helper (matches by email).
        """
        import re
        from datetime import datetime, timezone

        import psycopg2

        db_url = (getattr(settings, "PLANKA_DB_URL", "") or "").strip()
        if not db_url:
            raise PlankaAPIError("PLANKA_DB_URL is not configured.")

        raw_prefix = email.split("@")[0].lower()
        username = re.sub(r"[^a-z0-9_.]", "_", raw_prefix).strip("_.") or "user"
        username = username[:48]

        try:
            conn = psycopg2.connect(db_url)
        except Exception as error:
            raise PlankaAPIError(f"Could not connect to Planka database: {error}") from error

        try:
            with conn:
                with conn.cursor() as cur:
                    # Ensure username is unique — append suffix if needed
                    cur.execute("SELECT id FROM user_account WHERE username = %s", (username,))
                    if cur.fetchone():
                        base = username[:44]
                        for i in range(1, 1000):
                            candidate = f"{base}_{i}"
                            cur.execute("SELECT id FROM user_account WHERE username = %s", (candidate,))
                            if not cur.fetchone():
                                username = candidate
                                break

                    now = datetime.now(timezone.utc)
                    cur.execute(
                        """
                        INSERT INTO user_account (
                            id, email, name, username, role,
                            is_sso_user, is_deactivated,
                            subscribe_to_own_cards, subscribe_to_card_when_commenting,
                            turn_off_recent_card_highlighting, enable_favorites_by_default,
                            default_editor_mode, default_home_view, default_projects_order,
                            created_at, updated_at
                        ) VALUES (
                            next_id(), %s, %s, %s, %s,
                            true, false,
                            false, true,
                            false, true,
                            'wysiwyg', 'groupedProjects', 'byDefault',
                            %s, %s
                        )
                        RETURNING id
                        """,
                        (email, name, username, role, now, now),
                    )
                    user_id = cur.fetchone()[0]
        except psycopg2.IntegrityError as error:
            raise PlankaAPIError(f"User with email {email} may already exist: {error}") from error
        except Exception as error:
            raise PlankaAPIError(f"Failed to create Planka user via DB: {error}") from error
        finally:
            conn.close()

        return {"id": user_id, "email": email, "name": name, "username": username, "role": role}

    def update_user(self, user_id, name):
        payload = self._request("PATCH", f"/users/{user_id}", json={"name": name})
        return payload.get("item", {})

    def set_user_role(self, user_id, role):
        """Set Planka global role: 'admin' or 'boardUser'."""
        payload = self._request("PATCH", f"/users/{user_id}", json={"role": role})
        return payload.get("item", {})

    # Board membership

    def add_board_member(self, board_id, user_id, role="editor"):
        payload = self._request(
            "POST",
            f"/boards/{board_id}/board-memberships",
            json={"userId": user_id, "role": role},
        )
        return payload.get("item", {})

    def remove_board_member(self, membership_id):
        self._request("DELETE", f"/board-memberships/{membership_id}")
        return True

    def find_board_membership(self, board_id, user_id):
        """Return the board membership dict for user_id, or None."""
        try:
            payload = self._request("GET", f"/boards/{board_id}")
        except PlankaAPIError as error:
            if "404" in str(error):
                return None
            raise
        for m in (payload.get("included") or {}).get("boardMemberships", []):
            if str(m.get("userId")) == str(user_id):
                return m
        return None

    # Webhooks

    def list_webhooks(self):
        payload = self._request("GET", "/webhooks")
        return payload.get("items") or []

    def create_webhook(self, url, *, name="Journal Watch", events=None, access_token=None):
        """
        Register a global webhook. Planka webhooks are not board-scoped via the API.
        ``events`` is a list of event names; defaults to cardUpdate/cardCreate/cardDelete.
        ``access_token`` is sent by Planka as Authorization: Bearer <token> on each delivery.
        Returns the webhook item dict.
        Note: events is sent as a comma-separated string as required by the Planka API.
        """
        if events is None:
            events = ["cardUpdate", "cardCreate", "cardDelete"]
        body = {"name": name, "url": url, "events": ",".join(events)}
        if access_token:
            body["accessToken"] = access_token
        payload = self._request("POST", "/webhooks", json=body)
        return payload.get("item", {})

    def delete_webhook(self, webhook_id):
        """Delete a webhook by its Planka ID. Silently ignores 404."""
        try:
            self._request("DELETE", f"/webhooks/{webhook_id}")
        except PlankaAPIError as exc:
            if "404" in str(exc):
                return False
            raise
        return True
