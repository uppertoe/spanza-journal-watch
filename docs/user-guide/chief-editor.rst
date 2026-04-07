.. _chief-editor:

Chief Editor Guide
==================

The chief editor has full access to all editorial functions. This guide walks
through the complete workflow for a single newsletter issue — from creating the
issue through to publication.

Dashboard
---------

After logging in you land on the dashboard at ``/backend/``. It shows a live
snapshot of the system:

- **Homepage issue** — the issue currently shown on the public site
- **Current issue** — the issue you are actively building (status and review count)
- **Latest newsletter** — whether the most recent newsletter has been sent
- **Last CSV upload** — subscriber list status
- **Planka health** — whether the OIDC and API key connections are working

From the dashboard you can jump directly to any section of the backend. The
sidebar and top navigation are available on every page.

After logging in you can also choose to go directly to the Planka board via the
**Go to backend / Go to Planka** landing page (``/backend/go/``).


Settings: first-time setup
---------------------------

Before using the system for the first time, visit **Settings** (``/backend/settings/``)
and confirm the following.

PubMed API key
~~~~~~~~~~~~~~

An NCBI API key raises the PubMed rate limit and is required for production use.
Obtain one free at `https://www.ncbi.nlm.nih.gov/account/` then paste it into the
**PubMed API key** field and click **Save key**. The page shows the last
validation time and any error from PubMed.

Planka integration
~~~~~~~~~~~~~~~~~~

The Planka integration requires two setup steps, shown in sequence on the Settings page:

1. **Register OIDC application** — click **Run setup_planka_oidc**. This creates
   the OAuth2 client that lets Planka use Django as its identity provider. Only
   needs to be run once.

2. **Generate API key** — click **Run setup_planka_api_key**. This writes an API
   token directly to the Planka database. The Settings page shows the masked key
   and last validation time. If the key ever stops working (e.g. after a Planka
   database restore), click the button again to regenerate it.

The **Planka connection status** card below these buttons shows whether the API
connection is currently healthy and which account it is using.

Watched journals
~~~~~~~~~~~~~~~~

Article intake pulls from PubMed based on a list of journals you maintain.
Click **Manage watched journals** in Settings (or use the Watched Journals link
in the sidebar) to add or deactivate journals. Each journal entry holds a name
and ISSN numbers. Newly added journals are active by default.


Issue workflow overview
-----------------------

Each newsletter issue moves through seven steps, shown as tabs in the issue
context bar at the top of all issue builder pages:

1. **Setup** — create the issue, configure Planka, and assign coordinators
2. **Articles** — fetch articles from PubMed, stage candidates, and push to Planka
3. **Reviewers** — invite contributors and monitor their status
4. **Pull Reviews** — import completed reviews from the Planka board
5. **Edit Reviews** — edit and polish the review content
6. **Publish** — make reviews live and set the homepage
7. **Newsletter** — compose and send the newsletter email

Coordinators see only the Articles and Reviewers tabs. The remaining tabs are
chief-editor only.

You can navigate between tabs at any time and come back to earlier steps. An
**Issues** sidebar lets you switch between issues or create a new one.


Step 1: Setup
-------------

Go to **Setup** (``/backend/issues/builder/``).

Fill in:

- **Name** — the issue title as it will appear on the site (required)
- **Date** — publication date (optional)
- **Body** — introductory text displayed above the reviews (Markdown supported)
- **Image** — optional header image (JPG or PNG)

Click **Save issue draft**. The issue is created in draft state and will not be
visible on the public site until you publish it.

Once saved, two additional panels appear on the page:

**Planka setup** — create the Planka kanban board for this issue. Click
**Initialise Planka board** (or **Create Planka project**). This automatically:

- Creates a Planka project named after the issue
- Sets up a **Reviews** board with three lists: *Candidates*, *Under review*,
  and *Publish ready*
- Creates a separate **Instructions** board with guidance cards for reviewers,
  editors, and administrators
- Registers a webhook so that card changes in Planka sync back to the backend

After the board is created, the page shows a link to the board and allows you to
set a custom background image. You can recreate the board (e.g. if it was
accidentally deleted) without losing review data — the backend retains imported
review content independently of Planka.

**Issue coordinators** — assign regional coordinators to this issue. Enter a
coordinator's name and email address. Once assigned, they can access the
Articles and Reviewers tabs for this issue from their dashboard.

.. note::
   You can return to the Setup tab at any time to update the issue name, body,
   image, or coordinator list before publishing.


Step 2: Articles
----------------

Go to the **Articles** tab (``/backend/articles/intake/``). If an issue is not
already selected, choose one from the Issues sidebar.

Stage 1 — Fetch
~~~~~~~~~~~~~~~

Select the date range and tick the journals you want to search. Use the filter
box to find journals by name. Click **Fetch from PubMed**. The search runs in
the background; a status indicator shows progress. Results appear below once the
fetch completes.

Stage 2 — Stage candidates
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The results table shows every article returned from PubMed. Use the filters
to narrow the list:

- **Text search** — filter by title, DOI, or PMID
- **Journal** — filter to a single journal
- **Selection status** — show only staged or unstaged articles
- **Specialty filters** — paediatric content, humans only, pain, ICU, cardiac,
  neonatal, review papers, trial papers

For each article you want to send to the Planka board, tick the checkbox in
the **Staged** column. You can also:

- Click **Bulk select / unselect** to stage or unstage the entire filtered set
  at once
- Click the article title to expand the abstract
- Use the **Find article** panel (bottom right) to search for a specific article
  by title, DOI, or PMID and add it directly to the batch

Stage 3 — Push to Planka
~~~~~~~~~~~~~~~~~~~~~~~~~

Once you have staged the articles you want, click **Push staged articles to
Planka candidates**. Each staged article becomes a card in the *Candidates* list
on the Planka board. Cards that were already pushed are skipped.

Click **Reconcile Planka status** at any time to check which staged articles
are still in the Candidates list, which have been moved to another list, and
which have been deleted from Planka.

You can re-run the PubMed fetch (click **Refresh current batch**) to pick up
articles published after the initial search, without losing your existing
selections.


Step 3: Reviewers
-----------------

Go to the **Reviewers** tab (``/backend/issues/reviewers/``).

Adding reviewers
~~~~~~~~~~~~~~~~

Enter a reviewer's **name** and **email address**, then click **Add reviewer**.
Both fields are required. The reviewer is added with status *Pending*.

Sending invitations
~~~~~~~~~~~~~~~~~~~~

Once you have added the reviewers you want, click **Send invites**. Each
pending reviewer receives an email with a personalised invitation link valid
for 180 days. The link takes them to a page where they sign in (or create an
account) with their invited email address. Once signed in with the correct
email, their invitation is accepted automatically.

Once a reviewer accepts, their status changes to *Active* and they are
automatically added as a member of the Planka board.

Monitoring status
~~~~~~~~~~~~~~~~~

The contributors table shows each reviewer's current status:

- **Pending** — added but invitation not yet sent
- **Invited** — invitation email sent, not yet accepted
- **Active** — accepted and has board access
- **Revoked** — access removed

Use **Resend invite** to send a new link to a reviewer who has not accepted.
Use **Revoke** to remove a reviewer from the issue. Use **Sync to Planka** if a
reviewer's board membership appears out of sync (e.g. after a Planka database
restore).


Step 4: Pull Reviews
--------------------

When reviewers complete their cards and move them to the *Publish ready* list on
the Planka board, you import them into the backend.

Go to the **Pull Reviews** tab.

The **Import cards** panel shows all cards in the Planka board. Use the scope
selector to show only the *Publish ready* list (recommended) or all cards.

Click **Import** next to each card you want to bring in. The backend extracts
the review content from the card description (below the marker line) and creates
a Review record linked to the issue. Cards that have already been imported are
shown as blocked — click the review link to edit the existing review instead.

Click **Import all publish-ready cards** to import the entire Publish ready list
in one action.

.. note::
   Importing a card does not remove it from Planka. Reviewers can continue
   editing a card after it has been imported — use the **card revisions** panel
   (visible once a card is imported) to see the edit history and restore an
   earlier version if needed.


Step 5: Edit Reviews
---------------------

Go to the **Edit Reviews** tab (``/backend/issues/reviews/``).

The reviews table lists all reviews currently attached to this issue. For each
review you can see whether it is **Featured**, whether it is **Live** (published),
and whether it has a **featured image**.

Editing a review
~~~~~~~~~~~~~~~~~

Click **Edit** on any row to open the review form:

- **Article** — the linked PubMed article (searchable)
- **Author** — the reviewer's author profile (searchable; autocompletes from
  existing profiles)
- **Body** — the review text (Markdown)
- **Featured** — tick to mark this review as a featured article in the issue
- **Featured image** — optional image displayed alongside the featured review

Adding a review manually
~~~~~~~~~~~~~~~~~~~~~~~~~

Click **Add review** to create a review that was not imported from Planka. You
can search for an existing article or create one inline.

Removing a review
~~~~~~~~~~~~~~~~~

Click **Remove** on any row to detach the review from this issue. The review
record is not deleted — it can be re-attached later if needed.


Step 6: Publish
---------------

Go to the **Publish** tab (``/backend/issues/publish/``).

Making individual reviews live
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The publish table lists every review in the issue with its current live/draft
status. Click the toggle button on any row to make that review live or return
it to draft. Reviews in draft state are not visible on the public site even
after the issue is set as homepage.

Setting the homepage
~~~~~~~~~~~~~~~~~~~~

The homepage section shows which issue is currently live on the public site.
Click **Set {issue name} as homepage** to make this issue the active homepage.
A confirmation dialog appears before the change takes effect. Setting the
homepage does not automatically publish individual reviews — toggle them live
first.


Step 7: Newsletter
------------------

Go to the **Newsletter** tab to compose and send the newsletter email for this
issue.

The newsletter workflow:

1. **Draft** — compose the newsletter (it renders from the current live reviews)
2. **Test send** — send a test copy to a nominated address
3. **Final send** — dispatch to all active subscribers

The newsletter is rendered to HTML email using MJML and delivered via Amazon SES.
After sending, the Newsletters list shows delivery statistics.


Subscriber management
----------------------

Go to **Subscribers** (accessible from the dashboard) to manage the mailing list.

**Upload CSV** — upload a CSV file with an ``email`` column (other columns are
ignored). The system validates each address, skips duplicates, and shows a
summary of what was imported. Existing subscribers are not duplicated.

**Mailing list** — view and manage individual subscribers. Subscribers join via
double opt-in through the public sign-up form or are added via CSV. You can
manually unsubscribe any address from this list.
