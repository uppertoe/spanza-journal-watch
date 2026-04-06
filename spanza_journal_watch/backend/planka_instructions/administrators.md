## Admin setup checklist

- Confirm that Planka integration and Chief Editor roles are working
- Assign a regional coordinator to an issue
- Monitor the assignment of reviewers to the issue, and ensure their Planka access is properly assigned
- Once reviews are complete, pull these into the Journal Watch backend for a final editing pass and preparation for publishing.

## Backup and restore

Full system backups are hourly (through Restic onto S3); this will revert the state of the entire Planka +/- Journal Watch database.

Journal Watch also keeps a record of each edit made in Planka; these are stored in the issues -> pull reviews section of the editorial backend. Previous edits to a card can therefore be restored manually on request.

## Release readiness

Before issue publication:

1. All edited cards are pulled from Planka (including those not in Publish ready)
2. Each review has been edited for spelling, grammar and content
3. Appropriate tags have been selected for each card
4. Two feature cards have been selected, with cover images from each ([Unsplash](https://unsplash.com) is a good starting point for royalty-free images)
