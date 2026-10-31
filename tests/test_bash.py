"""Tests for bash tool — cd stripping, readonly detection."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.tools.bash import _strip_cd_prefix, _is_readonly, _validate_workdir_command


class TestStripCdPrefix(unittest.TestCase):

    WD = "/home/user/projects/shop"

    def test_strip_cd_and(self):
        self.assertEqual(_strip_cd_prefix(f"cd {self.WD} && git log", self.WD), "git log")

    def test_strip_cd_quoted(self):
        self.assertEqual(_strip_cd_prefix(f'cd "{self.WD}" && git show HEAD', self.WD), "git show HEAD")

    def test_strip_cd_semicolon(self):
        self.assertEqual(_strip_cd_prefix(f"cd {self.WD} ; ls -la", self.WD), "ls -la")

    def test_no_cd(self):
        self.assertEqual(_strip_cd_prefix("git log --oneline", self.WD), "git log --oneline")

    def test_different_path_not_stripped(self):
        cmd = "cd /other/path && ls"
        self.assertEqual(_strip_cd_prefix(cmd, self.WD), cmd)

    def test_strip_no_space_after_and(self):
        self.assertEqual(_strip_cd_prefix(f"cd {self.WD}&&git log", self.WD), "git log")


class TestWorkdirValidation(unittest.TestCase):

    WD = "/home/user/project"

    def test_rejects_chained_cd_path_probing(self):
        command = "cd /one 2>/dev/null; cd /two 2>/dev/null; git status"
        result = _validate_workdir_command(command, self.WD)
        self.assertIn("chained cd", result)
        self.assertIn(self.WD, result)

    def test_rejects_cd_outside_workdir(self):
        self.assertIn("outside", _validate_workdir_command("cd .. && ls", self.WD))

    def test_allows_cd_inside_workdir(self):
        self.assertEqual(_validate_workdir_command("cd src && ls", self.WD), "")

    def test_rejects_git_c_outside_workdir(self):
        result = _validate_workdir_command("git -C /tmp status", self.WD)
        self.assertIn("outside", result)

    def test_allows_git_c_inside_workdir(self):
        self.assertEqual(_validate_workdir_command("git -C src status", self.WD), "")


class TestIsReadonly(unittest.TestCase):

    # --- Filesystem ---

    def test_ls(self):
        self.assertTrue(_is_readonly("ls -la"))

    def test_cat(self):
        self.assertTrue(_is_readonly("cat file.txt"))

    def test_find(self):
        self.assertTrue(_is_readonly('find . -name "*.py"'))

    def test_grep(self):
        self.assertTrue(_is_readonly("grep -r pattern ."))

    def test_head_tail(self):
        self.assertTrue(_is_readonly("head -20 file.txt"))
        self.assertTrue(_is_readonly("tail -f log.txt"))

    # --- Git read-only ---

    def test_git_log(self):
        self.assertTrue(_is_readonly("git log --oneline -20"))

    def test_git_show(self):
        self.assertTrue(_is_readonly("git show HEAD --stat"))

    def test_git_diff(self):
        self.assertTrue(_is_readonly("git diff HEAD~3"))

    def test_git_status(self):
        self.assertTrue(_is_readonly("git status"))

    def test_git_blame(self):
        self.assertTrue(_is_readonly("git blame src/main.py"))

    def test_git_config_read(self):
        self.assertTrue(_is_readonly("git config --get user.name"))
        self.assertTrue(_is_readonly("git config --list"))

    # --- Mercurial ---

    def test_hg_log(self):
        self.assertTrue(_is_readonly("hg log -l 10"))

    def test_hg_diff(self):
        self.assertTrue(_is_readonly("hg diff -r 123"))

    def test_hg_status(self):
        self.assertTrue(_is_readonly("hg status"))

    def test_hg_blame(self):
        self.assertTrue(_is_readonly("hg blame file.py"))

    # --- SVN ---

    def test_svn_log(self):
        self.assertTrue(_is_readonly("svn log -l 5"))

    def test_svn_diff(self):
        self.assertTrue(_is_readonly("svn diff -r 100:101"))

    # --- Hex/encoding ---

    def test_xxd(self):
        self.assertTrue(_is_readonly("xxd file.bin"))

    def test_hexdump(self):
        self.assertTrue(_is_readonly("hexdump -C file"))

    def test_base64(self):
        self.assertTrue(_is_readonly("base64 file.txt"))

    # --- Help ---

    def test_man(self):
        self.assertTrue(_is_readonly("man git"))

    # --- Linters ---

    def test_php_lint(self):
        self.assertTrue(_is_readonly("php -l file.php"))

    def test_flake8(self):
        self.assertTrue(_is_readonly("flake8 src/"))

    def test_eslint(self):
        self.assertTrue(_is_readonly("eslint app.js"))

    def test_shellcheck(self):
        self.assertTrue(_is_readonly("shellcheck script.sh"))

    def test_gcc_syntax(self):
        self.assertTrue(_is_readonly("gcc -fsyntax-only main.c"))

    def test_cppcheck(self):
        self.assertTrue(_is_readonly("cppcheck src/"))

    def test_gnat_check(self):
        self.assertTrue(_is_readonly("gnat check pkg.adb"))

    # --- Version checks ---

    def test_python_version(self):
        self.assertTrue(_is_readonly("python3 --version"))

    def test_go_version(self):
        self.assertTrue(_is_readonly("go version"))

    # --- Pipes ---

    def test_pipe_readonly(self):
        self.assertTrue(_is_readonly("git log | head -20"))
        self.assertTrue(_is_readonly("ls -la | grep py"))
        self.assertTrue(_is_readonly("cat README.md | grep install | head"))

    def test_pipe_with_writing_stage(self):
        self.assertFalse(_is_readonly("cat file | tee copy"))
        self.assertFalse(_is_readonly("ls | xargs rm"))

    def test_readonly_command_sequence(self):
        command = 'git status --short; echo "---"; git log --oneline -10'
        self.assertTrue(_is_readonly(command))
        self.assertTrue(_is_readonly("test -f README.md && grep title README.md"))

    def test_sequence_with_writing_command(self):
        self.assertFalse(_is_readonly("git status; rm generated.txt"))
        self.assertFalse(_is_readonly("grep value file || echo value > file"))

    def test_shell_control_structures_stay_guarded(self):
        self.assertFalse(_is_readonly("for f in *.txt; do cat $f; done"))
        self.assertFalse(_is_readonly("cat file &"))
        self.assertFalse(_is_readonly("cat file\necho done"))

    # --- Dangerous commands ---

    def test_git_push(self):
        self.assertFalse(_is_readonly("git push"))

    def test_git_commit(self):
        self.assertFalse(_is_readonly("git commit -m x"))

    def test_hg_commit(self):
        self.assertFalse(_is_readonly("hg commit -m x"))

    def test_svn_commit(self):
        self.assertFalse(_is_readonly("svn commit"))

    def test_rm(self):
        self.assertFalse(_is_readonly("rm -rf /"))

    def test_redirect(self):
        self.assertFalse(_is_readonly("echo x > file"))
        self.assertFalse(_is_readonly("cat file > other"))
        self.assertFalse(_is_readonly("git log > /tmp/x"))

    def test_command_substitution(self):
        self.assertFalse(_is_readonly("echo $(cat secret)"))

    def test_sed_modify(self):
        self.assertFalse(_is_readonly("sed -i s/old/new/ file"))

    def test_git_config_write(self):
        self.assertFalse(_is_readonly("git config user.name foo"))

    def test_unknown_command(self):
        self.assertFalse(_is_readonly("some_random_cmd"))


if __name__ == "__main__":
    unittest.main()
