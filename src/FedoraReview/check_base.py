#-*- coding: utf-8 -*-

#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License along
#    with this program; if not, write to the Free Software Foundation, Inc.,
#    51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# (C) 2011 - Tim Lauridsen <timlau@fedoraproject.org>

'''
This module contains automatic test for Fedora Packaging guidelines
'''

import re
import StringIO

from abc import ABCMeta, abstractmethod
from textwrap import TextWrapper

from helpers_mixin import HelpersMixin


class _Attachment(object):
    """ Text written after the test lines. """

    def __init__(self, header, text, order_hint=10):
        """
        Setup an attachment. Args:
         -  header: short header, < 40 char.
         -  text: printed as-is.
         -  order_hint: Sorting hint, lower hint goes first.
                0 <= order_hint <= 10
        """

        self.header = header
        self.text = text
        self.order_hint = order_hint

    def __str__(self):
        s = self.header + '\n'
        s += '-' * len(self.header) + '\n'
        s += self.text
        return s

    def __cmp__(self, other):
        if not hasattr(other, 'order_hint'):
            return NotImplemented
        if self.order_hint < other.order_hint:
            return -1
        if self.order_hint > other.order_hint:
            return 1
        return 0


class AbstractCheck(object):
    """
    The basic interface for a test (a. k a. check) as seen from
    the outside.

    Class attributes:
      - log: logger
      - version version of api, defaults to 0.1
      - group: 'Generic', 'C/C++', 'PHP': binds the test to a
                Registry.
      - implementation: 'json'|'python'|'shell', defaults to
        'python'.

    Properties:
      - name: Unique string.
      - defined_in: Filename (complete path).
      - deprecates: List of  tests replaced (should not run) by this
        test  if the test is applicable.
      - needs: List of tests which should run before this test.
      - is_run: reflects if test has run.
      - is_na, is_failed, is_passed, is_pending: outcome of test.
      - result: Undefined until is_run, None if is_na, else
        TestResult.

    Methods:
      - run(): Run the test, sets result

    Equality:
      - Tests are considered equal if they have the same name.
    """
    # pylint: disable=R0201

    __metaclass__ = ABCMeta

    PASS    = 'pass'
    FAIL    = 'fail'
    NA      = 'na'
    PENDING = 'pending'

    version        = '0.1'
    implementation = 'python'

    def __init__(self, defined_in):
        self.defined_in = defined_in
        self.deprecates = []
        self.needs = []
        try:
            self.name = 'Undefined'
        except AttributeError:
            pass

    def __eq__(self, other):
        return self.name.__eq__(other)

    def __ne__(self, other):
        return self.name.__ne__(other)

    def __hash__(self):
        return self.name.__hash__()

    def __str__(self):
        return self.name

    @abstractmethod
    def run(self):
        ''' Perform the check, update result. '''
        pass

    @property
    def state(self):
        ''' None for (not is_run or is_na), else result.result '''
        assert self != None
        if hasattr(self, 'result'):
            return self.result.result if self.result else None
        return None

    is_run     = property(lambda self: hasattr(self, 'result'))
    is_failed  = property(lambda self: self.state == self.FAIL)
    is_passed  = property(lambda self: self.state == self.PASS)
    is_pending = property(lambda self: self.state == self.PENDING)
    is_na      = property(
                     lambda self: self.is_run and self.state == None)


class GenericCheck(AbstractCheck):

    """
    Common interface inherited by all Check implementations.

    Properties:
      - text: free format user info on test, one line.
      - description: longer, multiline free format text info.
      - type: 'MUST'|'SHOULD'|'EXTRA'|'UNDEFINED', defaults to
        'MUST'
      - url: Usually guidelines url, possibly None.
      - checks: Checks instance which created this check.
      - registry: Defining Registry, set by Registry.register()
    """
    registry = None

    class Attachment(_Attachment):
        """ Text written after the test lines. """
        pass

    def __init__(self, checks, defined_in):
        AbstractCheck.__init__(self, defined_in)
        self.checks = checks
        self.url = '(this test has no URL)'
        self.text = self.__class__.__name__
        self.description = 'This test has no description'
        self.type = 'MUST'
        self.needs = ['CheckBuildCompleted']

    spec       = property(lambda self: self.checks.spec)
    flags      = property(lambda self: self.checks.flags)
    srpm       = property(lambda self: self.checks.srpm)
    sources    = property(lambda self: self.checks.sources)
    log        = property(lambda self: self.checks.log)
    rpms       = property(lambda self: self.checks.rpms)

    @property
    def name(self):                              # pylint: disable=E0202
        ''' The check's name. '''
        return self.__class__.__name__

    def set_passed(self, result, output_extra=None, attachments=None):
        '''
        Set if the test is passed, failed or N/A
        and set optional extra output to be shown in repost
        '''
        if result in ['not_applicable', self.NA, None]:
            self.result = None
            return
        elif result == True or result == self.PASS:
            state = self.PASS
        elif result == False or result == self.FAIL:
            state = self.FAIL
        elif result == 'inconclusive' or result == self.PENDING:
            state = self.PENDING
        else:
            state = self.FAIL
            self.log.warning('Illegal return code: ' + str(result))
        r = TestResult(self, state, output_extra, attachments)
        self.result = r                          # pylint: disable=W0201


class CheckBase(GenericCheck, HelpersMixin):
    """ Base class for "regular" python checks. """
    # pylint: disable=R0201

    def __init__(self, checks, defined_in):
        HelpersMixin.__init__(self)
        GenericCheck.__init__(self, checks, defined_in)

    def is_applicable(self):
        """
        By default, language specific checks uses the registry's
        is_applicable()
        """
        return self.registry.is_applicable()

    def run_on_applicable(self):
        ''' Called by run() if is_applicable is true(). '''
        self.set_passed(self.PENDING)

    def run(self):
        ''' By default, a manual test returning 'inconclusive'.'''
        if self.is_applicable():
            self.run_on_applicable()
        else:
            self.set_passed('not_applicable')

    group = property(lambda self: self.registry.group)


class TestResult(object):
    ''' The outcome of a test, stored in check.result. '''

    TEST_STATES = {
         'pending': '[ ]', 'pass': '[x]', 'fail': '[!]', 'na': '[-]'}

    def __init__(self, check, result, output_extra, attachments=None):
        self.check = check
        self.text = re.sub("\s+", " ", check.text) if check.text else ''
        self.result = result
        self.output_extra = output_extra
        self.attachments = attachments if attachments else []
        if self.output_extra:
            self.output_extra = re.sub("\s+", " ", self.output_extra)
        self.wrapper = TextWrapper(width=78, subsequent_indent=" " * 5,
                                   break_long_words=False, )

    url = property(lambda self: self.check.url)
    name = property(lambda self: self.check.name)
    type = property(lambda self: self.check.type)
    group = property(lambda self: self.check.group)
    deprecates = property(lambda self: self.check.deprecates)

    state = property(lambda self: self.result)

    def get_text(self):
        ''' Return printable representation of test. '''
        strbuf = StringIO.StringIO()
        main_lines = self.wrapper.wrap(
            "%s: %s" % (self.TEST_STATES[self.result], self.text))
        strbuf.write("%s" % '\n'.join(main_lines))
        if self.output_extra and self.output_extra != "":
            strbuf.write("\n")
            extra_lines = self.wrapper.wrap("     Note: %s" %
                                            self.output_extra)
            strbuf.write('\n'.join(extra_lines))

        return strbuf.getvalue()

    def __str__(self):
        self.get_text()



# vim: set expandtab: ts=4:sw=4:
