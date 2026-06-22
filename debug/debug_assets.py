#!/usr/bin/env python3
"""
debug_assets.py — smoke + regression tests for the /assets API.

Covers:
- mkdir, ls at multiple levels (/, /tenant, /tenant/collection, deeper)
- upload (implicit version bump), download latest
- download explicit versions (foo.1.pdf, foo.2.pdf, ...)
- overwrite=false behavior (implicit path should 409 once asset exists)
- explicit version uploads (foo.10.pdf, foo.5.pdf) + latest semantics
- overwrite=false behavior on explicit version (should 409 if that version exists)
- mv (metadata-only move) and subsequent version/download behavior
- rm single version + rm latest version + rm whole asset
- negative tests (404s, 409s)

Run:
  python3 debug_assets.py --base http://localhost:8080

Notes:
- Safe to run repeatedly: creates a unique subdir per run by default.
- Use --fixed to reuse the literal --subdir path (not recommended unless debugging).
"""

import argparse
import json
import os
import sys
import time
from io import BytesIO
from typing import Any, Dict, Iterable, Optional, Tuple

import requests


# ---------- helpers ----------

def jprint(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def must(resp: requests.Response, expected: Iterable[int] = (200,)) -> requests.Response:
    exp = set(expected)
    if resp.status_code not in exp:
        print(f"\nERROR {resp.request.method} {resp.url}")
        print(f"Status: {resp.status_code} (expected {sorted(exp)})")
        try:
            jprint(resp.json())
        except Exception:
            print(resp.text)
        sys.exit(1)
    return resp


def assert_eq(got: Any, want: Any, msg: str) -> None:
    if got != want:
        raise AssertionError(f"{msg}\nGOT:  {got!r}\nWANT: {want!r}")


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def now_tag() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def req(method: str, url: str, **kwargs) -> requests.Response:
    return requests.request(method, url, **kwargs)


# ---------- API wrappers ----------

def mkdir(base: str, path: str) -> Dict[str, Any]:
    print(f"\n== mkdir {path}")
    r = req("POST", f"{base}/assets/mkdir", params={"path": path}, timeout=30)
    must(r, (200,))
    jprint(r.json())
    return r.json()


def ls(base: str, path: str, include_markers: bool = False) -> Dict[str, Any]:
    print(f"\n== ls {path} (include_markers={include_markers})")
    r = req(
        "GET",
        f"{base}/assets/ls",
        params={"path": path, "include_markers": str(include_markers).lower()},
        timeout=30,
    )
    must(r, (200,))
    jprint(r.json())
    return r.json()


def upload(
    base: str,
    logical_path: str,
    content: bytes,
    title: Optional[str] = None,
    overwrite: bool = True,
) -> Dict[str, Any]:
    print(f"\n== upload {logical_path} (overwrite={overwrite})")
    files = {"file": ("payload.bin", BytesIO(content), "application/octet-stream")}
    params: Dict[str, Any] = {"path": logical_path, "overwrite": str(overwrite).lower()}
    if title:
        params["title"] = title
    r = req("POST", f"{base}/assets", params=params, files=files, timeout=60)
    must(r, (200,))
    jprint(r.json())
    return r.json()


def upload_expect_status(
    base: str,
    logical_path: str,
    content: bytes,
    title: Optional[str],
    overwrite: bool,
    expected_status: Tuple[int, ...],
) -> requests.Response:
    print(f"\n== upload(expect {expected_status}) {logical_path} (overwrite={overwrite})")
    files = {"file": ("payload.bin", BytesIO(content), "application/octet-stream")}
    params: Dict[str, Any] = {"path": logical_path, "overwrite": str(overwrite).lower()}
    if title:
        params["title"] = title
    r = req("POST", f"{base}/assets", params=params, files=files, timeout=60)
    must(r, expected_status)
    if r.status_code != 200:
        try:
            jprint(r.json())
        except Exception:
            print(r.text)
    else:
        jprint(r.json())
    return r


def download(base: str, logical_path: str) -> bytes:
    print(f"\n== download {logical_path}")
    r = req("GET", f"{base}/assets", params={"path": logical_path}, timeout=60)
    must(r, (200,))
    print(f"Downloaded {len(r.content)} bytes, content-type={r.headers.get('content-type')}")
    return r.content


def download_expect_status(base: str, logical_path: str, expected_status: Tuple[int, ...]) -> requests.Response:
    print(f"\n== download(expect {expected_status}) {logical_path}")
    r = req("GET", f"{base}/assets", params={"path": logical_path}, timeout=60)
    must(r, expected_status)
    if r.status_code != 200:
        try:
            jprint(r.json())
        except Exception:
            print(r.text)
    else:
        print(f"Downloaded {len(r.content)} bytes, content-type={r.headers.get('content-type')}")
    return r


def download_by_id(base: str, document_id: str) -> bytes:
    print(f"\n== download by-id {document_id}")
    r = req("GET", f"{base}/assets/by-id/{document_id}", timeout=60)
    must(r, (200,))
    print(f"Downloaded {len(r.content)} bytes, content-type={r.headers.get('content-type')}")
    return r.content


def mv(base: str, src: str, dst: str) -> Dict[str, Any]:
    print(f"\n== mv {src} -> {dst}")
    r = req("POST", f"{base}/assets/mv", params={"src": src, "dst": dst}, timeout=30)
    must(r, (200,))
    jprint(r.json())
    return r.json()


def rm(base: str, path: str) -> Dict[str, Any]:
    print(f"\n== rm {path}")
    r = req("DELETE", f"{base}/assets", params={"path": path}, timeout=60)
    must(r, (200,))
    jprint(r.json())
    return r.json()


def rm_expect_status(base: str, path: str, expected_status: Tuple[int, ...]) -> requests.Response:
    print(f"\n== rm(expect {expected_status}) {path}")
    r = req("DELETE", f"{base}/assets", params={"path": path}, timeout=60)
    must(r, expected_status)
    if r.status_code != 200:
        try:
            jprint(r.json())
        except Exception:
            print(r.text)
    else:
        jprint(r.json())
    return r


# ---------- test plan ----------

def split_stem_ext(filename: str) -> Tuple[str, str]:
    if "." not in filename:
        raise ValueError("filename must have an extension (e.g. foo.pdf)")
    stem, ext = filename.rsplit(".", 1)
    return stem, ext


def make_versioned_path(dir_path: str, stem: str, ver: int, ext: str) -> str:
    return f"{dir_path}/{stem}.{ver}.{ext}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:18080", help="Base URL, e.g. http://localhost:8080")
    ap.add_argument("--tenant", default="ethz")
    ap.add_argument("--collection", default="physics")
    ap.add_argument("--subdir", default="mechanics/demo")
    ap.add_argument("--filename", default="angular.pdf")
    ap.add_argument("--fixed", action="store_true", help="Reuse --subdir without unique per-run suffix.")
    ap.add_argument("--cleanup", action="store_true", help="Best-effort delete created assets at end.")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    stem, ext = split_stem_ext(args.filename)

    run_suffix = "" if args.fixed else f"/_run_{now_tag()}_{os.getpid()}"
    dir_path = f"/{args.tenant}/{args.collection}/{args.subdir}{run_suffix}".replace("//", "/")
    file_path = f"{dir_path}/{args.filename}"

    moved_dir = f"/{args.tenant}/{args.collection}/mechanics/moved{run_suffix}".replace("//", "/")
    moved_file_path = f"{moved_dir}/{args.filename}"

    # We'll keep track of logical paths we touched, for cleanup.
    touched_paths = set()

    banner("Assets API debug test")
    print(f"BASE  = {base}")
    print(f"DIR   = {dir_path}")
    print(f"FILE  = {file_path}")
    print(f"MOVED = {moved_file_path}")

    # 1) mkdir
    banner("1) mkdir + ls at multiple levels")
    mkdir(base, dir_path)

    root = ls(base, "/")
    assert_true(args.tenant in root["directories"], "Tenant should appear under / after mkdir")

    t = ls(base, f"/{args.tenant}")
    assert_true(args.collection in t["directories"], "Collection should appear under /tenant after mkdir")

    ls(base, f"/{args.tenant}/{args.collection}")
    ls(base, dir_path)

    # sanity: include markers listing (should not error; presence depends on your marker strategy)
    ls(base, dir_path, include_markers=True)

    # 2) Upload v1 (implicit)
    banner("2) upload v1 (implicit) + download latest")
    v1_bytes = b"hello version 1\n"
    v1 = upload(base, file_path, v1_bytes, title="Demo Asset v1", overwrite=True)
    touched_paths.add(file_path)

    assert_true("document_id" in v1, "upload response must include document_id")
    assert_eq(v1.get("version"), 1, "first upload should be version 1")

    got = download(base, file_path)
    assert_eq(got, v1_bytes, "Downloaded bytes did not match v1 upload")

    # 3) overwrite=false semantics (implicit path)
    banner("3) overwrite=false on implicit path should 409 once asset exists")
    upload_expect_status(
        base,
        file_path,
        b"nope\n",
        title="should conflict",
        overwrite=False,
        expected_status=(409,),
    )

    # 4) Upload v2 and verify latest + explicit reads
    banner("4) upload v2 (implicit) + download latest + download .1/.2")
    v2_bytes = b"hello version 2\n"
    v2 = upload(base, file_path, v2_bytes, title="Demo Asset v2", overwrite=True)
    assert_eq(v2.get("version"), 2, "second upload should be version 2")

    got2 = download(base, file_path)
    assert_eq(got2, v2_bytes, "Downloaded bytes did not match v2 upload")

    v1_path = make_versioned_path(dir_path, stem, 1, ext)
    v2_path = make_versioned_path(dir_path, stem, 2, ext)
    touched_paths.update([v1_path, v2_path])

    got_v1 = download(base, v1_path)
    got_v2 = download(base, v2_path)
    assert_eq(got_v1, v1_bytes, "Versioned download .1 did not match v1 content")
    assert_eq(got_v2, v2_bytes, "Versioned download .2 did not match v2 content")

    # by-id should match v2 bytes
    banner("5) download by-id (v2)")
    got_by_id = download_by_id(base, v2["document_id"])
    assert_eq(got_by_id, v2_bytes, "download_by_id should match v2 bytes")

    # 6) Explicit version upload tests (.10 and .5)
    banner("6) explicit version uploads (.10 then .5) + latest semantics + overwrite=false conflict")
    v10_path = make_versioned_path(dir_path, stem, 10, ext)
    v5_path = make_versioned_path(dir_path, stem, 5, ext)
    touched_paths.update([v10_path, v5_path])

    v10_bytes = b"hello version 10\n"
    v10 = upload(base, v10_path, v10_bytes, title="Demo Asset v10", overwrite=True)
    assert_eq(v10.get("version"), 10, "explicit upload foo.10.ext should become version 10")

    # latest should now be v10
    got_latest = download(base, file_path)
    assert_eq(got_latest, v10_bytes, "latest should be v10 after uploading explicit v10")

    v5_bytes = b"hello version 5\n"
    v5 = upload(base, v5_path, v5_bytes, title="Demo Asset v5", overwrite=True)
    assert_eq(v5.get("version"), 5, "explicit upload foo.5.ext should become version 5")

    # latest should remain v10 (since 10 > 5)
    got_latest2 = download(base, file_path)
    assert_eq(got_latest2, v10_bytes, "latest should remain v10 after uploading explicit v5")

    # overwrite=false on an explicit version that exists should 409
    upload_expect_status(
        base,
        v5_path,
        b"should not replace v5\n",
        title="v5 conflict",
        overwrite=False,
        expected_status=(409,),
    )

    # 7) Move the asset and verify old path 404, new path works, versions still reachable
    banner("7) mv (metadata) + verify behavior on new location")
    mkdir(base, moved_dir)
    mv_resp = mv(base, file_path, moved_file_path)
    assert_true(mv_resp.get("moved") is True, "mv should return moved=true")

    download_expect_status(base, file_path, expected_status=(404,))
    got_moved_latest = download(base, moved_file_path)
    assert_eq(got_moved_latest, v10_bytes, "after mv, latest should still be v10")

    # versions should work at new location
    moved_v1 = make_versioned_path(moved_dir, stem, 1, ext)
    moved_v2 = make_versioned_path(moved_dir, stem, 2, ext)
    moved_v5 = make_versioned_path(moved_dir, stem, 5, ext)
    moved_v10 = make_versioned_path(moved_dir, stem, 10, ext)
    touched_paths.update([moved_file_path, moved_v1, moved_v2, moved_v5, moved_v10])

    assert_eq(download(base, moved_v1), v1_bytes, "v1 mismatch after mv")
    assert_eq(download(base, moved_v2), v2_bytes, "v2 mismatch after mv")
    assert_eq(download(base, moved_v5), v5_bytes, "v5 mismatch after mv")
    assert_eq(download(base, moved_v10), v10_bytes, "v10 mismatch after mv")

    # 8) Upload again after move -> should create version 11 if using implicit overwrite semantics
    banner("8) upload after mv (implicit) should create v11 and become latest")
    v11_bytes = b"hello version 11\n"
    v11 = upload(base, moved_file_path, v11_bytes, title="Demo Asset v11", overwrite=True)
    assert_eq(v11.get("version"), 11, "implicit overwrite after v10 should create v11")
    assert_eq(download(base, moved_file_path), v11_bytes, "latest should be v11")

    moved_v11 = make_versioned_path(moved_dir, stem, 11, ext)
    touched_paths.add(moved_v11)
    assert_eq(download(base, moved_v11), v11_bytes, "v11 versioned download mismatch")

    # 9) Delete latest version and ensure latest recomputes to previous (v10)
    banner("9) rm latest version (v11) should recompute latest to v10")
    rm(base, moved_v11)
    download_expect_status(base, moved_v11, expected_status=(404,))
    assert_eq(download(base, moved_file_path), v10_bytes, "latest should recompute to v10 after deleting v11")

    # 10) Delete a non-latest version (v1) and ensure others remain
    banner("10) rm v1 (non-latest) and verify v2/v10 still exist")
    rm(base, moved_v1)
    download_expect_status(base, moved_v1, expected_status=(404,))
    assert_eq(download(base, moved_v2), v2_bytes, "v2 should still exist after deleting v1")
    assert_eq(download(base, moved_file_path), v10_bytes, "latest should still be v10 after deleting v1")

    # 11) Delete whole asset and verify 404s
    banner("11) rm whole asset + verify 404s")
    rm(base, moved_file_path)
    download_expect_status(base, moved_file_path, expected_status=(404,))
    download_expect_status(base, moved_v10, expected_status=(404,))
    download_expect_status(base, moved_v2, expected_status=(404,))

    # negative rm
    rm_expect_status(base, moved_file_path, expected_status=(404,))

    # Cleanup best-effort (only if requested)
    if args.cleanup:
        banner("cleanup (best effort)")
        for p in sorted(touched_paths, key=len, reverse=True):
            try:
                rm_expect_status(base, p, expected_status=(200, 404))
            except SystemExit:
                # don't abort cleanup
                pass

    banner("All tests passed ✅")


if __name__ == "__main__":
    main()

