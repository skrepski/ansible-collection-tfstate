# -*- coding: utf-8 -*-

from os import getenv
import unittest

from plugins.inventory.tfstate_inventory import get_s3_tfstates


class TestGetS3TFStates(unittest.TestCase):
    s3_config: dict = {}

    @classmethod
    def setUpClass(cls):
        env_prefix: str = 'TFSTATE_S3_'
        env_variables: set = ('endpoint', 'region', 'bucket',
                              'access_key', 'secret_key')
        env_s3_config: dict = {}
        for key in env_variables:
            value = getenv((env_prefix + key).upper())
            if value is None:
                return
            env_s3_config[key] = value
        cls.s3_config = env_s3_config

    def test_get_s3_tfstates(self):
        if len(self.s3_config) == 0:
            self.skipTest('No S3 config')
        pattern: str = '**/*.tfstate'
        tfstates = get_s3_tfstates(self.s3_config, pattern)
        self.assertIsInstance(tfstates, list)
        self.assertGreater(len(tfstates), 0)
        for tfstate in tfstates:
            self.assertIsInstance(tfstate, dict)


if __name__ == '__main__':
    unittest.main()
