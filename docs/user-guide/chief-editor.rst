.. _chief-editor:

Chief Editor Guide
==================

The chief editor takes an issue from creation to the newsletter. The tabs
across the top of every issue page are the steps, in order: Setup, Articles,
Reviewers, Pull Reviews, Edit Reviews, Publish, Newsletter. Coordinators see
only Articles and Reviewers. This page is a reminder of which buttons to
press.


First-time setup
----------------

Open **Settings** once, before the first issue.

1. **PubMed API key**: paste an NCBI key and click **Save key**. Optional,
   but it raises the PubMed rate limit.
2. **Planka integration**: click **Run setup_planka_oidc**, then **Run
   setup_planka_api_key**. The connection card below turns green. Rerun the
   API key step if Planka is ever restored from backup.
3. **Watched journals**: click **Manage journals**, search by name, choose
   the NLM catalogue entry, and click **Add watched journal**. Repeat for
   each journal. **Deactivate** hides a journal from intake without deleting
   its articles.

The platform keeps its own copy of the PubMed feed for the watched journals,
refreshed every six hours for the current month and the two either side. A
newly added journal is picked up on the next refresh. For older months, run
the backfill command on the server (see
:doc:`/operations/management-commands`):

.. code-block:: bash

   python manage.py backfill_pubmed_journal_cache --from-month 2026-01 --to-month 2026-06

**Fetch monitoring** in Settings shows the last week of refreshes and when
the next one is due.


Step 1: Setup
-------------

Click **New issue** on the dashboard, or **Issues** in the top bar.

1. Enter the **Name**, and optionally the date, an introduction and a header
   image. Click **Create issue**.
2. Under **Planka board**, click **Create Planka board**. It takes about
   twenty seconds and continues if you leave the page. **Open board**,
   **Rename** and **Change background image** appear on the card once it is
   done.
3. Under **Coordinators**, add each coordinator's name and email, then click
   **Send initial invites**.

Coordinators see the issue on their dashboard once they accept. Come back to
this tab at any time to change the name, introduction or image.


Step 2: Articles
----------------

The coordinator normally does this step. The
:ref:`coordinator guide <coordinator>` has the details; in short:

1. Set the months, tick the journals, click **Start intake**.
2. Click **Check for new articles** on the **Keep the list up to date** card
   at the end of each month in the window, and a fortnight after it closes.
3. Toggle articles to **Staged**, then click **Push staged articles to
   Planka candidates**.

**Reconcile Planka status** shows which pushed cards reviewers have moved or
removed.


Step 3: Reviewers
-----------------

1. Enter each reviewer's name and email and click **Add**.
2. Click **Send initial invites**. Links stay valid for 180 days.
3. *Invited* means the email has gone out. *Active* means they have accepted
   and are on the board.
4. Tick a row and click **Resend to selected** to remind someone. **Revoke**
   removes them. **Sync Planka** appears if their board membership failed.


Step 4: Pull Reviews
--------------------

Reviewers move finished cards to *Publish ready* on the board.

1. Click **Refresh cards** if the list looks stale.
2. Tick the cards to bring in and click **Import selected**, or click
   **Import all Publish ready**.

Each import creates a review from the text below the card's marker line.
Cards already imported are marked and skipped. **History** shows a card's
earlier versions if a reviewer has edited it since.


Step 5: Edit Reviews
--------------------

1. Click **Edit** on a review to change its article, author, text, featured
   flag or featured image. Click **Save**.
2. Click **Add review** for a review written outside Planka.
3. **Remove** detaches a review from the issue without deleting it.

At most two reviews can be featured.


Step 6: Publish
---------------

1. Check the readiness badges. Fix anything red on the Edit Reviews tab.
2. Click **Publish all**, or **Publish** on individual rows.
3. Click **Set … as homepage** to put the issue on the public site.

Draft reviews stay hidden even after the issue is the homepage.


Step 7: Newsletter
------------------

1. Click **Save newsletter**. It renders from the live reviews.
2. Click **Send test email** and check it in a mail client.
3. Click **Send newsletter**. A newsletter can only be sent after a test, and
   only once unless you click **Enable one resend**.

Open and click statistics appear on the same tab once the send is complete.


Subscribers
-----------

Open **Subscribers** from the dashboard.

- **Upload CSV**: a file with an ``email`` column. Duplicates are skipped and
  a summary is shown.
- **Mailing list**: view or unsubscribe individual addresses. Public sign-ups
  arrive here after double opt-in.
