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

Choose the months the issue covers, tick the journals you would like to
search, and click **Start intake**. Journal Watch keeps its own copy of the
PubMed feed for the watched journals, so the list is usually ready within a
few seconds. Later on, **Check for new articles** picks up anything the
journals have published since.

Step 2: Choose articles to review
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. container:: feature

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

.. container:: feature flip

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
