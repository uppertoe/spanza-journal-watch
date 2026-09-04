.. _coordinator:

Regional Coordinator Guide
==========================

Regional coordinators are assigned to specific issues by the chief editor.
Your role is to shortlist the articles and invite the reviewers for the
issues assigned to you. You have access to the editorial pages for those
issues only.

What the editorial platform does
--------------------------------

.. container:: feature

   .. container:: feature-text

      The editorial platform takes care of the mechanical parts of assembling
      an issue, so that your time goes on judgement rather than on searching.

      Each issue covers a window of a month or two. For that window, the
      platform retrieves every article published in the watched journals
      directly from PubMed, with MeSH terms attached, and applies a paediatric
      filter before you see the list. Your task is to look over what remains
      and shortlist the articles that merit a review.

      The shortlist is sent to a shared Planka board, where reviewers claim
      articles, write their reviews and mark them complete. You can see who is
      covering what, and how each review is progressing, without a round of
      emails.

      1. The platform retrieves every article the watched journals published
         in the window, directly from PubMed.
      2. The paediatric MeSH filter removes what is not relevant before you
         look.
      3. You shortlist the articles that merit a review and send them to the
         Planka board.
      4. Reviewers claim, write and complete their reviews on the board, in
         view of the coordinators and editors.

   .. raw:: html
      :file: _demos/overview.html


Logging in
----------

.. container:: feature flip

   .. container:: feature-text

      Go to `/editorial/go </editorial/go>`_ on the Journal Watch site and
      sign in with your account. You will be asked where you would like to go:
      the **editorial backend**, where article intake and reviewer invitations
      are managed, or **Planka**, the reviewers' board.

      The same page is available from the account button at the top of the
      main site once you are signed in: open it and choose **Editor**. The
      button appears as soon as your invitation has been accepted.

      Choose the editorial backend and you land on your **dashboard**, which
      shows a card for each issue you have been assigned to. If the dashboard
      is empty, you have not been assigned to an issue yet. Contact the chief
      editor.

      Each issue card has two shortcuts: **Articles** opens Article Intake for
      that issue, and **Reviewers** opens its reviewer list. Once you are in an
      issue, the **context bar** at the top of every page shows which issue you
      are working in, with tabs for the two steps that are yours. The
      remaining tabs (Pull Reviews, Edit Reviews, Publish, Newsletter) are
      managed by the chief editor.

   .. raw:: html
      :file: _demos/go.html


Article intake
--------------

Click **Articles** on an issue card, or the Articles tab in the context bar,
to open Article Intake for that issue. The page walks through three steps:
loading the articles, choosing the ones worth reviewing, and sending that
shortlist across to the reviewers' board.

Step 1: Load the articles
~~~~~~~~~~~~~~~~~~~~~~~~~

.. container:: feature

   .. container:: feature-text

      Choose the months the issue covers, tick the journals you would like to
      search, and click **Start intake**. The month range and the journal
      list open with whatever was used last time for this issue, so on a
      return visit there is usually nothing to change.

      The platform keeps its own copy of the PubMed feed for the watched
      journals, refreshed every six hours for the months around the current
      date. Start intake loads the list from that copy straight away, then
      asks PubMed directly, in the background, for anything published in the
      window that the copy does not yet hold. A progress line shows each
      journal as it is checked, and the additions are merged into the list
      when the check finishes. You can begin looking through the list while
      it runs.

      1. Set **From month** and **To month** to the first and last months of
         the issue's window. A range may cover up to twelve months.
      2. Tick the journals to search, or use **Select all**.
      3. Click **Start intake**. The list appears within a few seconds, and
         the PubMed check follows over the next minute or two.

   .. raw:: html
      :file: _demos/load.html

An article belongs to the month of the publication date that PubMed records
for it, which is usually the date it first appeared online rather than the
date of the print issue. An article that appears in the October print issue
but was published online in August is therefore an August article.

If you would like to change the months or the journals, run **Start intake**
again with the new settings. Your shortlist is carried across: articles
already staged stay staged, and articles already on the Planka board are
recognised and are not pushed a second time.

Step 2: Choose articles to review
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. container:: feature flip

   .. container:: feature-text

      The list holds everything the watched journals published during the
      issue's window, retrieved from PubMed. The **Paediatric (MeSH)** filter
      is on by default, so articles without a paediatric MeSH term are hidden
      until you switch it off. The other filters and the journal tabs narrow
      the list further.

      Marking an article as **staged** puts it on your shortlist. It does not
      change the public website, and it is not yet visible to reviewers.

      1. Look through the filtered list. Use the journal tabs and the
         specialty filters to narrow it as you see fit.
      2. Click the toggle on each article you would like to shortlist. It
         turns green and reads *Staged*.
      3. If you would like everything currently showing, use **Stage all
         (filtered)**. **Unstage all (filtered)** takes them off again.

   .. raw:: html
      :file: _demos/stage.html

Step 3: Send the shortlist to the review board
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. container:: feature

   .. container:: feature-text

      Reviewers work on a shared **Planka board**, a simple kanban board with
      three lists. Pushing sends your staged articles to that board, where
      each one becomes a card in the *Candidates* list for reviewers to
      choose from.

      This does not affect the public website. The issue is only published
      when the editors release it.

      1. Once you are happy with the shortlist, scroll down to Step 3 and
         click **Push staged articles to Planka candidates**.
      2. Wait for the confirmation. The *pushed* count at the top of the
         results updates.
      3. You can stage more articles and push again whenever you like.
         Anything already on the board is left alone.

   .. raw:: html
      :file: _demos/push.html

Use **Reconcile Planka status** at any time to check which articles are still
in the Candidates column and which have been moved or removed by reviewers.


.. _coordinator-recheck:

Checking again as the window fills
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. container:: feature flip

   .. container:: feature-text

      The list you load on the first day is not the final list. Journals
      publish throughout the window, and PubMed indexes each article some
      days after it appears online. An issue covering September and October,
      set up in the first week of September, holds only a fraction of what
      the two months will eventually contain.

      The platform's own copy of the feed is refreshed every six hours, but
      your issue's list is only brought up to date when you ask. A
      highlighted card, **Keep the list up to date**, sits between Step 1
      and Step 2. It shows how far the issue's window has run, when to check
      next, and when the list was last checked, and it turns amber when a
      check is overdue. Its **Check for new articles** button runs the PubMed
      check again for the issue's months and journals. Articles you have
      already staged, and articles already on the Planka board, are left
      exactly as they were.

      1. Return at the **end of each month** in the window and click
         **Check for new articles** on the card. An amber card means more
         than a week has passed since the last check.
      2. Check once more a **fortnight or so after the window closes**, when
         the last of the final month's articles have been indexed, before
         you settle the shortlist.
      3. Additions since your last visit are marked with a **blue dot** and
         counted at the top of the list. Use the **New only** filter to see
         just those, and **Mark all seen** once you have looked through them.

   .. raw:: html
      :file: _demos/recheck.html

The check runs in the background and reports either *Found N new article(s)
since last check* or *No new articles found*. Each list can be checked once
every fifteen minutes; if you click again sooner, the page tells you how long
to wait, and no request is sent to PubMed.

The blue markers are kept per person. What is new to you is not necessarily
new to a colleague working on the same issue, and marking articles as seen
affects only your own view.


Inviting your reviewers
-----------------------

Click **Reviewers** on an issue card, or the Reviewers tab, to open the
reviewer list for that issue.

.. container:: feature

   .. container:: feature-text

      Reviewers are invited by email. You add each person to the issue first,
      then send the invitations together. When a reviewer accepts, the
      platform creates their Planka access and adds them to the board, so
      there is nothing to set up on the board itself.

      1. Enter the reviewer's name and email address and click **Add**. They
         appear in the table as *Pending*.
      2. Repeat for everyone you would like to invite.
      3. Click **Send initial invites**. Each pending reviewer receives an
         email with a link that stays valid for 180 days.
      4. Keep an eye on the Status column. *Invited* means the email has gone
         out, and *Active* means they have accepted and can see the board.
      5. To send a reminder, tick the reviewer's row and click **Resend to
         selected**.

   .. raw:: html
      :file: _demos/invite.html

The Status column can also show *Expired*, when an invitation link has run
out, and *Revoked*, when a reviewer has been removed from the issue. Use
**Revoke** on a reviewer's row to remove them.


Working in Planka
-----------------

Once you are assigned to an issue, you are added to its Planka board. Log in to
Planka using your Journal Watch account: click **Sign in with Journal Watch**
on the Planka login page. No separate Planka login is needed.

In Planka you can:

- View all article cards on the Reviews board
- Move cards between lists, for example from *Candidates* to *Under review*
- Comment on cards to give feedback to reviewers
- Check reviewer progress by seeing which cards are in each list

Your role on the Reviews board is board editor, so you can see and interact
with all cards. The Instructions board contains read-only guidance cards.
Planka board settings are managed by the chief editor.
