#!/usr/bin/env python3
"""Fail when reachable Git history contains common secrets or private markers."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


MAX_BLOB_BYTES = 5 * 1024 * 1024
PUBLIC_REVISIONS = ("--branches", "--tags")
PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "OpenAI-style token": re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "hard-coded credential": re.compile(
        rb"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd)\s*[:=]\s*['\"][^'\"\s]{8,}"
    ),
    "personal macOS path": re.compile(rb"/Users/(?!example(?:/|$))[A-Za-z0-9._-]+/"),
    "private IPv4 address": re.compile(
        rb"(?<![0-9])(?:10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2}|172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2})(?![0-9])"
    ),
}
BANNED_LITERALS = tuple(
    value.encode()
    for value in (
        "C9A4" + "EK8TYH",
        "/Users/" + "dp/",
        "xiaoyeqiannian" + "@163.com",
    )
)
EMAIL = re.compile(
    rb"(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}"
    rb"@[A-Za-z][A-Za-z0-9.-]*\.[A-Za-z]{2,}(?![A-Za-z0-9._%+-])"
)
ALLOWED_EMAIL_SUFFIX = b"@users.noreply.github.com"


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), stderr=subprocess.DEVNULL)


def reachable_blobs() -> list[tuple[str, str]]:
    blobs: dict[str, str] = {}
    for raw in git("rev-list", "--objects", *PUBLIC_REVISIONS).decode("utf-8", "replace").splitlines():
        object_id, _, path = raw.partition(" ")
        if path and git("cat-file", "-t", object_id).strip() == b"blob":
            blobs.setdefault(object_id, path)
    return sorted(blobs.items(), key=lambda item: item[1])


def scan_data(path: str, data: bytes, findings: list[str]) -> None:
    if len(data) > MAX_BLOB_BYTES or b"\0" in data[:8192]:
        return
    for label, pattern in PATTERNS.items():
        if pattern.search(data):
            findings.append(f"{path}: {label}")
    if any(value in data for value in BANNED_LITERALS):
        findings.append(f"{path}: known private marker")
    for email in EMAIL.findall(data):
        if not email.endswith(ALLOWED_EMAIL_SUFFIX):
            findings.append(f"{path}: email address")
            break


def main() -> int:
    findings: list[str] = []
    for email in git("log", *PUBLIC_REVISIONS, "--format=%ae").splitlines():
        if email and not email.endswith(ALLOWED_EMAIL_SUFFIX):
            findings.append("commit metadata: non-noreply author email")
            break
    blobs = reachable_blobs()
    for object_id, path in blobs:
        size = int(git("cat-file", "-s", object_id))
        if size > MAX_BLOB_BYTES:
            continue
        scan_data(path, git("cat-file", "blob", object_id), findings)
    for raw_path in git("ls-files", "-z").split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", "surrogateescape")
        try:
            data = Path(path).read_bytes()
        except OSError as error:
            findings.append(f"{path}: cannot read tracked file ({error})")
            continue
        scan_data(path, data, findings)
    if findings:
        print("Privacy audit failed:", file=sys.stderr)
        for finding in sorted(set(findings)):
            print(f"- {finding}", file=sys.stderr)
        return 1
    print(f"Privacy audit passed: {len(blobs)} reachable blobs and the tracked working tree scanned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
