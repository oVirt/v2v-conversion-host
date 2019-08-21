#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages

meta = {}
exec(open('meta.py').read(), meta)

setup(
    name=meta.get('NAME'),
    version=meta.get('VERSION'),
    description=meta.get('DESCRIPTION'),
    long_description=''.join(open('docs/Virt-v2v-wrapper.md').readlines()),
    keywords=meta.get('KEYWORDS'),
    author=meta.get('AUTHOR'),
    author_email=meta.get('EMAIL'),
    license=meta.get('LICENSE'),
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'virt-v2v-wrapper = wrapper.virt_v2v_wrapper:main'
        ]
    },
    install_requires=[
        'pycurl',
        'six'
    ],
    extras_require={
        'ovirt': 'ovirt-engine-sdk-python',
    },
    # tests_require=['tox'],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Information Technology',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 3',
    ]
)
