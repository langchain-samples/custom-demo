"""The assistant's versioned documents, as four routes over HTTP.

`GET /docs-files` lists them, `GET /docs-file` reads one (at a revision, when asked),
`POST /docs-file` saves an edit a person made, and `GET /docs-versions` lists the
revisions of one document. The store itself is `resources/docs.py`; what is here is the
HTTP shape: which parameter is missing, which failure is which status code, and keeping
Hub's blocking client off the event loop.

Status codes come from the exception TYPE, never from reading its message. A document
that is not there is a 404 the editor renders as "no such document", a save that lost a
race is a 409 the editor renders as "reload to see the other edit", and a Hub that
cannot be reached is a 502 that must not be mistaken for either.
"""

from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse

from custom_demo.config import load_env
from custom_demo.resources.docs import (
    DocumentConflict,
    DocumentNotFound,
    DocumentStoreError,
    list_documents,
    list_versions,
    read_document,
    write_document,
)
from custom_demo.web.errors import err, route_error

# A document is prose, and a person edits it in a browser. Anything past this is not an
# edit someone typed, so refusing it protects the repo from a runaway paste.
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


def _missing_repo() -> JSONResponse:
    """The one answer every route here owes a caller that named no documents repo."""
    return err(
        400,
        "no_docs_repo",
        "This assistant has no documents repo, so it has no versioned documents.",
    )


def _store_failure(exc: DocumentStoreError) -> JSONResponse:
    """Map a store failure to the status code its type means."""
    if isinstance(exc, DocumentConflict):
        return err(409, "conflict", str(exc))

    if isinstance(exc, DocumentNotFound):
        return err(404, "not_found", str(exc))

    return err(502, "store_unavailable", str(exc))


@route_error
async def docs_files(request):
    """GET /docs-files: every document in this assistant's repo, with its folder."""
    load_env()
    repo = request.query_params.get("docs_repo") or ""
    if not repo:
        return _missing_repo()

    ws = request.query_params.get("workspace") or None
    try:
        listing = await asyncio.to_thread(list_documents, repo, ws)
    except DocumentStoreError as exc:
        return _store_failure(exc)

    return JSONResponse(listing.model_dump())


@route_error
async def docs_file(request):
    """GET /docs-file: one document, at `version` when given, else at HEAD."""
    load_env()
    repo = request.query_params.get("docs_repo") or ""
    if not repo:
        return _missing_repo()

    path = request.query_params.get("path") or ""
    if not path:
        return err(400, "no_path", "Name the document to read.")

    ws = request.query_params.get("workspace") or None
    version = request.query_params.get("version") or None
    try:
        document = await asyncio.to_thread(read_document, repo, path, ws, version)
    except DocumentStoreError as exc:
        return _store_failure(exc)

    return JSONResponse(document.model_dump())


@route_error
async def docs_save(request):
    """POST /docs-file: save an edit as the next revision of one document.

    Body: {docs_repo, workspace?, path, content, message?, author?, role?, base_version?}.
    `base_version` is what the editor loaded; sending it is what turns a lost race
    into a 409 instead of a silent overwrite.
    """
    load_env()
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an unreadable body is a client error; answer 400, not 500
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    repo = body.get("docs_repo") or ""
    if not repo:
        return _missing_repo()

    path = body.get("path") or ""
    if not path:
        return err(400, "no_path", "Name the document to save.")

    content = body.get("content")
    if not isinstance(content, str):
        return err(400, "no_content", "A save needs the document's text.")

    if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        return err(
            413,
            "too_large",
            f"That document is larger than the {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB limit.",
        )

    try:
        saved = await asyncio.to_thread(
            write_document,
            repo,
            path,
            content,
            body.get("workspace") or None,
            str(body.get("message") or ""),
            str(body.get("author") or ""),
            body.get("base_version") or None,
            str(body.get("role") or ""),
        )
    except DocumentStoreError as exc:
        return _store_failure(exc)

    return JSONResponse(saved.model_dump())


@route_error
async def docs_versions(request):
    """GET /docs-versions: revisions in which one document's content changed."""
    load_env()
    repo = request.query_params.get("docs_repo") or ""
    if not repo:
        return _missing_repo()

    path = request.query_params.get("path") or ""
    if not path:
        return err(400, "no_path", "Name the document whose revisions you want.")

    ws = request.query_params.get("workspace") or None
    try:
        versions = await asyncio.to_thread(list_versions, repo, path, ws)
    except DocumentStoreError as exc:
        return _store_failure(exc)

    return JSONResponse({"path": path, "versions": [v.model_dump() for v in versions]})
