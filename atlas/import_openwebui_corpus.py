#!/usr/bin/env python3

import argparse
import contextlib
import errno
import fcntl
import json
import mimetypes
import os
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def load_api_key(db_path: pathlib.Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            select ak.key
            from api_key ak
            join user u on u.id = ak.user_id
            where ak.key like 'sk-%' and u.role = 'admin'
            order by ak.created_at asc
            limit 1
            """
        )
        row = cur.fetchone()
        if not row or not row[0]:
            raise RuntimeError(f"No Open WebUI API key found in {db_path}")
        return row[0]
    finally:
        conn.close()


def api_request(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    data=None,
    content_type=None,
    timeout: int = 300,
):
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    headers = {"Authorization": f"Bearer {api_key}"}

    body = None
    if data is not None:
        body = data
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif isinstance(data, str):
            body = data.encode("utf-8")
        if content_type:
            headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc


def api_request_with_retry(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    data=None,
    content_type=None,
    timeout: int = 300,
    max_retries: int = 3,
    retry_delay: float = 2.0,
):
    attempt = 0
    while True:
        attempt += 1
        try:
            return api_request(
                base_url,
                api_key,
                method,
                path,
                data=data,
                content_type=content_type,
                timeout=timeout,
            )
        except (TimeoutError, urllib.error.URLError, OSError, RuntimeError) as exc:
            retryable = True
            if isinstance(exc, RuntimeError):
                retryable = any(code in str(exc) for code in (" 429 ", " 500 ", " 502 ", " 503 ", " 504 "))

            if not retryable or attempt >= max_retries:
                raise

            print(
                f"RETRY\tattempt={attempt}\tmethod={method}\tpath={path}\terror={exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(retry_delay * attempt)


def encode_multipart(fields, file_path: pathlib.Path, file_field="file"):
    boundary = f"----CodexBoundary{uuid.uuid4().hex}"
    chunks = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        if isinstance(value, str):
            chunks.append(value.encode("utf-8"))
        else:
            chunks.append(json.dumps(value).encode("utf-8"))
        chunks.append(b"\r\n")

    mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\n'
        ).encode("utf-8")
    )
    chunks.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    chunks.append(file_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))

    return boundary, b"".join(chunks)


def get_or_create_knowledge(
    base_url: str,
    api_key: str,
    name: str,
    description: str,
    timeout: int,
    max_retries: int,
    retry_delay: float,
) -> str:
    page = 1
    while True:
        result = api_request_with_retry(
            base_url,
            api_key,
            "GET",
            f"/api/v1/knowledge/?page={page}",
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        ) or {}
        items = result.get("items", [])
        for item in items:
            if item.get("name") == name:
                return item["id"]
        total = result.get("total", 0)
        if page * 30 >= total or not items:
            break
        page += 1

    created = api_request_with_retry(
        base_url,
        api_key,
        "POST",
        "/api/v1/knowledge/create",
        {"name": name, "description": description, "access_grants": []},
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    return created["id"]


def list_knowledge_files(
    base_url: str,
    api_key: str,
    knowledge_id: str,
    timeout: int,
    max_retries: int,
    retry_delay: float,
):
    page = 1
    files = []
    while True:
        result = api_request_with_retry(
            base_url,
            api_key,
            "GET",
            f"/api/v1/knowledge/{knowledge_id}/files?page={page}",
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        ) or {}
        items = result.get("items", [])
        files.extend(items)
        total = result.get("total", 0)
        if page * 30 >= total or not items:
            break
        page += 1
    return files


def clear_knowledge(base_url: str, api_key: str, knowledge_id: str, timeout: int, max_retries: int, retry_delay: float):
    existing = list_knowledge_files(base_url, api_key, knowledge_id, timeout, max_retries, retry_delay)
    api_request_with_retry(
        base_url,
        api_key,
        "POST",
        f"/api/v1/knowledge/{knowledge_id}/reset",
        {},
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    return len(existing)


def upload_file(
    base_url: str,
    api_key: str,
    knowledge_id: str,
    file_path: pathlib.Path,
    timeout: int,
    max_retries: int,
    retry_delay: float,
):
    metadata = {
        "source": "homelab-corpus",
        "knowledge_id": knowledge_id,
        "original_path": str(file_path),
    }
    boundary, body = encode_multipart({"metadata": json.dumps(metadata)}, file_path)
    result = api_request_with_retry(
        base_url,
        api_key,
        "POST",
        "/api/v1/files/?process=true&process_in_background=false",
        body,
        content_type=f"multipart/form-data; boundary={boundary}",
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    return result["id"]


def attach_file(
    base_url: str,
    api_key: str,
    knowledge_id: str,
    file_id: str,
    timeout: int,
    max_retries: int,
    retry_delay: float,
):
    api_request_with_retry(
        base_url,
        api_key,
        "POST",
        f"/api/v1/knowledge/{knowledge_id}/file/add",
        {"file_id": file_id},
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )


def iter_files(corpus_dir: pathlib.Path):
    for path in sorted(corpus_dir.rglob("*")):
        if path.is_file() and path.stat().st_size > 0:
            yield path


@contextlib.contextmanager
def exclusive_lock(lock_path: pathlib.Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise RuntimeError(f"Another import is already running (lock: {lock_path})") from exc
            raise
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def reconfigure_stdio():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)


def main():
    reconfigure_stdio()

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--webui-db", default="/opt/stacks/ai-stack/webui_data/webui.db")
    parser.add_argument("--corpus-dir", required=True)
    parser.add_argument("--knowledge-name", default="Homelab Source of Truth")
    parser.add_argument(
        "--knowledge-description",
        default="Canonical homelab documentation, inventories, plans, configs, and change log.",
    )
    parser.add_argument("--lock-file", default="/tmp/homelab-openwebui-import.lock")
    parser.add_argument("--request-timeout", type=int, default=900)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    args = parser.parse_args()

    corpus_dir = pathlib.Path(args.corpus_dir)
    db_path = pathlib.Path(args.webui_db)
    lock_path = pathlib.Path(args.lock_file)

    if not corpus_dir.is_dir():
        raise SystemExit(f"Corpus directory not found: {corpus_dir}")
    if not db_path.is_file():
        raise SystemExit(f"Open WebUI DB not found: {db_path}")

    files_to_import = list(iter_files(corpus_dir))
    expected_files = len(files_to_import)

    with exclusive_lock(lock_path):
        api_key = load_api_key(db_path)
        knowledge_id = get_or_create_knowledge(
            args.base_url,
            api_key,
            args.knowledge_name,
            args.knowledge_description,
            args.request_timeout,
            args.max_retries,
            args.retry_delay,
        )
        removed = clear_knowledge(
            args.base_url,
            api_key,
            knowledge_id,
            args.request_timeout,
            args.max_retries,
            args.retry_delay,
        )

        print(
            f"START\tknowledge_id={knowledge_id}\texpected_files={expected_files}\tremoved_existing_files={removed}",
            flush=True,
        )

        uploaded = 0
        attached = 0
        failures = []

        for index, file_path in enumerate(files_to_import, start=1):
            try:
                file_id = upload_file(
                    args.base_url,
                    api_key,
                    knowledge_id,
                    file_path,
                    args.request_timeout,
                    args.max_retries,
                    args.retry_delay,
                )
                uploaded += 1
                attach_file(
                    args.base_url,
                    api_key,
                    knowledge_id,
                    file_id,
                    args.request_timeout,
                    args.max_retries,
                    args.retry_delay,
                )
                attached += 1
                print(f"IMPORTED\t{index}/{expected_files}\t{file_path}", flush=True)
            except Exception as exc:
                failures.append((str(file_path), str(exc)))
                print(f"FAILED\t{index}/{expected_files}\t{file_path}\t{exc}", file=sys.stderr, flush=True)

        attached_now = len(
            list_knowledge_files(
                args.base_url,
                api_key,
                knowledge_id,
                args.request_timeout,
                args.max_retries,
                args.retry_delay,
            )
        )

        summary = {
            "knowledge_name": args.knowledge_name,
            "knowledge_id": knowledge_id,
            "removed_existing_files": removed,
            "expected_files": expected_files,
            "uploaded": uploaded,
            "attached": attached,
            "attached_in_knowledge": attached_now,
            "failed": len(failures),
            "failures": failures,
        }
        print(json.dumps(summary, indent=2), flush=True)

        if failures or attached_now != expected_files:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
