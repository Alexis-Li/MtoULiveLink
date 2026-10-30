# ruff: noqa: E402

import importlib.util
import pathlib
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import _project_tools
import package
import validate_project
import version

CONFORMANCE_GENERATOR = ROOT / "protocol" / "generate_unreal_corpus.py"


def _load_generator(name):
    spec = importlib.util.spec_from_file_location(name, str(CONFORMANCE_GENERATOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectToolTests(unittest.TestCase):
    def test_project_is_valid(self):
        self.assertEqual([], validate_project.validate()["errors"])

    def test_unreal_conformance_data_is_current(self):
        self.assertEqual([], _load_generator("generate_mtou_unreal_corpus").check_generated())

    def test_conformance_validation_errors_are_deterministic(self):
        module = _load_generator("generate_mtou_unreal_corpus_invalid")
        invalid = {
            "schema_version": 1,
            "protocol_version": 4,
            "cases": [
                {
                    "id": "duplicate",
                    "operation": "init",
                    "applies_to": ["unreal"],
                    "payload": {},
                    "expected": {"accepted": True, "close": False},
                },
                {
                    "id": "duplicate",
                    "operation": "unknown",
                    "applies_to": [],
                    "expected": {"accepted": False, "close": True},
                },
            ],
            "limits": {
                "max_message_bytes": 33554432,
                "max_cache_payload_bytes": 1073741824,
                "max_cache_frame_count": 20000,
            },
        }
        self.assertEqual(
            [
                "protocol_version must equal 11",
                "limits.max_linkage_group_bytes must be a positive integer",
                "limits.max_control_queue must be a positive integer",
                "limits.max_reference_bytes must be a positive integer",
                "limits.max_reference_objects must be a positive integer",
                "limits.max_reference_triangles must be a positive integer",
                "limits.max_set_objects must be a positive integer",
                "case 1 id is duplicated: duplicate",
                "case 1 operation is unsupported",
                "case 1 applies_to must contain maya and/or unreal",
                "case 1 must define exactly one input source",
                "case 1 rejected case needs a stable v11 error_code",
            ],
            module.validate_corpus(invalid),
        )

    def test_both_components_share_the_product_version(self):
        self.assertEqual(_project_tools.maya_version(), _project_tools.unreal_version())

    def test_version_update_defaults_to_preview(self):
        current = _project_tools.maya_version()
        result = version.update("9.9.9", apply=False)
        self.assertEqual("dry-run", result["mode"])
        self.assertIn("unreal/MtoULiveLink/MtoULiveLink.uplugin", result["files"])
        self.assertIn("maya/MtoULiveLink/scripts/mtou_livelink/config.py", result["files"])
        self.assertEqual(current, _project_tools.maya_version())
        with self.assertRaises(ValueError):
            version.update("next", apply=False)

    def test_packages_can_be_previewed(self):
        current = _project_tools.maya_version()
        maya_result = package.package_maya(ROOT / "releases")
        unreal_result = package.package_unreal("5.7", ROOT / "releases")
        self.assertEqual("dry-run", maya_result["mode"])
        self.assertEqual("releases/MtoULiveLink-{0}.zip".format(current), maya_result["archive"])
        self.assertEqual(
            "releases/MtoULiveLink-{0}-UE5.7.zip".format(current), unreal_result["archive"]
        )
        self.assertGreater(maya_result["file_count"], 0)
        self.assertGreater(unreal_result["file_count"], 0)

    def test_packages_contain_only_component_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            maya_result = package.package_maya(output, apply=True)
            unreal_result = package.package_unreal("5.7", output, apply=True)

            with zipfile.ZipFile(output / pathlib.Path(maya_result["archive"]).name) as archive:
                names = archive.namelist()
            self.assertIn("scripts/MtoULiveLink.py", names)
            self.assertIn("scripts/mtou_livelink/controller.py", names)
            self.assertIn("scripts/mtou_livelink/ui/style.py", names)
            self.assertIn("install.mel", names)
            self.assertIn("scripts/mtou_livelink/install.py", names)
            self.assertFalse([name for name in names if name.startswith("tests/")])
            self.assertFalse(
                [name for name in names if "__pycache__" in name or name.endswith(".pyc")]
            )

            with zipfile.ZipFile(output / pathlib.Path(unreal_result["archive"]).name) as archive:
                names = archive.namelist()
            self.assertIn("MtoULiveLink/MtoULiveLink.uplugin", names)
            self.assertIn(
                "MtoULiveLink/Source/MtoULiveLink/Private/Tests/MtoULiveLinkTests.cpp", names
            )
            forbidden_names = {
                "README.md", "README_CN.md", "CHANGELOG.md", "LICENSE", "AGENTS.md", ".gitkeep",
            }
            self.assertFalse(
                [name for name in names if pathlib.PurePosixPath(name).name in forbidden_names]
            )
            self.assertFalse(
                [name for name in names
                 if {"Binaries", "Intermediate", "Saved"} & set(pathlib.PurePosixPath(name).parts)]
            )


if __name__ == "__main__":
    unittest.main()
