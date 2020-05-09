"""Amazon S3 storage connector."""
import json
import logging
import mimetypes
import os
from pathlib import Path

import boto3
import botocore

from .baseconnector import BaseStorageConnector, validate_url, validate_urls

logger = logging.getLogger(__name__)


class AwsS3Connector(BaseStorageConnector):
    """Amazon S3 storage connector."""

    def __init__(self, config: dict, name: str):
        """Connector initialization."""
        super().__init__(config, name)
        self.bucket_name = config["bucket"]
        self.supported_download_hash = ["md5", "crc32c", "awss3etag"]
        self.supported_upload_hash = ["awss3etag"]
        self.hash_propery = {"awss3etag": "e_tag"}
        default_chunk_size = 8 * 1024 * 1024  # 8MB
        self.multipart_threshold = self.config.get(
            "multipart_threshold", default_chunk_size
        )
        self.multipart_chunksize = self.config.get(
            "multipart_chunksize", default_chunk_size
        )
        self.use_threads = False

        self._session = None
        self._client = None
        self._sts = None
        self._awss3 = None

    def _initialize(self):
        """Initializaton."""
        credentials = self.config["credentials"]
        with open(credentials) as f:
            settings = json.load(f)
        self.session = boto3.Session(
            aws_access_key_id=settings["AccessKeyId"],
            aws_secret_access_key=settings["SecretAccessKey"],
            aws_session_token=settings.get("SessionToken"),
            region_name=self.config.get("region_name"),
        )
        self.awss3 = self.session.resource("s3")
        self.client = self.session.client("s3")
        self.sts = self.session.client("sts")

    def __getattr__(self, name):
        """Lazy initialize some attributes."""
        requires_initialization = ["client", "awss3", "sts", "session"]
        if name not in requires_initialization:
            raise AttributeError()

        self._initialize()
        return getattr(self, name)

    @validate_url
    def push(self, stream, url):
        """Push data from the stream to the given URL."""
        mime_type = mimetypes.guess_type(url)[0]
        extra_args = {} if mime_type is None else {"ContentType": mime_type}
        self.client.upload_fileobj(
            stream,
            self.bucket_name,
            os.fspath(url),
            Config=self._get_transfer_config(),
            ExtraArgs=extra_args,
        )

    @validate_urls
    def delete(self, urls):
        """Remove objects."""
        # At most 1000 objects can be deleted at the same time.
        max_chunk = 1000
        bucket = self.awss3.Bucket(self.bucket_name)
        for i in range(0, len(urls), max_chunk):
            next_chunk = urls[i : i + max_chunk]
            objects = [{"Key": os.fspath(url)} for url in next_chunk]
            bucket.delete_objects(Delete={"Objects": objects, "Quiet": True})

    @validate_url
    def get(self, url, stream):
        """Get data from the given URL and write it into the given stream."""
        self.client.download_fileobj(
            self.bucket_name, os.fspath(url), stream, Config=self._get_transfer_config()
        )

    def _get_transfer_config(self):
        """Get transfer config object."""
        return boto3.s3.transfer.TransferConfig(
            multipart_threshold=self.multipart_threshold,
            multipart_chunksize=self.multipart_chunksize,
            use_threads=self.use_threads,
        )

    @validate_url
    def get_object_list(self, url):
        """Get a list of objects stored bellow the given URL."""
        url = os.path.join(url, "")
        paginator = self.client.get_paginator("list_objects_v2")
        kwargs = {"Bucket": self.bucket_name, "Prefix": url}
        ret = []
        for page in paginator.paginate(**kwargs):
            try:
                contents = page["Contents"]
            except KeyError:
                break
            for obj in contents:
                ret.append(obj["Key"])
        return ret

    @validate_url
    def get_hash(self, url, hash_type):
        """Get the hash of the given type for the given object."""
        resource = self.awss3.Object(self.bucket_name, os.fspath(url))
        try:
            if hash_type in self.hash_propery:
                prop = self.hash_propery[hash_type]
                return getattr(resource, prop).strip('"')
            else:
                return resource.metadata.get(hash_type)
        except botocore.exceptions.ClientError as error:
            if error.response["Error"]["Code"] == "404":
                return None
            else:
                # Something else has gone wrong.
                raise

    @validate_url
    def exists(self, url):
        """Get if the object at the given URL exist."""
        try:
            self.awss3.Object(self.bucket_name, os.fspath(url)).load()
        except botocore.exceptions.ClientError as error:
            if error.response["Error"]["Code"] == "404":
                return False
            else:
                # Something else has gone wrong.
                raise
        else:
            return True

    @validate_url
    def set_hashes(self, url, hashes):
        """Set the  hashes for the given object."""
        # Changing metadata on existing objects in S3 is annoyingly hard.
        # See
        # https://boto3.amazonaws.com/v1/documentation/api/1.9.42/guide/s3.html
        # under managed copy for example.
        # If one uses copy_object method proposed by some solutions the e_tag
        # value on object can (and will) change. That causes error downloading
        # since hash check fails.
        head = self.client.head_object(Bucket=self.bucket_name, Key=os.fspath(url))
        meta = head["Metadata"]
        hashes = {k: v for (k, v) in hashes.items() if k not in self.hash_propery}
        meta.update(hashes)
        copy_source = {
            "Bucket": self.bucket_name,
            "Key": url,
        }
        self.client.copy(
            copy_source,
            self.bucket_name,
            url,
            ExtraArgs={"Metadata": meta, "MetadataDirective": "REPLACE"},
        )

    @property
    def base_path(self):
        """Get a base path for this connector."""
        return Path("")

    @validate_url
    def presigned_url(self, url, expiration=60):
        """Create a presigned URL.

        The URL is used to obtain temporary access to the object ar the
        given URL using only returned URL.

        :param expiration: expiration time of the link (in seconds), default
            is one minute.

        :returns: URL that can be used to access object or None.
        """
        response = None
        try:
            response = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": os.fspath(url)},
                ExpiresIn=expiration,
            )
        except botocore.exceptions.ClientError:
            logger.exception("Error creating presigned URL")

        # The response contains the presigned URL
        return response
