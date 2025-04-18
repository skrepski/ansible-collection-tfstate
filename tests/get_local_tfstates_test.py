# -*- coding: utf-8 -*-

from pathlib import Path
from shutil import copyfile
from string import ascii_lowercase
from random import choices
import unittest

from plugins.inventory.tfstate_inventory import get_local_tfstates


class TestGetLocalTFStates(unittest.TestCase):
    base_path = Path('./tests/files').absolute()
    source_fp = Path(base_path, '_file_storage_sample.json')
    test_subdirs_count: int = 5
    test_files_in_subdir_count: int = 3
    test_files_prefix: str = 'domain_'
    test_files_postfix: str = '.tfstates'
    search_pattern: str = f"**/{test_files_prefix}*{test_files_postfix}"

    test_subdirs: list[Path] = []
    test_files: list[Path] = []

    @classmethod
    def setUpClass(cls):
        ''' Create test files'''
        for i in range(cls.test_subdirs_count):
            test_subdir_name = ''.join(choices(ascii_lowercase, k=5))
            subdir = Path(cls.base_path, test_subdir_name).absolute()
            subdir.mkdir(exist_ok=True)
            cls.test_subdirs.append(subdir)

            for i in range(cls.test_files_in_subdir_count):
                test_file_name: str = cls.test_files_prefix + \
                    ''.join(choices(ascii_lowercase, k=10)) + \
                    cls.test_files_postfix
                file = Path(subdir, test_file_name).absolute()
                copyfile(cls.source_fp, file)
                cls.test_files.append(file)

    @classmethod
    def tearDownClass(cls):
        for file in cls.test_files:
            file.unlink()
        for subdir in cls.test_subdirs:
            subdir.rmdir()

    def test_get_local_tfstates(self):
        tfstates = get_local_tfstates(self.base_path, self.search_pattern)
        self.assertIsInstance(tfstates, list)
        self.assertEqual(
            len(tfstates),
            self.test_subdirs_count * self.test_files_in_subdir_count
        )
        for tfstate in tfstates:
            self.assertIsInstance(tfstate, dict)


if __name__ == '__main__':
    unittest.main()
