.. _coordinator:

Regional Coordinator Guide
==========================

Regional coordinators are assigned to specific issues by the chief editor.
Your role is to help manage article intake and contributors for the issues
assigned to you. You have access to the backend editorial interface for those
issues only.

Logging in
----------

Go to ``/backend/`` and sign in with your account. After logging in you land on
the **coordinator dashboard**, which shows a card for each issue you have been
assigned to.

If you have not yet been assigned to an issue, the dashboard will be empty.
Contact the chief editor to be assigned.

On each issue card you will see:

- The issue name and date
- The current status (Live or Draft)
- **Articles** — jump to Article Intake for this issue
- **Reviewers** — jump to the Contributors list for this issue

You can also choose to go directly to the Planka board from the
**Go to backend / Go to Planka** landing page (``/backend/go/``).


Article intake
--------------

Click **Articles** on an issue card to open Article Intake for that issue.
You can also access it via the top navigation if the issue is pre-selected.

Article Intake has three stages:

Stage 1 — Fetch from PubMed
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Select the date range and choose the journals to search. Click **Fetch from
PubMed**. The fetch runs in the background and results appear once it completes.

Stage 2 — Stage candidates
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The results table shows all articles returned from PubMed. Use the filters
(text search, journal, specialty toggles) to narrow the list to the articles
that are relevant to your region or specialty.

Tick the **Staged** checkbox on each article you want to send to the Planka
board. Use **Bulk select / unselect** to stage or unstage everything matching
the current filters at once.

Stage 3 — Push to Planka
~~~~~~~~~~~~~~~~~~~~~~~~~

Click **Push staged articles to Planka candidates**. Each staged article
becomes a card in the *Candidates* list on the Planka board, ready for
reviewers to pick up.

Use **Reconcile Planka status** at any time to check which articles are still
in the Candidates list and which have been moved or removed.


Managing contributors
---------------------

Click **Reviewers** on an issue card to open the Contributors list for that issue.

Adding a reviewer
~~~~~~~~~~~~~~~~~

Enter the reviewer's email address and click **Add reviewer**. They are added
with status *Pending*.

Sending invitations
~~~~~~~~~~~~~~~~~~~

Once you have added the reviewers you want, click **Send invites**. Each
pending reviewer receives an email with an invitation link valid for 180 days.
When they accept, their status changes to *Active* and they are added to the
Planka board automatically.

Monitoring progress
~~~~~~~~~~~~~~~~~~~

The contributors table shows each reviewer's status:

- **Pending** — added but not yet invited
- **Invited** — invitation sent, not yet accepted
- **Active** — accepted and has Planka board access
- **Revoked** — access removed

Use **Resend invite** to send a new link to a reviewer who has not responded.
Use **Revoke** to remove a reviewer from the issue.


Working in Planka
-----------------

Once you are assigned to an issue, you are added to its Planka board. Log in to
Planka at ``/planka/`` (or the URL your chief editor has given you) using your
Journal Watch account — no separate Planka login is needed.

In Planka you can:

- View all article cards in the board
- Move cards between lists (e.g. from *Candidates* to *Under Review*)
- Comment on cards
- Check reviewer progress

Your role in Planka is typically as a board editor. You can see and interact
with all cards, but Planka board settings are managed by the chief editor.
