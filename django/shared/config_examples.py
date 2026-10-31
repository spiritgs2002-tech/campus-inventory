"""Verify the checked-in database host example using bytes, not rendered output."""

import ast
from pathlib import Path


HOST_KEY = bytes([68, 66, 95, 72, 79, 83, 84])
LEGACY_KEY = bytes([68, 66, 95, 68, 78, 75, 79])


def verify_example_files(root: Path):
    example = (root / ".env.example").read_bytes()
    readme = (root / "README.md").read_bytes()
    entries = {}
    for line in example.splitlines():
        line = line.strip()
        if not line or line.startswith(b"#"):
            continue
        if b"=" not in line:
            raise ValueError("Invalid example assignment")
        key, value = line.split(b"=", 1)
        if key in entries:
            raise ValueError("Duplicate example key: " + key.hex())
        entries[key] = value
    if HOST_KEY not in entries or entries[HOST_KEY] != b"localhost":
        raise ValueError(
            "Database host example missing or incorrect; expected key hex="
            + HOST_KEY.hex()
        )
    if LEGACY_KEY in example or LEGACY_KEY in readme:
        raise ValueError(
            "Legacy misspelled key remains; forbidden key hex=" + LEGACY_KEY.hex()
        )
    if HOST_KEY + b"=localhost" not in readme or HOST_KEY + b"=/tmp" not in readme:
        raise ValueError("README must document TCP and Unix socket examples")
    database_name = entries.get(b"DB_NAME", b"")
    if not database_name or b"DB_NAME=" + database_name not in readme:
        raise ValueError("Application database example differs from README")

    # Check the actual DATABASES.default.HOST expression, rather than any
    # incidental occurrence of the correct spelling elsewhere in settings.py.
    module = ast.parse((root / "campus_inventory/settings.py").read_text())
    assignments = [
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "DATABASES"
            for target in node.targets
        )
    ]

    def dict_value(node, key):
        if not isinstance(node, ast.Dict):
            raise ValueError("Unsupported DATABASES structure")
        for name, value in zip(node.keys, node.values):
            if isinstance(name, ast.Constant) and name.value == key:
                return value
        raise ValueError("Missing database setting: " + key)

    if len(assignments) != 1:
        raise ValueError("DATABASES assignment missing or ambiguous")
    host = dict_value(dict_value(assignments[0].value, "default"), "HOST")
    if not (
        isinstance(host, ast.Call)
        and isinstance(host.func, ast.Attribute)
        and isinstance(host.func.value, ast.Attribute)
        and isinstance(host.func.value.value, ast.Name)
        and host.func.value.value.id == "os"
        and host.func.value.attr == "environ"
        and host.func.attr == "get"
        and host.args
        and isinstance(host.args[0], ast.Constant)
        and host.args[0].value == HOST_KEY.decode("ascii")
    ):
        raise ValueError(
            "DATABASES.default.HOST does not read the expected environment key"
        )
    return {
        "host_key_hex": HOST_KEY.hex(),
        "host_key_codepoints": list(HOST_KEY),
        "legacy_key_present": False,
        "database_name": database_name.decode("ascii"),
    }
