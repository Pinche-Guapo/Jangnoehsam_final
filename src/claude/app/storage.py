"""MinIO storage wrapper for file uploads/downloads"""
from minio import Minio
from minio.error import S3Error
from typing import BinaryIO, Optional
import os
from pathlib import Path

from .config import settings


class StorageService:
    """MinIO object storage service"""

    def __init__(self):
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure
        )
        self._ensure_buckets()

    def _ensure_buckets(self):
        """Create default buckets if they don't exist"""
        buckets = [
            "voice-recordings",
            "mri-scans",
            "processed",
            "exports",
            # MRI-specific derived artifacts
            "mri-preprocessed",
            # Future CAM/XAI artifacts (heatmaps, overlays, metadata)
            "mri-xai",
        ]

        for bucket in buckets:
            try:
                if not self.client.bucket_exists(bucket):
                    self.client.make_bucket(bucket)
            except S3Error as e:
                # Bucket might already exist or permission issue
                pass

    def upload_file(self, bucket: str, object_name: str, file_path: str) -> str:
        """
        Upload a file from filesystem to MinIO

        Args:
            bucket: Bucket name (e.g., 'voice-recordings')
            object_name: Object path in bucket (e.g., 'patient-123/recording-456.wav')
            file_path: Local file path to upload

        Returns:
            Object path that was uploaded
        """
        try:
            self.client.fput_object(bucket, object_name, file_path)
            return f"{bucket}/{object_name}"
        except S3Error as e:
            raise Exception(f"MinIO upload failed: {e}")

    def upload_fileobj(self, bucket: str, object_name: str, file_data: BinaryIO, length: int) -> str:
        """
        Upload file from file-like object to MinIO

        Args:
            bucket: Bucket name
            object_name: Object path in bucket
            file_data: File-like object (e.g., UploadFile.file)
            length: Size of the file in bytes

        Returns:
            Object path that was uploaded
        """
        try:
            self.client.put_object(bucket, object_name, file_data, length)
            return f"{bucket}/{object_name}"
        except S3Error as e:
            raise Exception(f"MinIO upload failed: {e}")

    def download_file(self, bucket: str, object_name: str, file_path: str) -> str:
        """
        Download a file from MinIO to filesystem

        Args:
            bucket: Bucket name
            object_name: Object path in bucket
            file_path: Local path to save file

        Returns:
            Local file path where file was saved
        """
        try:
            self.client.fget_object(bucket, object_name, file_path)
            return file_path
        except S3Error as e:
            raise Exception(f"MinIO download failed: {e}")

    def download_temp(self, bucket: str, object_name: str) -> str:
        """
        Download file to /tmp with automatic naming

        Args:
            bucket: Bucket name
            object_name: Object path in bucket

        Returns:
            Path to downloaded temp file
        """
        # Create safe temp filename
        temp_name = object_name.replace('/', '_')
        temp_path = f"/tmp/{temp_name}"

        return self.download_file(bucket, object_name, temp_path)

    def get_object_url(self, bucket: str, object_name: str, expires: int = 3600) -> str:
        """
        Get a presigned URL for temporary access to an object

        Args:
            bucket: Bucket name
            object_name: Object path in bucket
            expires: Expiration time in seconds (default 1 hour)

        Returns:
            Presigned URL
        """
        try:
            url = self.client.presigned_get_object(bucket, object_name, expires=expires)
            return url
        except S3Error as e:
            raise Exception(f"MinIO presigned URL failed: {e}")

    def delete_object(self, bucket: str, object_name: str):
        """Delete an object from MinIO"""
        try:
            self.client.remove_object(bucket, object_name)
        except S3Error as e:
            raise Exception(f"MinIO delete failed: {e}")

    def list_objects(self, bucket: str, prefix: Optional[str] = None):
        """List objects in a bucket with optional prefix filter"""
        try:
            objects = self.client.list_objects(bucket, prefix=prefix)
            return [obj.object_name for obj in objects]
        except S3Error as e:
            raise Exception(f"MinIO list failed: {e}")


# Global storage instance
storage = StorageService()
