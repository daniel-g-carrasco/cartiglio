#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create, verify and audit versioned inventories for distributed artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
import tempfile
from configparser import ConfigParser
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

SUFFIX = ".inventory.json"
SCHEMA = 1
ARTIFACT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(?:exe|zip|flatpak|tar\.gz|whl)")
SCANNED = {"PyPI", "Go"}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def component(ecosystem, name, version):
    if ecosystem == "PyPI":
        name = re.sub(r"[-_.]+", "-", name).lower()
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+!~-]*", value)
               for value in (ecosystem, name, version)):
        raise ValueError("invalid component ecosystem, name or version")
    return ecosystem, name, version


def flatpak_components(root, runtime_commit=None):
    ini = ConfigParser(interpolation=None)
    with (root / "metadata").open(encoding="utf-8") as stream:
        ini.read_file(stream)
    runtime = ini["Application"]["runtime"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", runtime):
        raise ValueError("invalid Flatpak runtime reference")
    if runtime_commit is None:
        runtime_commit = subprocess.check_output(
            ["flatpak", "info", "--show-commit", "runtime/" + runtime], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", runtime_commit):
        raise ValueError("invalid Flatpak runtime commit")
    roots = sorted((root / "files").glob("lib*/python*/site-packages"))
    wheels = [component("PyPI", dist.metadata["Name"], dist.version)
              for dist in importlib.metadata.distributions(path=roots)]
    return [*wheels, component("Flatpak", runtime, runtime_commit)]


def windows_components(path, *, gtk):
    data = json.loads(path.read_text(encoding="utf-8"))
    if gtk:
        if data["schema_version"] != 1:
            raise ValueError("unsupported Windows bundle inventory")
        ecosystems = {"wheel": "PyPI", "msys2": "MSYS2"}
        return [component(ecosystems[row["kind"]], row["name"], row["version"])
                for row in data["components"].values() if row["kind"] in ecosystems]
    return [component("PyPI", row["name"], row["version"]) for row in data]


def make_report(artifact, revision, components, platform):
    if not ARTIFACT.fullmatch(artifact.name) or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("invalid artifact name or revision")
    return {"schema_version": SCHEMA, "artifact": artifact.name, "sha256": sha256(artifact),
            "revision": revision, "platform": platform,
            "components": [dict(zip(("ecosystem", "name", "version"), row, strict=True))
                           for row in sorted(set(components))]}


def artifact_platform(name):
    if name.endswith(".flatpak"):
        return "flatpak"
    if name.endswith(".tar.gz"):
        return "linux"
    if name.endswith(".whl"):
        return "wheel"
    return "windows"


def read_report(path, name, checksum, revision=None):
    with path.open(encoding="utf-8") as stream:
        report = json.loads(stream.read(2_000_000))
    if report["schema_version"] != SCHEMA or report["artifact"] != name:
        raise ValueError(f"{name}: incorrect inventory schema or artifact name")
    if (not re.fullmatch(r"[a-f0-9]{64}", report["sha256"]) or report["sha256"] != checksum):
        raise ValueError(f"{name}: artifact SHA-256 differs from the inventory")
    sha = report["revision"]
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or (revision is not None and sha != revision):
        raise ValueError(f"{name}: artifact revision differs from the expected commit")
    if report["platform"] != artifact_platform(name) or not isinstance(report["components"], list):
        raise ValueError(f"{name}: wrong platform or missing component list")
    for row in report["components"]:
        component(row["ecosystem"], row["name"], row["version"])
    return report


def check(artifacts, revision=None):
    return [read_report(path.with_name(path.name + SUFFIX), path.name, sha256(path), revision)
            for path in artifacts]


def osv(payload):
    request = Request("https://api.osv.dev/v1/querybatch", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read(8_000_000))


def advisories(components, query=osv):
    covered = sorted({row for row in components if row[0] in SCANNED})
    findings = set()
    for start in range(0, len(covered), 100):
        pending = [(row, {}) for row in covered[start:start + 100]]
        seen = set()
        while pending:
            queries = [{"package": {"ecosystem": kind, "name": name}, "version": version, **page}
                       for (kind, name, version), page in pending]
            results = query({"queries": queries})["results"]
            if not isinstance(results, list) or len(results) != len(pending):
                raise ValueError("OSV returned the wrong number of results")
            next_page = []
            for (row, _), result in zip(pending, results, strict=True):
                for item in result.get("vulns", []):
                    advisory = item["id"]
                    if not re.fullmatch(r"[A-Za-z0-9-]+", advisory):
                        raise ValueError("OSV returned an invalid advisory identifier")
                    findings.add((*row, advisory))
                token = result.get("next_page_token")
                if token:
                    if not isinstance(token, str) or (row, token) in seen:
                        raise ValueError("OSV returned an invalid pagination token")
                    seen.add((row, token))
                    next_page.append((row, {"page_token": token}))
            pending = next_page
    return sorted(findings)


def publishable_artifact(name):
    if not ARTIFACT.fullmatch(name):
        return False
    lowered = name.lower()
    if name.endswith(".tar.gz"):
        return "linux" in lowered and "portable" in lowered
    return not name.endswith(".zip") or ("source" not in lowered and
                                         any(part in lowered for part in ("portable", "windows")))


def release_reports(repo, tag):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository name")
    endpoint = "tags/" + quote(tag, safe="") if tag else "latest"
    result = subprocess.run(["gh", "api", f"repos/{repo}/releases/{endpoint}"],
                            capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode:
        if not tag and "(HTTP 404)" in result.stderr:
            subprocess.run(["gh", "api", f"repos/{repo}", "--silent"], check=True)
            print("No stable release published yet.")
            return []
        raise ValueError("cannot read GitHub release metadata")
    release = json.loads(result.stdout)
    if release["draft"] or release["prerelease"]:
        raise ValueError("artifact auditing expects a published stable release")
    assets = {item["name"]: item for item in release["assets"] if item["state"] == "uploaded"}
    names = sorted(name for name in assets if publishable_artifact(name))
    if not names:
        raise ValueError("the stable release has no recognized distribution artifacts")
    with tempfile.TemporaryDirectory(prefix="release-inventory-") as temp:
        reports = []
        for name in names:
            sidecar = name + SUFFIX
            digest = assets[name].get("digest") or ""
            if not digest.startswith("sha256:") or sidecar not in assets:
                raise ValueError(f"{name}: published digest or inventory is missing")
            subprocess.run(["gh", "release", "download", "--repo", repo, "--pattern", sidecar,
                            "--dir", temp, "--", release["tag_name"]], check=True)
            path = Path(temp) / sidecar
            if assets[sidecar].get("digest") != "sha256:" + sha256(path):
                raise ValueError(f"{name}: downloaded inventory differs from the GitHub digest")
            reports.append(read_report(path, name, digest.removeprefix("sha256:")))
        print(f"Verified {len(reports)} inventories against GitHub digests for {release['tag_name']}.")
        return reports


def report_advisories(reports):
    components = {component(row["ecosystem"], row["name"], row["version"])
                  for report in reports for row in report["components"]}
    unscanned = sorted({kind for kind, _, _ in components} - SCANNED)
    if unscanned:
        print("Inventoried without OSV coverage: " + ", ".join(unscanned))
    count = sum(kind in SCANNED for kind, _, _ in components)
    print(f"Components eligible for OSV lookup: {count}.")
    findings = advisories(components)
    for kind, name, version, advisory in findings:
        print(f"{kind} {name} {version}: https://osv.dev/vulnerability/{advisory}")
    return int(bool(findings))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--revision", required=True)
    sources = create.add_mutually_exclusive_group()
    for source in ("gtk-inventory", "qt-inventory", "linux-inventory", "wheel-inventory", "flatpak"):
        sources.add_argument("--" + source, type=Path)
    create.add_argument("--component", action="append", default=[], metavar="ECOSYSTEM:NAME=VERSION")
    verify = commands.add_parser("check")
    verify.add_argument("--revision")
    audit = commands.add_parser("audit")
    release = commands.add_parser("release")
    release.add_argument("--repo", required=True)
    release.add_argument("--tag")
    for mode in (create, verify, audit):
        mode.add_argument("artifacts", type=Path, nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "release":
            return report_advisories(release_reports(args.repo, args.tag))
        if args.command in ("check", "audit"):
            reports = check(args.artifacts, getattr(args, "revision", None))
            print(f"Verified {len(reports)} inventories against the local artifacts.")
            return report_advisories(reports) if args.command == "audit" else 0
        components = []
        for value in args.component:
            kind, named = value.split(":", 1)
            name, version = named.rsplit("=", 1)
            components.append(component(kind, name, version))
        if args.flatpak:
            components.extend(flatpak_components(args.flatpak))
        if path := args.gtk_inventory or args.qt_inventory or args.linux_inventory or args.wheel_inventory:
            components.extend(windows_components(path, gtk=args.gtk_inventory is not None))
        if not components:
            raise ValueError("pass a platform inventory or at least one installed component")
        supplied_platform = "windows"
        if args.flatpak:
            supplied_platform = "flatpak"
        elif args.linux_inventory:
            supplied_platform = "linux"
        elif args.wheel_inventory:
            supplied_platform = "wheel"
        for artifact in args.artifacts:
            platform = artifact_platform(artifact.name)
            if platform != supplied_platform:
                raise ValueError(f"{artifact.name}: artifact does not match the inventory platform")
            report = make_report(artifact, args.revision, components, platform)
            path = artifact.with_name(artifact.name + SUFFIX)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(report, stream, indent=2)
                stream.write("\n")
            print(path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as error:
        print(f"Inventory check incomplete: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
