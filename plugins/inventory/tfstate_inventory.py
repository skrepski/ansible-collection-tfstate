# -*- coding: utf-8 -*-

# Copyright: Contributors to the Ansible project
# GNU General Public License v3.0+
# (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type


DOCUMENTATION = r'''
---
name: tfstate_inventory
short_description: Terraform states inventory source
description:
  - Works with an existing Terraform state files stored in host or S3 bucket.
  - Supports providers dmacvicar/libvirt, yandex-cloud/yandex.
  - Allows to generate group for each host.
  - Allows to set Terraform state outputs as host/group variables.
  - Does not support caching, constructable.
  - Uses a YAML configuration file that ends with C(tfstate.{yml|yaml}).
author:
  - Sergei Krepski (@skrepski)
options:
  plugin:
    description: The name of tfstate inventory plugin.
    required: True
    choices:
      - skrepski.tfstate.tfstate_inventory
  source_type:
    description: The type of Terraform state files source.
    required: True
    choices:
      - local
      - s3
  local_path:
    description: |
      Absolute path to directory contains Terraform state files.
      Required when I(source_type=local).
    type: path
  s3_config:
    description: |
      A group of key-values used to connect to S3 bucket.
      Required when I(source_type=s3).
    type: dict
    suboptions:
      endpoint:
        description: |
          S3 endpoint with port (if required).
          Required when I(source_type=s3).
        required: True
        type: str
        env:
          - name: TFSTATE_S3_ENDPOINT
      region:
        description: S3 region name.
        default: None
        type: str
        env:
          - name: TFSTATE_S3_REGION
      bucket:
        description: |
          S3 bucket name.
          Required when I(source_type=s3).
        required: True
        type: str
        env:
          - name: TFSTATE_S3_BUCKET
      access_key:
        description: |
          S3 access key id.
          Required when I(source_type=s3).
        required: True
        type: str
        env:
          - name: TFSTATE_S3_ACCESS_KEY
          - name: AWS_ACCESS_KEY_ID
      secret_key:
        description: |
          S3 secret access key.
          Required when I(source_type=s3).
        required: True
        type: str
        env:
          - name: TFSTATE_S3_SECRET_KEY
          - name: AWS_SECRET_ACCESS_KEY
  search_pattern:
    description: |
      Glob like search pattern to lookup Terraform state files.
      Examples: **/*.tfstate, production/**/kafka*.tfstate.
    required: True
    type: str
  collect_public_ips:
    description: |
      Collect only hosts with public (NAT/Bridge) IP addresses if True.
      In this case inventory C(ansible_host) set to public IP address.
      Else C(ansible_host) set to local IP address.
    type: bool
    default: False
  create_hosts_groups:
    description: |
      Create group named like host except ends digits.
      Adds C(hosts_groups_postfix) at group name end.
      All dashes (-) in group name changes to underscore (_).
    type: bool
    default: True
  hosts_groups_postfix:
    description: |
      Postfix for hosts groups.
      Ignores by C(create_hosts_groups) is False.
      All dashes (-) in group name changes to underscore (_).
    type: str
    default: _group
  group_variables_from_output:
    description:
      - Adds groups variables from Terraform state output values.
      - >-
        Set group name as C(group_variables_from_output) key,
        list of Terrafrom states output variables as value.
    type: dict
    default: {}
  host_variables_from_output:
    description:
      - Adds hosts variables from Terraform state output values.
      - >
          Set host name as C(group_variables_from_output) key,
          list of Terrafrom states output variables as value.
    type: dict
    default: {}
'''

EXAMPLES = r'''
# Simple Inventory plugin example with local Terraform states
plugin: skrepski.tfstate.tfstate_inventory
source_type: local
local_path: /path/to/terraform/files
search_pattern: "**/*.tfstate"

# Simple Inventory plugin example with S3 backend
plugin: skrepski.tfstate.tfstate_inventory
source_type: s3
s3_config:
    endpoint: https://storage.yandexcloud.net
    region: ru-central1
    bucket: terraform
    access_key: xxxxxxxxxxxxxx
    secret_key: xxxxxxxxxxxxxx
search_pattern: "production/**/*.tfstate"

# Inventory plugin example with hosts variables from outputs
plugin: skrepski.tfstate.tfstate_inventory
source_type: local
local_path: /path/to/terraform/files
search_pattern: "**/*.tfstate"
host_variables_from_output:
    k8-node-01:
        - k8_master_ips
    k8-node-02:
        - k8_master_ips

    # Running command `ansible-inventory -i local.tfstate.yml --graph`
    # would then produce the inventory:
    # @all:
    # |--@ungrouped:
    # |  |--k8-node-01
    # |  |  |--{ansible_host = 192.168.1.201}
    # |  |  |--{k8_master_ips = ['192.168.1.101]}
    # |  |--k8-node-02
    # |  |  |--{ansible_host = 192.168.1.202}
    # |  |  |--{k8_master_ips = ['192.168.1.101]}
'''


from dataclasses import dataclass
from json import loads, JSONDecodeError
from os import getenv
from pathlib import Path
from typing import Generator
from re import sub, compile, Pattern

from ansible.errors import AnsibleParserError, AnsibleError
from ansible.parsing.yaml.objects import AnsibleUnicode
from ansible.plugins.inventory import BaseInventoryPlugin


@dataclass
class TFStateHost:
    name: str
    ip_address: str = None
    nat_ip_address: str = None


def unicode_to_str(data):
    ''' Convert object values from AnsibleUnicode to str'''
    if isinstance(data, list):
        for i in range(len(data)):
            data[i] = unicode_to_str(data[i])
    elif isinstance(data, dict):
        for key, value in data.items():
            data[key] = unicode_to_str(value)
    else:
        if isinstance(data, AnsibleUnicode):
            data = str(data)
    return data


def sanitize_group_name(group_name: str) -> str:
    ''' Replaces invalid characters in group name'''
    return sub(r'[^\w]', '_', group_name)


def get_host_group_name(hostname: str, postfix: str = None) -> str:
    ''' Returns host sanitized group name'''
    group_name = sub(r'(?:\-\d*)?$', '', hostname)
    if postfix:
        group_name += postfix
    return sanitize_group_name(group_name)


def glob_to_regex(glob: str) -> Pattern:
    ''' Return re Pattern by glob like string'''
    escape_symbols: set = ('\\', '.', ',', '^', '$', '+', '?', '{', '}')
    separator: str = '/'

    glob_parts: list[str] = glob.split(separator)
    re_parts: list[str] = []

    for part in glob_parts:
        # Check ** part
        if '**' in part and part != '**':
            raise AnsibleParserError((
                'Invalid search_pattern error: '
                '\'**\' can only be an entire path component'
            ))
        # Escape symbols
        for symbol in escape_symbols:
            if symbol in part:
                part = part.replace(symbol, f"\\{symbol}")
        # Replace ** and *
        if '**' in part:
            part = part.replace('**', r'[\w\s\-\.\/]*')
        elif '*' in part:
            part = part.replace('*', r'[\w\s\-\.]*')
        re_parts.append(part)
    pattern = '^' + f"\\{separator}".join(re_parts) + '$'
    return compile(pattern)


def get_local_tfstates(base_path: Path, search_pattern: str) -> list[dict]:
    ''' Returns local Terraform state files content.
        :arg base_path: base path to search files
        :arg search_pattern: glob like search pattern
    '''
    tfstates: list[dict] = []
    for fp in base_path.glob(search_pattern):
        if not fp.is_file():
            continue
        with open(fp, 'r') as f:
            try:
                tfstates.append(loads(f.read()))
            except JSONDecodeError:
                continue
    return tfstates


def get_s3_tfstates(s3_config: dict, search_pattern: str) -> list[dict]:
    ''' Returns S3 bucket Terraform state files content.
        :arg s3_config: dict with connection data. Required keys are:
            endpoint, region, bucket, access_key, secret_key
        :arg search_pattern: glob like search pattern
    '''
    try:
        from boto3 import client
    except ImportError:
        raise AnsibleError('Packages boto3 and botocore are required.')

    s3_client = client(service_name='s3', endpoint_url=s3_config['endpoint'],
                       region_name=s3_config['region'],
                       aws_access_key_id=s3_config['access_key'],
                       aws_secret_access_key=s3_config['secret_key'],
                       verify=True,
                       use_ssl=s3_config['endpoint'].startswith('https://'))

    # Get bucket objects keys
    pattern = glob_to_regex(search_pattern)
    content: list = s3_client.list_objects(
        Bucket=s3_config['bucket']
    )['Contents']

    object_keys: list[str] = list(
        [x['Key'] for x in content if pattern.match(x['Key'])]
    )

    # Get objects content
    tfstates: list[dict] = []
    for key in object_keys:
        response = s3_client.get_object(Bucket=s3_config['bucket'], Key=key)
        try:
            tfstates.append(loads(response['Body'].read()))
        except JSONDecodeError:
            continue
    return tfstates


class TFStateInventory:
    ''' Class to collect inventory from Terrfaform state files'''
    # Used to filter resources
    RESOURCE_MODES: set = ('managed')
    RESOURCE_TYPES: set = ('libvirt_domain', 'yandex_compute_instance')
    RESOURCE_PROVIDERS: set = (
        'provider["registry.terraform.io/dmacvicar/libvirt"]',
        'provider["registry.terraform.io/yandex-cloud/yandex"]'
    )

    tfstates: list[dict] = []
    hosts: list[TFStateHost] = []
    outputs: dict = {}

    def __init__(self, tfstates: list[dict]):
        ''' Initiate TFStateInventory instance by tfstates data'''
        self.tfstates = tfstates
        self._collect_outputs()
        self._collect_hosts()

    def list_hosts(self) -> Generator[TFStateHost, None, None]:
        ''' Return Generator with hosts'''
        for host in self.hosts:
            yield host

    def _collect_outputs(self):
        ''' Collect outputs from tfstates'''
        for tfstate in self.tfstates:
            outputs = tfstate.get('outputs', {})
            for key, value_dict in outputs.items():
                try:
                    self.outputs |= {key: value_dict['value']}
                except KeyError:
                    continue
        return

    def _collect_hosts(self):
        ''' Collect hosts from tfstates'''
        for tfstate in self.tfstates:
            for resource in tfstate['resources']:
                try:
                    provider = resource['provider']
                    # Filter resources
                    if resource['mode'] not in self.RESOURCE_MODES \
                        or resource['type'] not in self.RESOURCE_TYPES \
                            or provider not in self.RESOURCE_PROVIDERS:
                        continue
                except KeyError:
                    continue
                for instance_data in resource['instances']:
                    host = self._parse_host(instance_data, provider)
                    if host:
                        self.hosts.append(host)
        return

    def _parse_host(self, instance_data: dict, provider: str) -> TFStateHost:
        ''' Parse instance data by provider'''
        if provider == 'provider["registry.terraform.io/dmacvicar/libvirt"]':
            return self._parse_lv_host(instance_data)
        elif provider == \
                'provider["registry.terraform.io/yandex-cloud/yandex"]':
            return self._parse_yc_host(instance_data)
        else:
            return None

    def _parse_lv_host(self, instance_data: dict) -> TFStateHost:
        ''' Parse instance data for dmacvicar/libvirt provider'''
        IP_ADDRESS_KEYS: set = ('network_id')
        NAT_IP_ADDRESS_KEYS: set = ('macvtap', 'bridge', 'vepa', 'passthrough')
        try:
            attributes = instance_data['attributes']
            network_interfaces = attributes['network_interface']
            name = network_interfaces[0]['hostname']
            ip_address: str = None
            nat_ip_address: str = None
            for interface in network_interfaces:
                # get ip_address
                for key in IP_ADDRESS_KEYS:
                    value = interface.get(key, '')
                    if value != '' and ip_address is None:
                        ip_address = interface['addresses'][0]
                # get nat_ip_address
                for key in NAT_IP_ADDRESS_KEYS:
                    value = interface.get(key, '')
                    if value != '' and nat_ip_address is None:
                        nat_ip_address = interface['addresses'][0]
                if ip_address and nat_ip_address:
                    break
            return TFStateHost(name, ip_address, nat_ip_address)
        except (KeyError, IndexError):
            return None

    def _parse_yc_host(self, instance_data: dict) -> TFStateHost:
        ''' Parse instance data for yandex-cloud/yandex provider'''
        try:
            attributes = instance_data['attributes']
            network_interfaces = attributes['network_interface']
            name = attributes['hostname']
            ip_address: str = None
            nat_ip_address: str = None
            for interface in network_interfaces:
                # get ip_address
                if interface['ip_address'] and ip_address is None:
                    ip_address = interface['ip_address']
                # get nat_ip_address
                if interface['nat_ip_address'] and nat_ip_address is None:
                    nat_ip_address = interface['nat_ip_address']
                if ip_address and nat_ip_address:
                    break
            return TFStateHost(name, ip_address, nat_ip_address)
        except KeyError:
            return None


class InventoryModule(BaseInventoryPlugin):

    NAME = 'skrepski.tfstate.tfstate_inventory'

    def verify_file(self, path):
        valid = False
        if super(InventoryModule, self).verify_file(path):
            if path.endswith(('tfstate.yml', 'tfstate.yaml')):
                valid = True
        return valid

    def parse(self, inventory, loader, path, cache=True):
        super(InventoryModule, self).parse(inventory, loader, path, cache)
        config = unicode_to_str(self.read_config(loader, path))
        self.config = config
        source_path = config['source_type']
        search_pattern = config['search_pattern']
        if source_path == 'local':
            local_path = config['local_path']
            tfstates = get_local_tfstates(Path(local_path), search_pattern)
        elif source_path == 's3':
            s3_config = config['s3_config']
            tfstates = get_s3_tfstates(s3_config, search_pattern)
        tfstate_inventory = TFStateInventory(tfstates)

        # Add hosts
        collect_public_ips = config['collect_public_ips']
        create_hosts_groups = config['create_hosts_groups']
        hosts_groups_postfix = config['hosts_groups_postfix']
        for host in tfstate_inventory.list_hosts():
            if self._add_host(host, collect_public_ips) and \
                    create_hosts_groups:
                group_name = get_host_group_name(host.name,
                                                 hosts_groups_postfix)
                self.inventory.add_group(group_name)
                self.inventory.add_child(group_name, host.name)
        # Add variables
        self._add_groups_vars(tfstate_inventory.outputs)
        self._add_hosts_vars(tfstate_inventory.outputs)

    def read_config(self, loader, path) -> dict:
        ''' Return config variables.
        Raise on config errors.
        '''
        S3_ENV_MAP: dict = {
            'endpoint': ('TFSTATE_S3_ENDPOINT',),
            'region': ('TFSTATE_S3_REGION',),
            'bucket': ('TFSTATE_S3_BUCKET',),
            'access_key': ('TFSTATE_S3_ACCESS_KEY', 'AWS_ACCESS_KEY_ID'),
            'secret_key': ('TFSTATE_S3_SECRET_KEY', 'AWS_SECRET_ACCESS_KEY')
        }

        self._read_config_data(path)
        config_data = self.get_options()
        s3_config: dict = {}
        for key, envs in S3_ENV_MAP.items():
            for env in envs:
                value = getenv(env)
                if value:
                    s3_config[key] = value
                    break
            else:
                s3_config[key] = None
        if 's3_config' in config_data.keys():
            try:
                config_data['s3_config'] |= s3_config
            except TypeError:
                config_data['s3_config'] = s3_config
        else:
            config_data['s3_config'] = s3_config
        return config_data

    def _add_host(self, host: TFStateHost, collect_public_ips: bool) -> bool:
        ''' Add host to inventory. Return True if host is added.'''
        ip_address: str = host.ip_address
        if collect_public_ips:
            ip_address = host.nat_ip_address
        if not ip_address:
            self.display.vvvvv(f"Skip host by no IP address: {host}")
            return False
        self.inventory.add_host(host.name)
        self.display.vvvvv(f"Add host {host}")
        self.inventory.set_variable(host.name, 'ansible_host', ip_address)
        return True

    def _add_groups_vars(self, outputs: dict) -> None:
        ''' Add groups variables by group_variables_from_output'''
        data = self.config['group_variables_from_output']
        for name, keys in data.items():
            for key in keys:
                try:
                    value = outputs[key]
                except KeyError:
                    self.display.warning(
                        f"Vairaible {key} for group {name} "
                        'not found in outputs.'
                    )
                    continue
                try:
                    self.inventory.set_variable(name, key, value)
                except AnsibleError as e:
                    self.display.warning(
                        f"{e.message} to set group variable from output"
                    )
                    continue

    def _add_hosts_vars(self, outputs: dict) -> None:
        ''' Add hosts variables by host_variables_from_output'''
        data = self.config['host_variables_from_output']
        for name, keys in data.items():
            for key in keys:
                try:
                    value = outputs[key]
                except KeyError:
                    self.display.warning(
                        f"Vairaible {key} for host {name} "
                        'not found in outputs.'
                    )
                    continue
                try:
                    self.inventory.set_variable(name, key, value)
                except AnsibleError as e:
                    self.display.warning(
                        f"{e.message} to set host variable from output"
                    )
                    continue
