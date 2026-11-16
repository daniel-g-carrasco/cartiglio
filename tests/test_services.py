"""Cartiglio's side of the shared modules: the names app/services.py gives
them, and what they must agree with (the installer's script, the release
workflow, the folders of before)."""

import os
import unittest
from pathlib import Path

import _setup

import cartiglio
from cartiglio import i18n
from cartiglio.app import services, updates


class Names(unittest.TestCase):
    def test_the_project(self):
        project = services.project()
        self.assertEqual((project.repo, project.slug, project.version, project.public_key),
                         (cartiglio.GITHUB_REPO, "cartiglio", cartiglio.__version__, cartiglio.PUBLIC_KEY))
        # the requests say who asks, as before: cartiglio/<version>
        self.assertEqual(project.exe_name, "cartiglio")

    def test_the_installer_looked_for_is_the_one_made(self):
        iss = (_setup.ROOT / "installer" / "cartiglio.iss").read_text(encoding="utf-8")
        self.assertIn("OutputBaseFilename=cartiglio-setup-v{#AppVersion}-windows-x64\n", iss.replace("\r\n", "\n"))
        project = services.project()
        self.assertEqual(project.installer_name("1.3.0"), "cartiglio-setup-v1.3.0-windows-x64.exe")

        def asset(name):
            url = project.downloads + "v1.3.0/" + name
            return updates.Asset(name, url, 9000, "ab", url + ".minisig")

        release = updates.Release("1.3.0", project.releases_url, "", [
            asset("cartiglio-v1.3.0-windows-x64-portable.zip"), asset("cartiglio-setup-v1.3.0-windows-x64.exe")])
        self.assertEqual(updates.installer_of(project, release).name, "cartiglio-setup-v1.3.0-windows-x64.exe")

    def test_setup_opens_the_app_again(self):
        """/RELAUNCH=1 is what the installer's script reads to start the app
        at the end of an update made from it."""
        iss = (_setup.ROOT / "installer" / "cartiglio.iss").read_text(encoding="utf-8")
        self.assertIn("{param:RELAUNCH|0}", iss)
        command = services.installer_command(Path("x.exe"))
        self.assertEqual(command[:4], ["x.exe", "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        self.assertEqual(command[-1], "/RELAUNCH=1")

    def test_the_workflow_checks_the_key_the_code_trusts(self):
        workflow = (_setup.ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn(f"PUBLIC_KEY: {cartiglio.PUBLIC_KEY}\n", workflow.replace("\r\n", "\n"))


class Folders(unittest.TestCase):
    def test_windows_folders_are_the_ones_of_before(self):
        """On Windows the state (errors.log) goes under %LOCALAPPDATA%\\cartiglio,
        where settings.ini has always been."""
        if os.name != "nt":
            self.skipTest("Windows' folders")
        env = {"APPDATA": r"C:\U\Roaming", "LOCALAPPDATA": r"C:\U\Local"}
        paths = services.app_paths(env)
        self.assertEqual(paths.state, Path(r"C:\U\Local\cartiglio\state"))

    def test_linux_folders(self):
        if os.name == "nt":
            self.skipTest("Linux's folders")
        paths = services.app_paths({"XDG_STATE_HOME": "/home/u/.local/state", "HOME": "/home/u"})
        self.assertEqual(paths.state, Path("/home/u/.local/state/cartiglio"))


class Language(unittest.TestCase):
    def setUp(self):
        before = os.environ.get("LANGUAGE")
        self.addCleanup(lambda: os.environ.__setitem__("LANGUAGE", before) if before is not None
                        else os.environ.pop("LANGUAGE", None))
        self.addCleanup(i18n.use, "en")

    def test_english_chosen(self):
        self.assertEqual(services.setup_language("en"), "en")
        self.assertEqual(os.environ["LANGUAGE"], "en")      # GTK's own strings follow
        self.assertEqual(i18n._("Queued"), "Queued")

    def test_italian_chosen(self):
        if not (_setup.ROOT / "locale" / "it" / "LC_MESSAGES" / "cartiglio.mo").is_file():
            self.skipTest("the catalogue is compiled by tools/compile_po.py")
        self.assertEqual(services.setup_language("it"), "it")
        self.assertEqual(os.environ["LANGUAGE"], "it")
        self.assertEqual(i18n._("Queued"), "In coda")

    def test_auto_is_english_or_italian(self):
        self.assertIn(services.setup_language("auto"), services.LANGUAGES)


if __name__ == "__main__":
    unittest.main()
