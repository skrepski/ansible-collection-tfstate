# -*- coding: utf-8 -*-

from ansible.errors import AnsibleParserError
from re import Pattern
import unittest

from plugins.inventory.tfstate_inventory import sanitize_group_name, \
    get_host_group_name, glob_to_regex


class TestSanitazeGroupName(unittest.TestCase):
    pass_cases: dict = {
        'test_group_name_01': [
            'test group name 01',
            'test.group.name.01',
            'test+group+name+01',
            'test-group-name-01'
        ]
    }

    def test_sanitaze_group_name(self):
        for sanitized, cases in self.pass_cases.items():
            for case in cases:
                self.assertEqual(sanitize_group_name(case), sanitized)


class TestGetHostGroupName(unittest.TestCase):
    pass_cases: dict = {
        'test_host': [
            'test-host-01',
            'test.host-02',
            'test-host-03'
        ]
    }
    postfixes: set = (None, '', '_group')

    def test_get_host_group_name(self):
        for postfix in self.postfixes:
            for sanitized, cases in self.pass_cases.items():
                group_name = sanitized + (postfix if postfix else '')
                for case in cases:
                    self.assertEqual(get_host_group_name(case, postfix),
                                     group_name)


class TestGlobToRegex(unittest.TestCase):
    error_cases: set = ('/some/path/prod**/*',
                        'some***path',
                        '/some/path*/**.tfstate')
    pass_cases: dict = {
        'production/**/*.tfstate': [
            'production/dir/file.tfstate',
            'production/dir/subdir/file.tfstate'
        ],
        'production/*dir/*.tfstate': [
            'production/dir/file.tfstate',
            'production/subbdir/file.tfstate'
        ],
        'production/*/*file.tfstate': [
            'production/dir/file.tfstate',
            'production/subdir/file.tfstate',
            'production/dir/somefile.tfstate',
            'production/subdir/somefile.tfstate',
        ]
    }

    def test_glob_exceptions(self):
        for case in self.error_cases:
            self.assertRaises(AnsibleParserError,
                              lambda: glob_to_regex(case))

    def test_glob_to_regex(self):
        for glob, cases in self.pass_cases.items():
            pattern = glob_to_regex(glob)
            self.assertIsInstance(pattern, Pattern)
            for case in cases:
                self.assertIsNotNone(pattern.match(case))


if __name__ == 'main':
    unittest.main()
