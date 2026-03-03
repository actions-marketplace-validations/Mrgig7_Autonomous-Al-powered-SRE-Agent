from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from sre_agent.schemas.fix_plan import FixOperation, FixPlan


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


@dataclass(frozen=True)
class PatchStats:
    files_changed: list[str]
    total_files: int
    total_lines_added: int
    total_lines_removed: int
    diff_bytes: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PatchOutput:
    diff_text: str
    stats: PatchStats


def _normalize_whitespace(content: str) -> str:
    lines = [line.rstrip() for line in content.splitlines()]
    return "\n".join(lines) + "\n"


def _unified_diff(file_path: str, before: str, after: str) -> str:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    )


def _count_diff_changes(diff_text: str) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _toml_section_bounds(lines: list[str], section_header: str) -> tuple[int, int] | None:
    header = f"[{section_header}]"
    start: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("[") and lines[j].strip().endswith("]"):
            end = j
            break
    return start, end


def _toml_upsert_dependency(content: str, dep_name: str, dep_spec: str) -> str:
    lines = content.splitlines()
    bounds = _toml_section_bounds(lines, "tool.poetry.dependencies")
    if bounds is None:
        raise ValueError("pyproject.toml missing [tool.poetry.dependencies]")
    start, end = bounds

    key_pattern = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=\s*(.+)$")
    existing: list[tuple[str, int]] = []
    for idx in range(start + 1, end):
        m = key_pattern.match(lines[idx])
        if m:
            existing.append((m.group(1), idx))

    for key, idx in existing:
        if key.lower() == dep_name.lower():
            lines[idx] = f'{dep_name} = "{dep_spec}"'
            return "\n".join(lines) + "\n"

    insertion_idx = end
    for key, idx in existing:
        if key.lower() != "python" and dep_name.lower() < key.lower():
            insertion_idx = idx
            break

    lines.insert(insertion_idx, f'{dep_name} = "{dep_spec}"')
    return "\n".join(lines) + "\n"


def _requirements_upsert_dependency(content: str, dep_name: str, dep_spec: str) -> str:
    lines = content.splitlines()
    normalized_name = dep_name.lower()
    updated = False

    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue

        lowered = stripped.lower()
        if (
            lowered == normalized_name
            or lowered.startswith(normalized_name + "==")
            or lowered.startswith(normalized_name + ">=")
        ):
            out.append(f"{dep_name}{dep_spec}")
            updated = True
        else:
            out.append(line)

    if not updated:
        out.append(f"{dep_name}{dep_spec}")

    return "\n".join(out) + "\n"


def _package_json_upsert_dependency(content: str, dep_name: str, dep_spec: str) -> str:
    data = json.loads(content or "{}")
    if not isinstance(data, dict):
        raise ValueError("package.json must contain a JSON object")
    deps = data.get("dependencies")
    if not isinstance(deps, dict):
        deps = {}
        data["dependencies"] = deps
    deps[dep_name] = dep_spec
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _package_lock_update(content: str, details: dict) -> str:
    data = json.loads(content or "{}")
    if not isinstance(data, dict):
        raise ValueError("package-lock.json must contain a JSON object")

    lockfile_version = details.get("lockfile_version")
    if isinstance(lockfile_version, int):
        data["lockfileVersion"] = lockfile_version

    ensure_deps = details.get("ensure_root_dependencies")
    if isinstance(ensure_deps, dict):
        packages = data.get("packages")
        if not isinstance(packages, dict):
            packages = {}
            data["packages"] = packages
        root = packages.get("")
        if not isinstance(root, dict):
            root = {}
            packages[""] = root
        root_deps = root.get("dependencies")
        if not isinstance(root_deps, dict):
            root_deps = {}
            root["dependencies"] = root_deps
        for k, v in ensure_deps.items():
            if isinstance(k, str) and isinstance(v, str):
                root_deps[k] = v

        deps = data.get("dependencies")
        if not isinstance(deps, dict):
            deps = {}
            data["dependencies"] = deps
        for k, v in ensure_deps.items():
            if isinstance(k, str) and isinstance(v, str):
                node = deps.get(k)
                if not isinstance(node, dict):
                    node = {}
                    deps[k] = node
                node["version"] = v

    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _go_mod_upsert_require(content: str, module: str, version: str) -> str:
    lines = content.splitlines()
    require_start: int | None = None
    require_end: int | None = None

    for i, line in enumerate(lines):
        if line.strip() == "require (":
            require_start = i
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == ")":
                    require_end = j
                    break
            break

    def _line_matches(line_text: str) -> bool:
        parts = line_text.strip().split()
        return len(parts) >= 2 and parts[0] == module

    if require_start is not None and require_end is not None:
        for idx in range(require_start + 1, require_end):
            if _line_matches(lines[idx]):
                lines[idx] = f"\t{module} {version}"
                return "\n".join(lines) + "\n"
        lines.insert(require_end, f"\t{module} {version}")
        return "\n".join(lines) + "\n"

    for idx, line in enumerate(lines):
        if line.startswith("require ") and _line_matches(line[len("require ") :]):
            lines[idx] = f"require {module} {version}"
            return "\n".join(lines) + "\n"

    lines.append(f"require {module} {version}")
    return "\n".join(lines) + "\n"


def _pom_xml_pin_dependency_version(
    content: str, group_id: str, artifact_id: str, version: str
) -> str:
    blocks = re.split(r"(<dependency>\s*[\s\S]*?</dependency>)", content, flags=re.IGNORECASE)
    out: list[str] = []
    updated = False
    for part in blocks:
        if not part.lower().startswith("<dependency>"):
            out.append(part)
            continue
        gid = re.search(r"<groupId>\s*([^<]+)\s*</groupId>", part, flags=re.IGNORECASE)
        aid = re.search(r"<artifactId>\s*([^<]+)\s*</artifactId>", part, flags=re.IGNORECASE)
        if not gid or not aid:
            out.append(part)
            continue
        if gid.group(1).strip() != group_id or aid.group(1).strip() != artifact_id:
            out.append(part)
            continue
        if re.search(r"<version>\s*[^<]+\s*</version>", part, flags=re.IGNORECASE):
            out.append(part)
            updated = True
            continue
        m = re.search(r"</artifactId>", part, flags=re.IGNORECASE)
        if not m:
            out.append(part)
            continue
        insert_at = m.end()
        replacement = part[:insert_at] + f"\n      <version>{version}</version>" + part[insert_at:]
        out.append(replacement)
        updated = True
    if not updated:
        raise ValueError("pom.xml dependency not found or could not be updated")
    return "".join(out)


def _pom_xml_pin_plugin_version(content: str, group_id: str, artifact_id: str, version: str) -> str:
    blocks = re.split(r"(<plugin>\s*[\s\S]*?</plugin>)", content, flags=re.IGNORECASE)
    out: list[str] = []
    updated = False
    for part in blocks:
        if not part.lower().startswith("<plugin>"):
            out.append(part)
            continue
        gid = re.search(r"<groupId>\s*([^<]+)\s*</groupId>", part, flags=re.IGNORECASE)
        aid = re.search(r"<artifactId>\s*([^<]+)\s*</artifactId>", part, flags=re.IGNORECASE)
        if not aid:
            out.append(part)
            continue
        gid_value = gid.group(1).strip() if gid else "org.apache.maven.plugins"
        if gid_value != group_id or aid.group(1).strip() != artifact_id:
            out.append(part)
            continue
        if re.search(r"<version>\s*[^<]+\s*</version>", part, flags=re.IGNORECASE):
            out.append(part)
            updated = True
            continue
        m = re.search(r"</artifactId>", part, flags=re.IGNORECASE)
        if not m:
            out.append(part)
            continue
        insert_at = m.end()
        replacement = part[:insert_at] + f"\n      <version>{version}</version>" + part[insert_at:]
        out.append(replacement)
        updated = True
    if not updated:
        raise ValueError("pom.xml plugin not found or could not be updated")
    return "".join(out)


def _dockerfile_update(content: str, details: dict) -> str:
    lines = content.splitlines()
    out: list[str] = []
    pinned = details.get("pin_base_image")
    pin_done = False
    for line in lines:
        if not pin_done and line.strip().lower().startswith("from ") and isinstance(pinned, dict):
            image = str(pinned.get("image") or "").strip()
            tag = str(pinned.get("tag") or "").strip()
            if image and tag:
                out.append(
                    re.sub(
                        rf"^(FROM\s+){re.escape(image)}(:[^\s]+)?",
                        rf"\1{image}:{tag}",
                        line,
                        flags=re.IGNORECASE,
                    )
                )
                pin_done = True
                continue
        out.append(line)
    content2 = "\n".join(out) + ("\n" if content.endswith("\n") else "")

    if details.get("apt_get_cleanup") is True:
        fixed: list[str] = []
        for line in content2.splitlines():
            if (
                line.strip().startswith("RUN ")
                and "apt-get" in line
                and "rm -rf /var/lib/apt/lists" not in line
            ):
                fixed.append(line.rstrip() + " && rm -rf /var/lib/apt/lists/*")
            else:
                fixed.append(line)
        content2 = "\n".join(fixed) + "\n"

    return content2


def _remove_unused_import(content: str, details: dict) -> str:
    name = str(details.get("name") or details.get("import") or "").strip()
    module = str(details.get("module") or "").strip()
    if not name:
        raise ValueError("remove_unused requires details.name")

    lines = content.splitlines()
    out: list[str] = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("import "):
            leading = line[: line.index("import") + len("import")]
            rest = line[line.index("import") + len("import") :].strip()
            parts = [p.strip() for p in rest.split(",") if p.strip()]
            new_parts: list[str] = []
            removed = False
            for p in parts:
                base = p.split(" as ")[0].strip()
                if base == name:
                    removed = True
                else:
                    new_parts.append(p)
            if removed:
                if not new_parts:
                    continue
                out.append(leading + " " + ", ".join(new_parts))
                continue

        if stripped.startswith("from ") and " import " in stripped:
            from_part, import_part = stripped.split(" import ", 1)
            from_module = from_part.replace("from ", "", 1).strip()
            if module and from_module != module:
                out.append(line)
                continue

            leading = line[: line.index("import") + len("import")]
            rest = line[line.index("import") + len("import") :].strip()
            parts = [p.strip() for p in rest.split(",") if p.strip()]
            new_parts = []
            removed = False
            for p in parts:
                base = p.split(" as ")[0].strip()
                if base == name:
                    removed = True
                else:
                    new_parts.append(p)
            if removed:
                if not new_parts:
                    continue
                out.append(leading + " " + ", ".join(new_parts))
                continue

        out.append(line)

    return "\n".join(out) + "\n"


class PatchGenerator:
    def generate(self, repo_path: Path, plan: FixPlan) -> PatchOutput:
        plan_files = sorted({_normalize_path(p) for p in plan.files if p})
        if not plan_files:
            raise ValueError("FixPlan.files is empty")

        original: dict[str, str] = {}
        updated: dict[str, str] = {}

        for fp in plan_files:
            abs_path = repo_path / fp
            original[fp] = (
                abs_path.read_text(encoding="utf-8", errors="replace") if abs_path.exists() else ""
            )
            updated[fp] = original[fp]

        for op in plan.operations:
            op_file = _normalize_path(op.file)
            if op_file not in original:
                raise ValueError("Operation touches file not included in plan.files")
            updated[op_file] = self._apply_operation(updated[op_file], op)

        diffs: list[str] = []
        files_changed: list[str] = []
        total_added = 0
        total_removed = 0

        for fp in plan_files:
            before = _normalize_whitespace(original[fp])
            after = _normalize_whitespace(updated[fp])
            if before == after:
                continue
            diff_text = _unified_diff(fp, before, after)
            if diff_text.strip():
                diffs.append(diff_text)
                files_changed.append(fp)
                added, removed = _count_diff_changes(diff_text)
                total_added += added
                total_removed += removed

        combined = ("\n".join(diffs).strip() + "\n") if diffs else ""
        stats = PatchStats(
            files_changed=files_changed,
            total_files=len(files_changed),
            total_lines_added=total_added,
            total_lines_removed=total_removed,
            diff_bytes=len(combined.encode("utf-8")),
        )
        return PatchOutput(diff_text=combined, stats=stats)

    def _apply_operation(self, content: str, op: FixOperation) -> str:
        if op.type in {"add_dependency", "pin_dependency"}:
            return self._apply_dependency(content, op)
        if op.type == "update_config":
            return self._apply_update_config(content, op)
        if op.type == "remove_unused":
            return _remove_unused_import(content, op.details)
        raise ValueError(f"Unsupported operation type for deterministic patching: {op.type}")

    def _apply_dependency(self, content: str, op: FixOperation) -> str:
        name = str(op.details.get("name") or op.details.get("package") or "").strip()
        spec = str(op.details.get("spec") or op.details.get("version") or "").strip()
        if not name or not spec:
            raise ValueError("dependency operation requires details.name and details.spec")

        file_path = _normalize_path(op.file)
        if file_path.endswith("pyproject.toml"):
            return _toml_upsert_dependency(content, name, spec)
        if file_path.endswith("requirements.txt"):
            return _requirements_upsert_dependency(content, name, spec)
        if file_path.endswith("package.json"):
            return _package_json_upsert_dependency(content, name, spec)
        if file_path.endswith("go.mod"):
            return _go_mod_upsert_require(content, name, spec)
        if file_path.endswith("pom.xml"):
            group_id = str(op.details.get("group_id") or "").strip()
            artifact_id = str(op.details.get("artifact_id") or "").strip()
            if not group_id or not artifact_id:
                if ":" in name:
                    group_id, artifact_id = name.split(":", 1)
                else:
                    raise ValueError(
                        "pom.xml pin requires details.group_id and details.artifact_id"
                    )
            if bool(op.details.get("plugin")):
                return _pom_xml_pin_plugin_version(content, group_id, artifact_id, spec)
            return _pom_xml_pin_dependency_version(content, group_id, artifact_id, spec)
        raise ValueError(
            "dependency operation supports only pyproject.toml, requirements.txt, package.json, go.mod, pom.xml"
        )

    def _apply_update_config(self, content: str, op: FixOperation) -> str:
        file_path = _normalize_path(op.file)
        if file_path.endswith("package-lock.json"):
            return _package_lock_update(content, op.details)
        if file_path.endswith("Dockerfile"):
            return _dockerfile_update(content, op.details)
        if file_path.endswith("go.sum"):
            return content if content.endswith("\n") else content + "\n"
        raise ValueError("update_config supports only package-lock.json, Dockerfile, go.sum")
