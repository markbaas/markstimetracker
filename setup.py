#!/usr/bin/env python3

from distutils.core import setup
import sys

if not sys.version_info[0] >= 3:
    sys.exit("Sorry, only Python 3 is supported")


setup(name='markstimetracker',
      version='1.0',
      description='Mark\'s Time Tracker',
      author='Mark Baas',
      author_email='',
      url='https://github.com/markbaas/markstimetracker',
      packages=['markstimetracker'],
      scripts=['bin/markstimetracker'],
      data_files=[('share/applications', ['data/markstimetracker.desktop']),
                  ('share/icons/hicolor/scalable/apps', ['data/cat.svg'])]
      )
