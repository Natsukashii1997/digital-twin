"""Python 版本提示（M5 #5）：合并前验证认 3.9——Hermes 定时任务、CI、名册里的测试命令用的都是它。

别的版本照样能跑、不判失败（有的模块要求 3.9 和 3.14 都过，见 README），但在输出最后提醒一句，
免得把「本机 3.14 全过」当成验证过。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import atexit
import sys
import unittest

VERIFIED_ON = (3, 9)


def notice(version):
    """不是 3.9 时的提示语；是 3.9 时为空。"""
    if tuple(version[:2]) == VERIFIED_ON:
        return ""
    return (f"提示：这次用的是 Python {version[0]}.{version[1]}，不算合并前验证。"
            "合并前验证用系统自带的 3.9：/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests")


if notice(sys.version_info):
    atexit.register(lambda: print("\n" + notice(sys.version_info), file=sys.stderr))


class PythonVersionNoticeTest(unittest.TestCase):
    def test_no_notice_on_39(self):
        self.assertEqual(notice((3, 9, 6)), "")

    def test_other_versions_are_told_which_command_counts(self):
        for version in ((3, 14, 5), (3, 10, 0), (3, 8, 18)):
            said = notice(version)
            self.assertIn(f"Python {version[0]}.{version[1]}", said)
            self.assertIn("/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests", said)


if __name__ == "__main__":
    unittest.main()
