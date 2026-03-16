# Regression route coverage map

This map links project-owned routes to regression tests.

## Public routes

| Route             | Purpose                 | Covered by                                                                                       |
| ----------------- | ----------------------- | ------------------------------------------------------------------------------------------------ |
| `/`               | Homepage rendering      | `TestPublicRoutes.test_core_routes_status_and_templates`, `TestPublicRoutes.test_html_snapshots` |
| `/reviews`        | Redirect to search      | `TestPublicRoutes.test_core_routes_status_and_templates`                                         |
| `/reviews/<slug>` | Review detail rendering | `TestPublicRoutes.test_core_routes_status_and_templates`                                         |
| `/issues`         | Issue list rendering    | `TestPublicRoutes.test_core_routes_status_and_templates`, `TestPublicRoutes.test_html_snapshots` |
| `/issues/latest`  | Latest issue redirect   | `TestPublicRoutes.test_core_routes_status_and_templates`                                         |
| `/issues/<slug>`  | Issue detail rendering  | `TestPublicRoutes.test_core_routes_status_and_templates`                                         |
| `/tags`           | Tag list rendering      | `TestPublicRoutes.test_core_routes_status_and_templates`, `TestPublicRoutes.test_html_snapshots` |
| `/tags/<slug>`    | Tag detail rendering    | `TestPublicRoutes.test_core_routes_status_and_templates`                                         |
| `/search?q=...`   | Search rendering        | `TestPublicRoutes.test_core_routes_status_and_templates`, `TestPublicRoutes.test_html_snapshots` |
| `/about`          | Author list rendering   | `TestPublicRoutes.test_core_routes_status_and_templates`                                         |
| `/about/<slug>`   | Author detail rendering | `TestPublicRoutes.test_core_routes_status_and_templates`                                         |
| `/ajax/tags`      | Tag API JSON contract   | `TestPublicRoutes.test_core_routes_status_and_templates`                                         |

## Newsletter + analytics routes

| Route                             | Purpose                            | Covered by                                                                                                |
| --------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `/newsletter/subscribe`           | HTMX guard and form rendering      | `TestPublicRoutes.test_core_routes_status_and_templates`, `TestNewsletterFlows.test_subscribe_flow_htmx`  |
| `/newsletter/success`             | Success page rendering             | `TestPublicRoutes.test_core_routes_status_and_templates`                                                  |
| `/newsletter/unsubscribe/<token>` | Unsubscribe flow                   | `TestNewsletterFlows.test_unsubscribe_get_and_post`, `TestNewsletterFlows.test_unsubscribe_invalid_token` |
| `/analytics/pixel.png`            | Open tracking pixel                | `TestPublicRoutes.test_analytics_routes`                                                                  |
| `/analytics/link`                 | Email click tracking redirect      | `TestPublicRoutes.test_analytics_routes`                                                                  |
| `/analytics/link/<token>`         | Newsletter click tracking redirect | `TestPublicRoutes.test_analytics_routes`                                                                  |

## User routes

| Route               | Purpose                            | Covered by                                                                                                       |
| ------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `/users/~redirect/` | Login required + redirect behavior | `TestUserRoutes.test_users_redirect_requires_login`, `TestUserRoutes.test_users_redirect_for_authenticated_user` |
| `/users/<pk>/`      | Self-only access guard             | `TestUserRoutes.test_user_detail_only_for_self`                                                                  |
| `/users/~update/`   | Authenticated update page          | `TestUserRoutes.test_user_update_page_for_authenticated_user`                                                    |

## Backend routes

| Route                                               | Purpose                                       | Covered by                                                                                                                                                             |
| --------------------------------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/backend/`                                         | Auth + permission guard + page render         | `TestBackendRoutes.test_backend_routes_require_login`, `TestBackendRoutes.test_backend_manage_csv_permission_guard`, `TestBackendRoutes.test_backend_superuser_access` |
| `/backend/subscribers/upload`                       | CSV upload form + preview                     | `TestBackendRoutes.test_backend_routes_require_login`, `TestBackendRoutes.test_backend_superuser_access`, `TestBackendWorkflows.test_upload_subscriber_csv_preview`    |
| `/backend/subscribers/upload/change-header/<token>` | HTMX-only header edit                         | `TestBackendRoutes.test_htmx_only_backend_endpoints`, `TestBackendWorkflows.test_edit_csv_header_with_htmx`                                                            |
| `/backend/subscribers/upload/process-csv/<token>`   | HTMX-only process submit + queue side effect  | `TestBackendRoutes.test_htmx_only_backend_endpoints`, `TestBackendWorkflows.test_process_csv_sets_confirmed_and_queues_task`                                           |
| `/backend/newsletter/send/<token>`                  | Final send confirmation page                  | `TestBackendRoutes.test_backend_routes_require_login`, `TestBackendRoutes.test_backend_superuser_access`                                                               |
| `/backend/newsletter/send/confirm/<token>`          | Final send execution page + queue side effect | `TestBackendRoutes.test_htmx_only_backend_endpoints`, `TestBackendWorkflows.test_send_final_newsletter_queues_task_when_ready`                                         |
| `/backend/newsletter/stats`                         | Stats list page                               | `TestBackendRoutes.test_backend_routes_require_login`, `TestBackendRoutes.test_backend_superuser_access`                                                               |
| `/backend/newsletter/stats/<pk>`                    | Stats detail rendering + calculations         | `TestBackendRoutes.test_backend_routes_require_login`, `TestBackendRoutes.test_backend_superuser_access`, `TestBackendWorkflows.test_newsletter_stats_detail_math`     |

## Optional integration marker

| Check       | Purpose                                 | Covered by                                                             |
| ----------- | --------------------------------------- | ---------------------------------------------------------------------- |
| MailHog API | Validate test runtime can reach MailHog | `TestNewsletterFlows.test_mailhog_api_is_reachable` (`mailhog` marker) |
