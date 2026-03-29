"""
deploy/bootstrap/tests/test_aws_setup.py — Tests for deploy/bootstrap/aws_setup.py.

These tests are Django-free; the --ds flag loads settings at collection time
but no database is touched (no django_db marker).

Requires moto[s3,iam,sns,sesv2] (in requirements/local.in).
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from deploy.bootstrap.aws_setup import (
    backup_policy,
    create_iam_user,
    django_policy,
    main,
    media_public_read_policy,
    parse_args,
    planka_policy,
    print_credentials,
    provision,
    setup_iam,
    setup_s3,
    setup_ses,
    setup_sns,
)

BUCKET = "test-jw-bucket"
REGION = "ap-southeast-2"
DOMAIN = "journalwatch.test"
ACCOUNT_ID = "123456789012"
WEBHOOK_SECRET = "test-secret"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """Inject dummy credentials so boto3 never tries to contact real AWS."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture
def s3(aws_env):
    with mock_aws():
        yield boto3.client("s3", region_name=REGION)


@pytest.fixture
def iam(aws_env):
    with mock_aws():
        yield boto3.client("iam", region_name=REGION)


@pytest.fixture
def sns(aws_env):
    with mock_aws():
        yield boto3.client("sns", region_name=REGION)


@pytest.fixture
def ses_v2():
    """Mocked sesv2 client — avoids moto SESv2 coverage gaps."""
    mock = MagicMock()
    mock.create_email_identity.return_value = {"DkimAttributes": {"Tokens": ["token1", "token2", "token3"]}}
    mock.create_configuration_set.return_value = {}
    mock.create_configuration_set_event_destination.return_value = {}
    return mock


# ---------------------------------------------------------------------------
# Policy document tests (pure unit — no mocking)
# ---------------------------------------------------------------------------


class TestPolicyDocuments:
    def test_django_policy_sids(self):
        doc = django_policy(BUCKET)
        sids = {s["Sid"] for s in doc["Statement"]}
        assert sids == {"S3MediaReadWrite", "S3InboundEmailRead", "S3ListBucket", "SESSend"}

    def test_django_policy_media_resource(self):
        doc = django_policy(BUCKET)
        media = next(s for s in doc["Statement"] if s["Sid"] == "S3MediaReadWrite")
        assert media["Resource"] == f"arn:aws:s3:::{BUCKET}/media/*"
        assert "s3:PutObject" in media["Action"]
        assert "s3:DeleteObject" in media["Action"]

    def test_django_policy_ses_actions(self):
        doc = django_policy(BUCKET)
        ses = next(s for s in doc["Statement"] if s["Sid"] == "SESSend")
        assert "ses:SendEmail" in ses["Action"]
        assert "ses:SendRawEmail" in ses["Action"]
        assert ses["Resource"] == "*"

    def test_planka_policy_sids(self):
        doc = planka_policy(BUCKET)
        sids = {s["Sid"] for s in doc["Statement"]}
        assert sids == {"S3PlankaBucketObjects", "S3ListBucket"}

    def test_planka_policy_bucket_only(self):
        doc = planka_policy(BUCKET)
        obj = next(s for s in doc["Statement"] if s["Sid"] == "S3PlankaBucketObjects")
        assert obj["Resource"] == f"arn:aws:s3:::{BUCKET}/*"
        # Must NOT grant SES permissions
        assert not any("ses:" in a for a in obj["Action"])

    def test_planka_policy_no_ses(self):
        doc = planka_policy(BUCKET)
        for stmt in doc["Statement"]:
            actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
            assert not any(a.startswith("ses:") for a in actions), "Planka policy must not grant any SES permissions"

    def test_backup_policy_sids(self):
        doc = backup_policy(BUCKET)
        sids = {s["Sid"] for s in doc["Statement"]}
        assert sids == {"S3BackupReadWrite", "S3ListBucket"}

    def test_backup_policy_backups_prefix(self):
        doc = backup_policy(BUCKET)
        obj = next(s for s in doc["Statement"] if s["Sid"] == "S3BackupReadWrite")
        assert obj["Resource"] == f"arn:aws:s3:::{BUCKET}/*"

    def test_policies_use_correct_bucket(self):
        other = "other-bucket"
        for policy_fn in (django_policy, planka_policy, backup_policy, media_public_read_policy):
            doc = policy_fn(other)
            resources = []
            for stmt in doc["Statement"]:
                r = stmt.get("Resource", "")
                resources += r if isinstance(r, list) else [r]
            bucket_resources = [r for r in resources if r != "*"]
            assert all(
                other in r for r in bucket_resources
            ), f"{policy_fn.__name__} has resources not scoped to bucket '{other}'"

    def test_media_public_read_policy_only_exposes_media_prefix(self):
        doc = media_public_read_policy(BUCKET)
        assert doc["Statement"] == [
            {
                "Sid": "PublicReadMedia",
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{BUCKET}/media/*",
            }
        ]


# ---------------------------------------------------------------------------
# S3 setup tests
# ---------------------------------------------------------------------------


class TestSetupS3:
    def test_creates_bucket(self, s3):
        setup_s3(s3, BUCKET, REGION)
        buckets = [b["Name"] for b in s3.list_buckets()["Buckets"]]
        assert BUCKET in buckets

    def test_versioning_enabled(self, s3):
        setup_s3(s3, BUCKET, REGION)
        resp = s3.get_bucket_versioning(Bucket=BUCKET)
        assert resp["Status"] == "Enabled"

    def test_public_access_blocked_except_for_bucket_policy(self, s3):
        setup_s3(s3, BUCKET, REGION)
        resp = s3.get_public_access_block(Bucket=BUCKET)
        cfg = resp["PublicAccessBlockConfiguration"]
        assert cfg["BlockPublicAcls"] is True
        assert cfg["IgnorePublicAcls"] is True
        assert cfg["BlockPublicPolicy"] is False
        assert cfg["RestrictPublicBuckets"] is False

    def test_bucket_policy_allows_public_read_for_media_only(self, s3):
        setup_s3(s3, BUCKET, REGION)
        resp = s3.get_bucket_policy(Bucket=BUCKET)
        policy = json.loads(resp["Policy"])
        assert policy == media_public_read_policy(BUCKET)

    def test_idempotent(self, s3):
        """Running setup twice should not raise."""
        setup_s3(s3, BUCKET, REGION)
        setup_s3(s3, BUCKET, REGION)
        buckets = [b["Name"] for b in s3.list_buckets()["Buckets"]]
        assert buckets.count(BUCKET) == 1

    def test_default_lifecycle_does_not_expire_backup_versions(self, s3):
        setup_s3(s3, BUCKET, REGION)
        resp = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)
        rules = resp["Rules"]
        assert len(rules) == 1
        assert rules[0]["ID"] == "expire-incomplete-multipart"

    def test_optional_backup_noncurrent_expiration_rule(self, s3):
        setup_s3(s3, BUCKET, REGION, backup_noncurrent_expiration_days=90)
        resp = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)
        rules = {rule["ID"]: rule for rule in resp["Rules"]}
        assert "expire-old-backup-versions" in rules
        assert rules["expire-old-backup-versions"]["NoncurrentVersionExpiration"]["NoncurrentDays"] == 90

    def test_can_leave_versioning_disabled(self, s3):
        setup_s3(s3, BUCKET, REGION, enable_versioning=False)
        resp = s3.get_bucket_versioning(Bucket=BUCKET)
        assert "Status" not in resp


# ---------------------------------------------------------------------------
# IAM setup tests
# ---------------------------------------------------------------------------


class TestSetupIAM:
    def test_creates_three_users(self, iam):
        setup_iam(iam, BUCKET, f"{BUCKET}-planka", f"{BUCKET}-backups")
        users = {u["UserName"] for u in iam.list_users()["Users"]}
        assert {"jw-django", "jw-planka", "jw-backup"} <= users

    def test_creates_three_users_with_suffix(self, iam):
        setup_iam(iam, BUCKET, f"{BUCKET}-planka", f"{BUCKET}-backups", suffix="staging")
        users = {u["UserName"] for u in iam.list_users()["Users"]}
        assert {"jw-django-staging", "jw-planka-staging", "jw-backup-staging"} <= users
        # Must not create un-suffixed names
        assert "jw-django" not in users

    def test_returns_keys_for_new_users(self, iam):
        keys = setup_iam(iam, BUCKET, f"{BUCKET}-planka", f"{BUCKET}-backups")
        assert set(keys) == {"django", "planka", "backup"}
        for name, (ak, sk) in keys.items():
            assert ak.startswith("AKIA") or len(ak) > 0
            assert len(sk) > 0

    def test_policies_attached(self, iam):
        setup_iam(iam, BUCKET, f"{BUCKET}-planka", f"{BUCKET}-backups")
        for username, expected_policy in [
            ("jw-django", "jw-django-policy"),
            ("jw-planka", "jw-planka-policy"),
            ("jw-backup", "jw-backup-policy"),
        ]:
            resp = iam.get_user_policy(UserName=username, PolicyName=expected_policy)
            doc = (
                json.loads(resp["PolicyDocument"])
                if isinstance(resp["PolicyDocument"], str)
                else resp["PolicyDocument"]
            )
            assert "Statement" in doc

    def test_policies_attached_with_suffix(self, iam):
        setup_iam(iam, BUCKET, f"{BUCKET}-planka", f"{BUCKET}-backups", suffix="staging")
        for username, expected_policy in [
            ("jw-django-staging", "jw-django-staging-policy"),
            ("jw-planka-staging", "jw-planka-staging-policy"),
            ("jw-backup-staging", "jw-backup-staging-policy"),
        ]:
            resp = iam.get_user_policy(UserName=username, PolicyName=expected_policy)
            doc = (
                json.loads(resp["PolicyDocument"])
                if isinstance(resp["PolicyDocument"], str)
                else resp["PolicyDocument"]
            )
            assert "Statement" in doc

    def test_idempotent_no_new_keys(self, iam):
        """Second run skips users that already have keys."""
        setup_iam(iam, BUCKET, f"{BUCKET}-planka", f"{BUCKET}-backups")  # first run — creates keys
        keys = setup_iam(
            iam, BUCKET, f"{BUCKET}-planka", f"{BUCKET}-backups"
        )  # second run — users exist, already have keys
        # Keys should be None (skipped) for all three
        assert keys == {}

    def test_create_iam_user_returns_none_when_keys_exist(self, iam):
        iam.create_user(UserName="jw-test")
        iam.create_access_key(UserName="jw-test")
        result = create_iam_user(iam, "jw-test", "jw-test-policy", django_policy(BUCKET))
        assert result is None


# ---------------------------------------------------------------------------
# SES setup tests (MagicMock — avoids moto SESv2 gaps)
# ---------------------------------------------------------------------------


class TestSetupSES:
    def test_creates_email_identity(self, ses_v2):
        setup_ses(ses_v2, DOMAIN)
        ses_v2.create_email_identity.assert_called_once_with(
            EmailIdentity=DOMAIN,
            DkimSigningAttributes={"NextSigningKeyLength": "RSA_2048_BIT"},
        )

    def test_returns_dkim_tokens(self, ses_v2):
        tokens = setup_ses(ses_v2, DOMAIN)
        assert tokens == ["token1", "token2", "token3"]

    def test_creates_configuration_set_default_name(self, ses_v2):
        setup_ses(ses_v2, DOMAIN)
        ses_v2.create_configuration_set.assert_called_once_with(ConfigurationSetName="TrackingConfigSet")

    def test_creates_configuration_set_custom_name(self, ses_v2):
        setup_ses(ses_v2, DOMAIN, config_set_name="TrackingConfigSet-staging")
        ses_v2.create_configuration_set.assert_called_once_with(ConfigurationSetName="TrackingConfigSet-staging")

    def test_handles_already_exists(self, ses_v2):
        """AlreadyExistsException on both calls should be silently swallowed."""
        error = ClientError(
            {"Error": {"Code": "AlreadyExistsException", "Message": "Already exists"}},
            "CreateEmailIdentity",
        )
        ses_v2.create_email_identity.side_effect = error
        ses_v2.get_email_identity.return_value = {"DkimAttributes": {"Tokens": ["t1", "t2", "t3"]}}
        ses_v2.create_configuration_set.side_effect = ClientError(
            {"Error": {"Code": "AlreadyExistsException", "Message": "Already exists"}},
            "CreateConfigurationSet",
        )
        tokens = setup_ses(ses_v2, DOMAIN)
        assert tokens == ["t1", "t2", "t3"]


# ---------------------------------------------------------------------------
# SNS setup tests
# ---------------------------------------------------------------------------


class TestSetupSNS:
    def test_creates_topic(self, sns, ses_v2):
        setup_sns(sns, ses_v2, REGION, ACCOUNT_ID, DOMAIN, WEBHOOK_SECRET)
        topics = [t["TopicArn"] for t in sns.list_topics()["Topics"]]
        assert any("journalwatch-ses-events" in arn for arn in topics)

    def test_creates_topic_with_suffix(self, sns, ses_v2):
        setup_sns(sns, ses_v2, REGION, ACCOUNT_ID, DOMAIN, WEBHOOK_SECRET, suffix="staging")
        topics = [t["TopicArn"] for t in sns.list_topics()["Topics"]]
        assert any("journalwatch-ses-events-staging" in arn for arn in topics)
        assert not any(
            arn.endswith("journalwatch-ses-events") for arn in topics
        ), "Un-suffixed topic must not be created"

    def test_returns_topic_arn(self, sns, ses_v2):
        topic_arn = setup_sns(sns, ses_v2, REGION, ACCOUNT_ID, DOMAIN, WEBHOOK_SECRET)
        assert "journalwatch-ses-events" in topic_arn

    def test_wires_ses_event_destination(self, sns, ses_v2):
        setup_sns(sns, ses_v2, REGION, ACCOUNT_ID, DOMAIN, WEBHOOK_SECRET)
        ses_v2.create_configuration_set_event_destination.assert_called_once()
        call_kwargs = ses_v2.create_configuration_set_event_destination.call_args[1]
        assert call_kwargs["ConfigurationSetName"] == "TrackingConfigSet"
        dest = call_kwargs["EventDestination"]
        assert "BOUNCE" in dest["MatchingEventTypes"]
        assert "COMPLAINT" in dest["MatchingEventTypes"]

    def test_wires_ses_event_destination_with_suffix(self, sns, ses_v2):
        setup_sns(
            sns,
            ses_v2,
            REGION,
            ACCOUNT_ID,
            DOMAIN,
            WEBHOOK_SECRET,
            suffix="staging",
            config_set_name="TrackingConfigSet-staging",
        )
        call_kwargs = ses_v2.create_configuration_set_event_destination.call_args[1]
        assert call_kwargs["ConfigurationSetName"] == "TrackingConfigSet-staging"
        assert call_kwargs["EventDestinationName"] == "BounceComplaintSNS-staging"

    def test_idempotent(self, sns, ses_v2):
        """SNS create_topic is idempotent by spec; event destination handles AlreadyExists."""
        ses_v2.create_configuration_set_event_destination.side_effect = [
            {},
            ClientError(
                {"Error": {"Code": "AlreadyExistsException", "Message": "Already exists"}},
                "CreateConfigurationSetEventDestination",
            ),
        ]
        setup_sns(sns, ses_v2, REGION, ACCOUNT_ID, DOMAIN, WEBHOOK_SECRET)
        setup_sns(sns, ses_v2, REGION, ACCOUNT_ID, DOMAIN, WEBHOOK_SECRET)
        topics = [t["TopicArn"] for t in sns.list_topics()["Topics"]]
        assert sum(1 for t in topics if "journalwatch-ses-events" in t) == 1


# ---------------------------------------------------------------------------
# End-to-end provision() tests
# ---------------------------------------------------------------------------


class TestProvision:
    def test_provision_creates_all_resources(self, ses_v2):
        with mock_aws():
            s3 = boto3.client("s3", region_name=REGION)
            iam = boto3.client("iam", region_name=REGION)
            sns = boto3.client("sns", region_name=REGION)

            keys, dkim_tokens, topic_arn = provision(
                s3,
                iam,
                ses_v2,
                sns,
                BUCKET,
                f"{BUCKET}-planka",
                f"{BUCKET}-backups",
                REGION,
                DOMAIN,
                ACCOUNT_ID,
                WEBHOOK_SECRET,
            )

            # S3 bucket was created
            buckets = [b["Name"] for b in s3.list_buckets()["Buckets"]]
            assert BUCKET in buckets
            assert f"{BUCKET}-planka" in buckets
            assert f"{BUCKET}-backups" in buckets

            # IAM users were created
            users = {u["UserName"] for u in iam.list_users()["Users"]}
            assert {"jw-django", "jw-planka", "jw-backup"} <= users

            # Keys returned for all three users
            assert set(keys) == {"django", "planka", "backup"}

            # DKIM tokens returned from SES
            assert dkim_tokens == ["token1", "token2", "token3"]

            # SNS topic ARN returned
            assert "journalwatch-ses-events" in topic_arn

            backup_versioning = s3.get_bucket_versioning(Bucket=f"{BUCKET}-backups")
            assert "Status" not in backup_versioning

    def test_provision_idempotent(self, ses_v2):
        """Running provision twice should not raise."""
        ses_v2.create_configuration_set_event_destination.side_effect = [
            {},
            ClientError(
                {"Error": {"Code": "AlreadyExistsException", "Message": "Already exists"}},
                "CreateConfigurationSetEventDestination",
            ),
        ]
        with mock_aws():
            s3 = boto3.client("s3", region_name=REGION)
            iam = boto3.client("iam", region_name=REGION)
            sns = boto3.client("sns", region_name=REGION)

            provision(
                s3,
                iam,
                ses_v2,
                sns,
                BUCKET,
                f"{BUCKET}-planka",
                f"{BUCKET}-backups",
                REGION,
                DOMAIN,
                ACCOUNT_ID,
                WEBHOOK_SECRET,
            )
            # Second call — users and bucket already exist
            keys2, _, _ = provision(
                s3,
                iam,
                ses_v2,
                sns,
                BUCKET,
                f"{BUCKET}-planka",
                f"{BUCKET}-backups",
                REGION,
                DOMAIN,
                ACCOUNT_ID,
                WEBHOOK_SECRET,
            )

            # No new keys on second run (users already have keys)
            assert keys2 == {}

    def test_provision_staging_suffix(self, ses_v2):
        """--suffix creates namespaced IAM users, SNS topic, and config set."""
        staging_bucket = "jw-staging"
        with mock_aws():
            s3 = boto3.client("s3", region_name=REGION)
            iam = boto3.client("iam", region_name=REGION)
            sns = boto3.client("sns", region_name=REGION)

            keys, _, topic_arn = provision(
                s3,
                iam,
                ses_v2,
                sns,
                staging_bucket,
                f"{staging_bucket}-planka",
                f"{staging_bucket}-backups",
                REGION,
                "staging.journalwatch.test",
                ACCOUNT_ID,
                WEBHOOK_SECRET,
                suffix="staging",
                ses_domain=DOMAIN,
            )

            users = {u["UserName"] for u in iam.list_users()["Users"]}
            assert {"jw-django-staging", "jw-planka-staging", "jw-backup-staging"} <= users
            assert "jw-django" not in users

            assert "journalwatch-ses-events-staging" in topic_arn

            ses_v2.create_configuration_set.assert_called_with(ConfigurationSetName="TrackingConfigSet-staging")
            # SES identity uses ses_domain (prod domain), not the staging app domain
            ses_v2.create_email_identity.assert_called_with(
                EmailIdentity=DOMAIN,
                DkimSigningAttributes={"NextSigningKeyLength": "RSA_2048_BIT"},
            )


# ---------------------------------------------------------------------------
# --profile argument tests
# ---------------------------------------------------------------------------


class TestProfileArg:
    def test_profile_defaults_to_none(self):
        args = parse_args(["--bucket", "b", "--domain", "d.com"])
        assert args.profile is None
        assert args.planka_bucket is None

    def test_profile_accepted(self):
        args = parse_args(["--bucket", "b", "--domain", "d.com", "--profile", "jw-admin"])
        assert args.profile == "jw-admin"

    def test_planka_bucket_accepted(self):
        args = parse_args(["--bucket", "b", "--planka-bucket", "b-planka", "--domain", "d.com"])
        assert args.planka_bucket == "b-planka"

    def test_backup_bucket_accepted(self):
        args = parse_args(["--bucket", "b", "--backup-bucket", "b-backups", "--domain", "d.com"])
        assert args.backup_bucket == "b-backups"

    def test_backup_noncurrent_expiration_days_defaults_to_zero(self):
        args = parse_args(["--bucket", "b", "--domain", "d.com"])
        assert args.backup_noncurrent_expiration_days == 0

    def test_backup_noncurrent_expiration_days_accepted(self):
        args = parse_args(["--bucket", "b", "--domain", "d.com", "--backup-noncurrent-expiration-days", "30"])
        assert args.backup_noncurrent_expiration_days == 30

    def test_profile_passed_to_boto3_session(self, monkeypatch):
        """boto3.Session must receive profile_name so credentials never need to be in env."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)

        captured = {}

        def fake_session(profile_name=None, region_name=None):
            captured["profile_name"] = profile_name
            # Return a real moto-backed session so the rest of main() works
            import boto3 as _boto3

            return _boto3.Session(region_name=region_name)

        argv = ["aws_setup.py", "--bucket", BUCKET, "--domain", DOMAIN, "--profile", "jw-admin", "--region", REGION]

        with mock_aws():
            ses_mock = MagicMock()
            ses_mock.create_email_identity.return_value = {"DkimAttributes": {"Tokens": ["t1", "t2", "t3"]}}
            ses_mock.create_configuration_set.return_value = {}
            ses_mock.create_configuration_set_event_destination.return_value = {}

            with patch("deploy.bootstrap.aws_setup.boto3.Session", side_effect=fake_session), patch(
                "sys.argv", argv
            ), patch("deploy.bootstrap.aws_setup.boto3.Session") as mock_boto_session:
                mock_boto_session.return_value.client.return_value = ses_mock
                # We just need to verify the call signature — don't run full main()
                mock_boto_session.reset_mock()

                from deploy.bootstrap.aws_setup import main as aws_main

                try:
                    aws_main()
                except SystemExit:
                    pass  # may exit after printing credentials — that's fine
                except Exception:
                    pass

                # Verify Session was called with the profile name
                assert mock_boto_session.call_count >= 1
                call_kwargs = mock_boto_session.call_args_list[0]
                assert call_kwargs.kwargs.get("profile_name") == "jw-admin"

    def test_no_profile_session_called_with_none(self):
        """Omitting --profile results in profile_name=None (boto3 default chain)."""
        argv = ["aws_setup.py", "--bucket", BUCKET, "--domain", DOMAIN, "--region", REGION]

        with patch("sys.argv", argv), patch("deploy.bootstrap.aws_setup.boto3.Session") as mock_boto_session:
            mock_boto_session.return_value.client.return_value = MagicMock()
            mock_boto_session.return_value.client.return_value.get_caller_identity.return_value = {
                "Account": ACCOUNT_ID
            }

            from deploy.bootstrap.aws_setup import main as aws_main

            try:
                aws_main()
            except (SystemExit, Exception):
                pass

            assert mock_boto_session.call_count >= 1
            call_kwargs = mock_boto_session.call_args_list[0]
            assert call_kwargs.kwargs.get("profile_name") is None


class TestCliEntryPoints:
    def test_main_accepts_explicit_argv(self):
        with patch("deploy.bootstrap.aws_setup.boto3.Session") as mock_boto_session:
            sts = MagicMock()
            sts.get_caller_identity.return_value = {"Account": ACCOUNT_ID}

            def fake_client(name):
                clients = {
                    "s3": MagicMock(),
                    "iam": MagicMock(),
                    "sesv2": MagicMock(),
                    "sns": MagicMock(),
                    "sts": sts,
                }
                return clients[name]

            mock_boto_session.return_value.client.side_effect = fake_client

            with patch(
                "deploy.bootstrap.aws_setup.provision",
                return_value=({}, [], "arn:aws:sns:ap-southeast-2:123456789012:journalwatch-ses-events"),
            ):
                main(["--bucket", BUCKET, "--domain", DOMAIN, "--profile", "jw-admin", "--region", REGION])

            first_call = mock_boto_session.call_args_list[0]
            assert first_call.kwargs == {"profile_name": "jw-admin", "region_name": REGION}


class TestOutputHelpers:
    def test_print_credentials_matches_vps_env_fields(self, capsys):
        keys = {
            "django": ("django-ak", "django-sk"),
            "planka": ("planka-ak", "planka-sk"),
            "backup": ("backup-ak", "backup-sk"),
        }

        print_credentials(
            keys,
            BUCKET,
            f"{BUCKET}-planka",
            f"{BUCKET}-backups",
            REGION,
            "TrackingConfigSet-staging",
            webhook_secret=WEBHOOK_SECRET,
        )

        out = capsys.readouterr().out
        assert "DJANGO_AWS_ACCESS_KEY_ID=django-ak" in out
        assert "DJANGO_AWS_SECRET_ACCESS_KEY=django-sk" in out
        assert f"DJANGO_AWS_STORAGE_BUCKET_NAME={BUCKET}" in out
        assert f"DJANGO_AWS_S3_REGION_NAME={REGION}" in out
        assert f"DJANGO_AWS_DEFAULT_REGION={REGION}" in out
        assert "DJANGO_ANYMAIL_INBOUND_S3_OBJECT_PREFIX=email" in out
        assert f"WEBHOOK_SECRET={WEBHOOK_SECRET}" in out
        assert "ANYMAIL_CONFIGURATION_SET_NAME=TrackingConfigSet-staging" in out
        assert "PLANKA_S3_ACCESS_KEY_ID=planka-ak" in out
        assert "PLANKA_S3_SECRET_ACCESS_KEY=planka-sk" in out
        assert f"PLANKA_S3_BUCKET={BUCKET}-planka" in out
        assert f"PLANKA_S3_REGION={REGION}" in out
        assert f"RESTIC_REPOSITORY=s3:s3.amazonaws.com/{BUCKET}-backups" in out
        assert "AWS_ACCESS_KEY_ID=backup-ak" in out
        assert "AWS_SECRET_ACCESS_KEY=backup-sk" in out
        assert f"AWS_DEFAULT_REGION={REGION}" in out
