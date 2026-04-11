import mimetypes

from storages.backends.s3boto3 import S3Boto3Storage

# Ensure webp is recognised on all platforms (Alpine's mimetypes DB may lack it)
mimetypes.add_type("image/webp", ".webp")


class StaticRootS3Boto3Storage(S3Boto3Storage):
    location = "static"
    default_acl = "public-read"


class MediaRootS3Boto3Storage(S3Boto3Storage):
    location = "media"
    file_overwrite = True


class S3ReferenceStorage(S3Boto3Storage):
    """Saves the reference to an existing file"""

    def save(self, name, content, max_length=None):
        return name
