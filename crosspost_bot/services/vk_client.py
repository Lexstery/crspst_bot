from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Iterable, Optional

import vk_api


LOGGER = logging.getLogger(__name__)

TOKEN_PATTERN = re.compile(r"access_token=([a-zA-Z0-9._-]+)")


def extract_token_from_url(value: str) -> Optional[str]:
    if not value:
        return None
    match = TOKEN_PATTERN.search(value)
    if match:
        return match.group(1)
    if len(value) > 80 and "vk1." in value:
        return value
    return None


class VKClient:
    def __init__(self, token: str):
        self._token = token
        self._vk_session = vk_api.VkApi(token=token)
        self._api = self._vk_session.get_api()
        self._upload = vk_api.VkUpload(self._vk_session)

    def update_token(self, token: str) -> None:
        self._token = token
        self._vk_session = vk_api.VkApi(token=token)
        self._api = self._vk_session.get_api()
        self._upload = vk_api.VkUpload(self._vk_session)

    def validate(self) -> bool:
        try:
            self._api.utils.getServerTime()
            return True
        except vk_api.ApiError as exc:
            LOGGER.error("VK token validation failed: %s", exc)
            return False

    @staticmethod
    def _normalize_group_id(group_id: str) -> int:
        group_id = group_id.strip()
        if group_id.startswith("-"):
            return int(group_id)
        if group_id.startswith("club"):
            group_id = group_id[4:]
        return -abs(int(group_id))

    def post_to_group(
        self,
        *,
        group_id: str,
        message: Optional[str],
        photo_files: Optional[Iterable[tuple[str, bytes]]] = None,
    ) -> dict:
        owner_id = self._normalize_group_id(group_id)
        attachments: list[str] = []
        if photo_files:
            temp_files = []
            try:
                for idx, (filename, data) in enumerate(photo_files):
                    suffix = os.path.splitext(filename or "photo.jpg")[1] or ".jpg"
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=suffix, prefix=f"vk_upload_{idx}_"
                    ) as tmp:
                        tmp.write(data)
                        temp_files.append(tmp.name)
                try:
                    uploaded = self._upload.photo_wall(
                        photos=temp_files, group_id=abs(owner_id)
                    )
                    for photo in uploaded:
                        attachments.append(f"photo{photo['owner_id']}_{photo['id']}")
                except vk_api.ApiError as exc:
                    LOGGER.exception("Failed to upload VK photo: %s", exc)
                    raise
            finally:
                for tmp_path in temp_files:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
        try:
            response = self._api.wall.post(
                owner_id=owner_id,
                message=message or "",
                attachments=",".join(attachments) if attachments else None,
                from_group=True,
            )
            LOGGER.info("VK post created: %s", response)
            return response
        except vk_api.ApiError as exc:
            LOGGER.exception("Failed to post in VK: %s", exc)
            raise


