Operations
==========

Runbooks and operational guides for the Journal Watch production stack.

Production runs on the ``journal-watch-vps`` host from the server repo
checkout at ``/opt/deploy``; the Django service is ``journal-watch`` and a
release is ``push to main`` → GitHub Actions image build →
``ssh journal-watch-vps ./deploy``. Start with the production deployment
page for the layout, the runbook for exact commands.

.. toctree::
   :maxdepth: 2

   production-deploy
   deploy-runbook
   aws-setup
   management-commands
