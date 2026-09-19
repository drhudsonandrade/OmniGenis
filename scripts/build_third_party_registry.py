from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] if Path(__file__).name != "build_registry.py" else Path.cwd()
OUT = ROOT / "config/third_party_software_registry.json"

PERMISSIVE_LICENSE_IDS = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD-4-Clause",
    "CC0-1.0",
    "HPND",
    "ISC",
    "MIT",
    "MIT-CMU",
    "PSF-2.0",
    "Python-2.0",
    "X11",
    "Zlib",
    "public-domain",
}

REVIEW_MARKERS = ("LGPL", "MPL", "EPL", "CC-BY", "LicenseRef-", "Artistic")
BLOCK_MARKERS = ("AGPL", "SSPL", "NON-COMMERCIAL", "NONCOMMERCIAL", "RESEARCH-ONLY", "ACADEMIC-ONLY")


def load(root: Path, path: str) -> dict[str, Any]:
    return json.loads((root / path).read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _license_expression_is_well_formed(tokens: list[str]) -> bool:
    if not tokens:
        return False
    expect_operand = True
    depth = 0
    for token in tokens:
        if token == "(":
            if not expect_operand:
                return False
            depth += 1
        elif token == ")":
            if expect_operand or depth == 0:
                return False
            depth -= 1
            expect_operand = False
        elif token in {"AND", "OR"}:
            if expect_operand:
                return False
            expect_operand = True
        else:
            if not expect_operand:
                return False
            expect_operand = False
    return not expect_operand and depth == 0


def license_policy(value: str) -> str:
    text = (value or "UNKNOWN").strip()
    upper = text.upper()
    if not text or upper in {"UNKNOWN", "NOASSERTION", "NONE"} or text.startswith("sha256:"):
        return "REVIEW_REQUIRED"
    if any(marker in upper for marker in BLOCK_MARKERS):
        return "BLOCKED_BY_DEFAULT"
    if "GPL" in upper:
        if "LGPL" in upper or " WITH " in upper or " OR " in upper:
            return "REVIEW_REQUIRED"
        return "BLOCKED_BY_DEFAULT"
    if any(marker.upper() in upper for marker in REVIEW_MARKERS):
        return "REVIEW_REQUIRED"
    # A permissive result requires the complete expression to contain only
    # recognized permissive identifiers and boolean operators. Unknown/custom
    # terms never inherit permissive status from a substring match.
    if re.search(r"[^A-Za-z0-9.+()\-\s]", text):
        return "REVIEW_REQUIRED"
    tokens = re.findall(r"\(|\)|[A-Za-z0-9.+-]+", text)
    terms = [
        token
        for token in tokens
        if token not in {"AND", "OR", "(", ")"}
    ]
    if (
        terms
        and "WITH" not in tokens
        and _license_expression_is_well_formed(tokens)
        and all(term in PERMISSIVE_LICENSE_IDS for term in terms)
    ):
        return "PERMISSIVE"
    return "REVIEW_REQUIRED"


def component(entry: dict[str, Any]) -> dict[str, Any]:
    licenses = entry.get("licenses") or [entry.get("license") or "UNKNOWN"]
    statuses = {license_policy(x) for x in licenses}
    if "BLOCKED_BY_DEFAULT" in statuses:
        status = "BLOCKED_BY_DEFAULT"
    elif "REVIEW_REQUIRED" in statuses:
        status = "REVIEW_REQUIRED"
    else:
        status = "PERMISSIVE"
    entry["licenses"] = licenses
    entry.pop("license", None)
    entry["policy_status"] = status
    entry["notice_required"] = True
    entry["legal_review_required"] = status != "PERMISSIVE"
    return entry


def parse_python_lock(root: Path) -> dict[str, str]:
    result = {}
    for line in (root / "reporting/requirements.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^ \\]+)", line)
        if m:
            result[m.group(1).lower()] = m.group(2)
    return result


def direct_python(root: Path) -> set[str]:
    names=set()
    for raw in (root/"reporting/requirements.in").read_text(encoding="utf-8").splitlines():
        raw=raw.strip()
        if not raw or raw.startswith("#"):
            continue
        names.add(raw.split("==",1)[0].lower())
    return names


def build_payload(root: Path = ROOT) -> dict[str, Any]:
    records: list[dict[str, Any]] = []

    base = load(root, "locks/base-image-software.json")
    for item in base["components"]:
        records.append(component({
            "id": f"base:{item['purl'] or item['name'] + '@' + item['version']}",
            "ecosystem": "deb",
            "name": item["name"],
            "version": item["version"],
            "scope": "distributed_runtime",
            "relationship": "base_image",
            "source": base["requested_image_reference"],
            "purl": item.get("purl"),
            "licenses": item.get("licenses") or ["UNKNOWN"],
            "evidence": item.get("license_evidence_paths") or [],
        }))

    conda = load(root, "locks/conda-linux-64-resolution.json")
    env_names=set()
    for raw in (root/"environment.yml").read_text(encoding="utf-8").splitlines():
        raw=raw.strip()
        if raw.startswith("- "):
            token=raw[2:].strip()
            if token in {"conda-forge","bioconda","nodefaults"}:
                continue
            env_names.add(re.split(r"[<>= ]",token,1)[0])
    for item in conda["packages"]:
        records.append(component({
            "id": f"conda:{item['name']}@{item['version']}#{item['build']}",
            "ecosystem": "conda",
            "name": item["name"],
            "version": item["version"],
            "scope": "distributed_runtime",
            "relationship": "direct" if item["name"] in env_names else "transitive",
            "source": item["url"],
            "sha256": item["sha256"],
            "licenses": [item.get("license") or "UNKNOWN"],
            "evidence": ["locks/conda-linux-64-resolution.json"],
        }))

    py_meta = load(root, "locks/python-license-metadata.json")
    py_lock = parse_python_lock(root)
    py_direct = direct_python(root)
    metadata_by_name={x["name"].lower():x for x in py_meta["packages"]}
    if set(py_lock) != set(metadata_by_name):
        raise SystemExit("python license metadata coverage mismatch")
    for name,version in sorted(py_lock.items()):
        meta=metadata_by_name[name]
        if meta["version"] != version:
            raise SystemExit(f"python metadata version mismatch: {name}")
        evidence=["locks/python-license-metadata.json","reporting/requirements.txt"]
        if meta.get("license_evidence"):
            evidence.append(meta["license_evidence"])
        record=component({
            "id": f"pypi:{name}@{version}",
            "ecosystem": "pypi",
            "name": name,
            "version": version,
            "scope": "distributed_runtime",
            "relationship": "direct" if name in py_direct else "transitive",
            "source": meta["source"],
            "licenses": [meta["license"]],
            "evidence": evidence,
        })
        if name == "pypdfium2" and version == "5.13.0":
            record["policy_status"]="REVIEWED_ACCEPTED_EXACT_ARTIFACT"
            record["legal_review_required"]=False
        records.append(record)

    package_json = load(root, "mcp/package.json")
    npm_direct=set(package_json.get("dependencies",{})) | set(package_json.get("devDependencies",{}))
    package_lock = load(root, "mcp/package-lock.json")
    for path,meta in sorted(package_lock.get("packages",{}).items()):
        if not path:
            continue
        name=meta.get("name") or path.rsplit("node_modules/",1)[-1]
        version=meta.get("version") or "UNKNOWN"
        relation="direct" if path == f"node_modules/{name}" and name in npm_direct else "transitive"
        scope="build_dependency_pruned_after_build" if meta.get("dev") else "distributed_runtime"
        records.append(component({
            "id": f"npm:{path}@{version}",
            "ecosystem": "npm",
            "name": name,
            "version": version,
            "scope": scope,
            "relationship": relation,
            "source": meta.get("resolved") or "mcp/package-lock.json",
            "integrity": meta.get("integrity"),
            "licenses": [meta.get("license") or "UNKNOWN"],
            "evidence": ["mcp/package-lock.json"],
        }))

    actions = load(root, "locks/action-license-metadata.json")
    for item in actions["actions"]:
        records.append(component({
            "id": f"github-action:{item['name']}@{item['sha']}",
            "ecosystem": "github-action",
            "name": item["name"],
            "version": item["version"],
            "scope": "build_only",
            "relationship": "direct",
            "source": f"https://github.com/{item['name']}/tree/{item['sha']}",
            "sha": item["sha"],
            "licenses": [item["license"]],
            "evidence": [item["license_url"], "locks/actions-lock.json"],
        }))

    records.sort(key=lambda x: x["id"])
    counts=Counter(r["policy_status"] for r in records)
    scopes=Counter(r["scope"] for r in records)
    ecosystems=Counter(r["ecosystem"] for r in records)
    payload: dict[str, Any] = {
        "schema":"omnigenis-third-party-software-registry-v1",
        "generated_date":"2026-09-18",
        "scope_note":"Software/container inventory only. Dataset, score, model and scientific-resource terms are governed by the later scientific-data licensing stage.",
        "policy_source":"docs/compliance/DEPENDENCY_POLICY.md",
        "standards":{
            "cyclonedx_output":"1.7",
            "spdx_output":"2.3",
            "spdx_current_upstream":"3.0",
        },
        "summary":{
            "component_records":len(records),
            "by_ecosystem":dict(sorted(ecosystems.items())),
            "by_scope":dict(sorted(scopes.items())),
            "by_policy_status":dict(sorted(counts.items())),
            "license_clean_claim_allowed":False,
        },
        "components":records,
    }
    return payload


def render_payload(root: Path = ROOT) -> str:
    return json.dumps(build_payload(root), indent=2, sort_keys=True) + "\n"


def main() -> int:
    rendered = render_payload(ROOT)
    payload = json.loads(rendered)
    records = payload["components"]
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != rendered:
            print("FAIL\tthird_party_registry\tdrift")
            return 1
        print(f"PASS\tthird_party_registry\tcomponents={len(records)}")
        return 0
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(rendered,encoding="utf-8")
    print(f"WROTE\t{OUT}\tcomponents={len(records)}")
    return 0


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    args=parser.parse_args()
    raise SystemExit(main())
