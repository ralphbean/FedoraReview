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
Tools for helping Fedora package reviewers
'''

import re
import rpm

from subprocess import Popen, PIPE, CalledProcessError
try:
    from subprocess import check_output
except ImportError:
    from FedoraReview.el_compat import check_output


from review_error import ReviewError

from settings import Settings

SECTIONS = ['build', 'changelog', 'check', 'clean', 'description', 'files',
               'install', 'package', 'prep', 'pre', 'post', 'preun', 'postun',
               'trigger', 'triggerin', 'triggerun', 'triggerprein',
               'triggerpostun', 'pretrans', 'posttrans']
SPEC_SECTIONS = re.compile(r"^(\%(" + "|".join(SECTIONS) + "))\s*")
MACROS = re.compile(r"^%(define|global)\s+(\w*)\s+(.*)")


class SpecFile(object):
    '''
    Wrapper classes for getting information from a .spec file
    '''
    # pylint: disable=W0212

    def __init__(self, filename):
        self.log = Settings.get_logger()
        self._sections = {}
        self._section_list = []
        self.filename = filename
        with open(filename, "r") as f:
            self.lines = f.readlines()
        ts = rpm.TransactionSet()
        self.spec_obj = ts.parseSpec(self.filename)

        self._name_vers_rel = [self.get_from_spec('name'),
                               self.get_from_spec('version'),
                               self.get_from_spec('release')]
        self.process_sections()

    name = property(lambda self: self._name_vers_rel[0])
    version = property(lambda self: self._name_vers_rel[1])
    release = property(lambda self: self._name_vers_rel[2])

    def get_sources(self, _type='Source'):
        ''' Get SourceX/PatchX lines with macros resolved '''
        result = {}
        for (url, num, flags) in self.spec_obj.sources:
            # rpmspec.h, rpm.org ticket #123
            srctype = "Source" if flags & 1 else "Patch"
            if _type != srctype:
                continue
            tag = srctype + str(num)
            result[tag] = self.spec_obj.sourceHeader.format(url)
        return result

    def has_patches(self):
        '''Returns true if source rpm contains patch files'''
        return len(self.get_sources('Patch')) > 0

    def process_sections(self):
        ''' Scan lines and build self._sections, self.section_list. '''
        section_lines = []
        cur_sec = 'main'
        for l in self.lines:
            # check for release
            line = l[:-1]
            res = SPEC_SECTIONS.search(line)
            if res:
                this_sec = line
                # This is a new section, store lines in old one
                if cur_sec != this_sec:
                    self._section_list.append(cur_sec)
                    self._sections[cur_sec] = section_lines
                    section_lines = []
                    cur_sec = this_sec
            else:
                if line:
                    section_lines.append(line.strip())
        self._section_list.append(cur_sec)
        self._sections[cur_sec] = section_lines
        cur_sec = this_sec

    def get_from_spec(self, macro):
        ''' Use rpm for a value for a given tag (macro is resolved)'''
        qf = '%{' + macro.upper() + "}\n"  # The RPM tag to search for
        # get the name
        cmd = ['rpm', '-q', '--qf', qf, '--specfile', self.filename]
                # Run the command
        try:
            proc = \
                Popen(cmd, stdout=PIPE, stderr=PIPE, env={'LC_ALL': 'C'})
            output, error = proc.communicate()
            #print "output : [%s], error : [%s]" % (output, error)
        except OSError, e:
            self.log.error("OSError : %s" % str(e))
            self.log.debug("OSError : %s" % str(e), exc_info=True)
            return False
        if output:
            rc = output.split("\n")[0]
            #print "RC: ", rc
            if rc == '(none)':
                rc = None
            return rc
        else:
            # rpm dont know the tag, so it is not found
            if 'unknown tag' in error:
                return None
            value = self.find_tag(macro)
            if len(value) > 0:
                return value
            else:
                self.log.warning("Error : [%s]" % (error))
                return False

    def get_expanded(self):
        ''' Return expanded spec, as provided by rpmspec -P. '''
        # this is not really working now, but only json is using it...phasing
        # out
        return self.lines

    def find_tag(self, tag, section = None):
        '''
        Find a given tag in the spec file. Parameters:
          - tag: tag to look for. E. g., 'Name'
            Matches tag: (E. g. Name:) in beginning of line.
          - section. A section corresponds to a subpackage e. g.,
            'devel'

        Returns array of strings, each string corresponds to a
        definition line in the spec file.

        '''
        key = re.compile(r"^%s\d*\s*:\s*(.*)" % tag, re.I)
        values = []
        lines = self.lines
        if section:
            lines_by_section = self.get_section(section)
            if lines_by_section:
                lines = lines_by_section[section]
        for line in lines:
            match = key.search(line)
            if match:
                values.append(match.group(1).strip())
        return values

    @staticmethod
    def rpm_eval(expression):
        ''' Evaluate expression using rpm --eval. '''
        try:
            reply = check_output(['rpm', '--eval', expression])
        except CalledProcessError as err:
            raise ReviewError(str(err))
        return  reply.strip()

    def get_build_requires(self):
        ''' Return the list of build requirements. '''
        return self.spec_obj.sourceHeader[rpm.RPMTAG_REQUIRES]

    def get_requires(self, pkg_name=None):
        ''' Return list of requirements i. e., Requires: '''
        package = self._get_pkg_by_name(pkg_name)
        return package.header[rpm.RPMTAG_REQUIRES]

    def _get_pkg_by_name(self, pkg_name):
        '''
        Return package with given name. pgk_name == None
        -> base package, not existing name -> KeyError
        '''
        if not pkg_name:
            return self.spec_obj.packages[0]
        for p in self.spec_obj.packages:
            if p.header[rpm.RPMTAG_NAME] == pkg_name:
                return p
        raise KeyError(pkg_name + ': no such package')

    def get_section(self, section):
        '''
        get the lines in a section in the spec file
        ex. %install, %clean etc
        '''
        results = {}
        for sec in self._section_list:
            if sec.startswith(section):
                results[sec.strip()] = self._sections[sec]
        return results

    def find(self, regex):
        ''' Return first line matching regex or None. '''
        for line in self.lines:
            res = regex.search(line)
            if res:
                return res
        return None

    def find_all(self, regex, skip_changelog=False):
        ''' Find all non-changelog lines matching regex. '''
        result = []
        for line in self.lines:
            if skip_changelog:
                if line.lower().strip().startswith('%changelog'):
                    break
            res = regex.search(line)
            if res:
                result.append(res)
        return result

# vim: set expandtab: ts=4:sw=4:
