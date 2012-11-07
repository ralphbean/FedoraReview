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

'''
Plugin module acting as an interface between, simple, shell-based plugins
and the regular python plugins.
'''

import os
import os.path
import re
import shutil

from glob import glob
from subprocess import Popen, PIPE

from FedoraReview import AbstractRegistry, GenericCheck
from FedoraReview import ReviewDirs, Settings, XdgDirs

ENVIRON_TEMPLATE = """
unset $(env | sed 's/=.*//')
PATH=/bin:/usr/bin:/sbin/:/usr/sbin

FR_FLAGS_generator
FR_SETTINGS_generator
export FR_REVIEWDIR='@review_dir@'
export HOME=$FR_REVIEWDIR
cd $HOME

export FR_NAME='@name@'
export FR_VERSION='@version@'
export FR_RELEASE='@release@'
export FR_GROUP='@group@'
export FR_LICENSE='@license@'
export FR_URL='@url@'

FR_SOURCE_generator
FR_PATCH_generator

export FR_PREP=@prep@
export FR_BUILD=@build@
export FR_INSTALL=@install@

FR_FILES_generator
FR_DESCRIPTION_generator

export FR_FILES FR_DESCRIPTION

export FR_PASS=80
export FR_FAIL=81
export FR_PENDING=82
export FR_NOT_APPLICABLE=83


function get_used_rpms()
# returns (stdout) list of used rpms if found, else returns 1
{
    cd $FR_REVIEWDIR
    if test  "${FR_SETTINGS[prebuilt]}" = True
    then
        files=( $(ls ../*.rpm 2>/dev/null | grep -v .src.rpm) ) \
               || files=( '@@' )
    else
        files=( $(ls results/*.rpm 2>/dev/null | grep -v .src.rpm) ) \
               || files=( '@@' )
    fi
    test -e ${files[0]} || return 1
    echo "${files[@]}"
    cd $OLDPWD
}

function unpack_rpms()
# Unpack all non-src rpms in results into rpms-unpacked, one dir per rpm.
{
    [ -d rpms-unpacked ] && return 0
    rpms=( $( get_used_rpms ) ) || return 1
    mkdir rpms-unpacked
    cd rpms-unpacked
    retval=0
    for rpm_path in ${rpms[@]};  do
        rpm=$( basename $rpm_path)
        mkdir $rpm
        cd $rpm
        rpm2cpio ../../$rpm_path | cpio -id &>/dev/null
        cd ..
    done
    cd ..
}

function unpack_sources()
# Unpack sources in upstream into upstream-unpacked
# Ignores (reuses) already unpacked items.
{
    sources=( $(cd upstream; ls) ) || sources=(  )
    if [[ ${#sources[@]} -eq 0 || ! -e "upstream/${sources[0]}" ]]; then
       return $FR_NOT_APPLICABLE
    fi
    for source in "${sources[@]}"; do
        mkdir upstream-unpacked/$source 2>/dev/null || continue
        rpmdev-extract -qfC  upstream-unpacked/$source upstream/$source || \
           cp upstream/$source upstream-unpacked/$source
    done
}

function attach()
# Usage: attach <sorting hint> <header>
# Reads attachment from stdin
{
    startdir=$(pwd)
    cd $FR_REVIEWDIR
    for (( i = 0; i < 10; i++ )); do
        test -e $FR_REVIEWDIR/.attachments/*$i || break
    done
    if [ $i -eq 10 ]; then
        echo "More than 10 attachments! Giving up" >&2
        exit 1
    fi
    sort_hint=$1
    shift
    title=${*//\/ }
    file="$sort_hint;${title/;/:};$i"
    cat > .attachments/"$file"
    cd $startdir
}

"""

ENV_PATH = 'review-env.sh'

_TAGS = ['name', 'version', 'release', 'group', 'license', 'url']
_SECTIONS = ['prep', 'build', 'install', 'check']

_PASS = 80
_FAIL = 81
_PENDING = 82
_NOT_APPLICABLE = 83


def _find_value(line, key):
    ''' Locate tag like @tag:, return value or None. '''
    key = '@' + key + ':'
    if key in line:
        return re.sub('.*' + key, '', line).strip()
    return None


def _quote(s):
    ''' Fix string to be included within '' '''
    return s.replace("'", "'\\''")


def _settings_generator():
    ''' Bash code defining FR_SETTINGS, reflecting Settings. '''
    body = 'declare -A FR_SETTINGS \n'
    for key in Settings.__dict__.iterkeys():
        if key.startswith('_'):
            continue
        value = Settings.__dict__[key]
        if not value:
            value = ''
        body += 'FR_SETTINGS[%s]="%s"\n' % (key, value)
    return body


def _source_generator(spec):
    ''' Bash code defining the %sourceX items. '''
    body = ''
    for tag, path in spec.sources_by_tag.iteritems():
        body += 'export ' + tag + '="' + path + '"\n'
    return body


def _patch_generator(spec):
    ''' Bash code defining the %patchX items. '''
    body = ''
    for tag, path in spec.patches_by_tag.iteritems():
        body += 'export ' + tag + '="' + path + '"\n'
    return body


def _files_generator(spec):
    ''' Bash code defining FR_FILES,reflecting %files. '''
    body = 'declare -A FR_FILES\n'
    for pkg in spec.packages:
        item = _quote('\n'.join(spec.get_files(pkg)))
        body += """FR_FILES[%s]='%s'\n""" % (pkg, item)
    return body


def _description_generator(spec):
    '''
    Bash code defining FR_DESCRIPTION,reflecting %description.
    '''
    body = 'declare -A FR_DESCRIPTION\n'
    for pkg in spec.packages:
        section = spec.get_section('%description', pkg)
        if section:
            item = _quote('\n'.join(section))
            body += """FR_DESCRIPTION[%s]='%s'\n""" % (pkg, item)
    return body


def _flags_generator(flags):
    ''' Bash code defining FR_FLAGS,reflecting checks.flags. '''
    body = 'declare -A FR_FLAGS\n'
    for flag in flags.itervalues():
        body += """FR_FLAGS[%s]='%s'\n""" % (flag.name, str(flag))
    return body


def _write_section(spec, env, s):
    ''' Substitute a spec section into env.'''
    body = ''
    section = '%' + s.strip()
    lines = spec.get_section(section)
    if lines:
        body += _quote('\n'.join(lines))
    body = "'" + body + "'"
    if len(body) < 5:
        body = ''
    env = env.replace('@' + s + '@', body)
    return env


def _write_tag(spec, env, tag):
    ''' Substitute a spec tag into env. '''
    value = spec.expand_tag(tag.upper())
    if value:
        env = env.replace('@' + tag + '@', value)
    else:
        env = env.replace('@' + tag + '@', '""')
    return env


def _create_env(checks):
    ''' Create the review-env.sh file. '''

    env = ENVIRON_TEMPLATE
    env = env.replace('FR_SETTINGS_generator', _settings_generator())
    env = env.replace('@review_dir@', ReviewDirs.root)
    for tag in _TAGS:
        env = _write_tag(checks.spec, env, tag)
    env = env.replace('FR_SOURCE_generator',
                       _source_generator(checks.spec))
    env = env.replace('FR_PATCH_generator',
                       _patch_generator(checks.spec))
    for s in _SECTIONS:
        env = _write_section(checks.spec, env, s)
    env = env.replace('FR_FILES_generator',
                      _files_generator(checks.spec))
    env = env.replace('FR_FLAGS_generator',
                      _flags_generator(checks.flags))
    env = env.replace('FR_DESCRIPTION_generator',
                      _description_generator(checks.spec))
    with open(ENV_PATH, 'w') as f:
        f.write(env)
    attach_path = os.path.join(ReviewDirs.root, '.attachments')
    if os.path.exists(attach_path):
        shutil.rmtree(attach_path)
    os.makedirs(attach_path)


class Registry(AbstractRegistry):
    ''' Registers all script plugins. '''

    group = 'Shell-api'

    class CreateEnvCheck(GenericCheck):
        ''' Create the review-env.sh file in review dir. No output. '''

        group = 'Generic'

        def __init__(self, checks, registry):
            GenericCheck.__init__(self, checks, __file__)
            self.text = 'Create review-env.sh'
            self.automatic = True
            self.type = 'EXTRA'
            self.registry = registry

        def run(self):
            _create_env(self.checks)
            self.set_passed(self.NA)

    def __init__(self, checks):
        AbstractRegistry.__init__(self, checks)
        self.groups = checks.groups
        self.checks = checks
        self.log = Settings.get_logger()

    def register(self, plugin):
        ''' Return all available scripts as ShellCheck instances. '''

        def _get_plugin_dirs():
            ''' Return list of dirs to scan for scripts. '''
            dir_list = []
            plugindir = os.path.dirname(__file__)
            plugindir = os.path.join(plugindir, '../scripts')
            plugindir = os.path.normpath(plugindir)
            dir_list.append(plugindir)
            dir_list.append(os.path.join(XdgDirs.app_datadir,
                                                  'scripts'))
            return dir_list

        dirs = _get_plugin_dirs()
        my_checks = [self.CreateEnvCheck(self.checks, self)]
        for d in dirs:
            for f in glob(os.path.join(d, '*.sh')):
                my_checks.append(ShellCheck(self, f))
        return my_checks


class ShellCheck(GenericCheck):
    """ A single test  defined by a shell plugin. """
    DEFAULT_GROUP  = 'Generic'
    DEFAULT_TYPE   = 'MUST'
    implementation = 'script'

    def __init__(self, registry, path):
        GenericCheck.__init__(self, registry.checks, path)
        for tag in _TAGS:
            try:
                setattr(self, tag, tag + ': undefined')
            except AttributeError:
                pass
        self.type = self.DEFAULT_TYPE
        self.group = self.DEFAULT_GROUP
        self.needs = ['CreateEnvCheck']
        self.deprecates = []
        self.text = ''
        self.registry = registry
        self._name = None
        self._parse(path)

    groups = property(lambda self: self.registry.checks.groups)

    def _parse_attributes(self, lines):
        ''' Parse all tags and populate attributes. '''

        for line in lines:
            for attr in ['group', 'url', 'type']:
                value = _find_value(line, attr)
                if value:
                    setattr(self, attr, value)
            text = _find_value(line, 'register-flag')
            if text:
                (name, doc) = text.split(None, 1)
                self.checks.flags.add(
                    self.registry.Flag(name, doc, self.defined_in))
            text = _find_value(line, 'set-flag')
            if text:
                (flag, value) = text.split(None, 2)
                self.checks.flags[flag].value = value
            text = _find_value(line, 'text')
            if text:
                if self.text:
                    self.text += ' '
                self.text += text
            victims = _find_value(line, 'deprecates')
            if victims:
                self.deprecates = victims.replace(',', ' ').split()
            needed = _find_value(line, 'needs')
            if needed:
                self.needs.extend(needed.replace(',', ' ').split())

    def _parse(self, path):
        """
        Parse registration info for plugin with just one test, like:
        @name: test-name
        @group: java # generic, C/C++ etc
        @type: MUST|SHOULD|EXTRA
        @text :"User info, visible in listings"
        @text: "More text, appended to previous, w blank separator"
        @url: Guidelines URL, optional
        @deprecates: test1, test2, ...
        @needs: test4, test5, ...
        """

        with open(path) as f:
            lines = f.readlines()
        for line in lines:
            name = _find_value(line, 'name')
            if name:
                break
        if not name:
            name = os.path.splitext(os.path.basename(path))[0]
        self._name = name
        self._parse_attributes(lines)

    def _do_run(self, cmd):
        '''
        Actually invoke the external script, returning
        (retcode, stdout, stderr)
        '''
        p = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
        try:
            stdout, stderr = p.communicate()
        except OSError:
            self.log.warning("Cannot execute " + cmd)
            self.log.debug("Cannot execute " + cmd, exc_info=True)
            return -1, None, None
        stdout = None if stdout == '' else stdout
        stderr = None if stderr == '' else stderr
        return p.returncode, stdout, stderr

    @property
    def name(self):                             # pylint: disable=E0202
        ''' Check's name. '''
        return self._name

    def _get_attachments(self):
        ''' Pick up shell-script attachments from .attachments. '''
        attachments = []
        for path in glob(os.path.join(ReviewDirs.root,
                                      '.attachments', '*;*;*')):
            with open(path) as f:
                body = f.read(8192)
            sort_hint, header = os.path.basename(path).split(';')[:2]
            try:
                sort_hint = int(sort_hint)
            except ValueError:
                self.log.warning('Cannot decode attachment sorting hint: '
                                  + sort_hint + ', defaulting to 7')
                sort_hint = 7
            a = self.Attachment(header, body, sort_hint)
            attachments.append(a)
            os.unlink(path)
        return attachments

    def run(self):
        ''' Run the check. '''
        if self.is_run:
            return
        if not self.group in self.groups:
            self.set_passed(self.PENDING,
                           "Test run failed: illegal group")
            self.log.warning('Illegal group %s in %s' %
                                   (self.group, self.defined_in))
            return
        if not self.groups[self.group].is_applicable():
            self.set_passed(self.NA)
            return
        cmd = 'source ./review-env.sh; source ' + self.defined_in
        retval, stdout, stderr = self._do_run(cmd)
        attachments = self._get_attachments()
        if retval == -1:
            self.set_passed(self.PENDING,
                            "Cannot execute shell command" + cmd)
        elif retval == _PASS and not stderr:
            self.set_passed(self.PASS, stdout, attachments)
        elif retval == _FAIL and not stderr:
            self.set_passed(self.FAIL, stdout, attachments)
        elif retval == _PENDING and not stderr:
            self.set_passed(self.PENDING, stdout, attachments)
        elif retval == _NOT_APPLICABLE and not stderr:
            self.set_passed(self.NA, stdout)
        else:
            self.log.warning(
                'Illegal return from %s, code %d, output: %s' %
                     (self.defined_in, retval,
                     'stdout:' + str(stdout) + ' stderr:' + str(stderr)))
            self.set_passed(self.PENDING, 'Test run failed')

# vim: set expandtab: ts=4:sw=4:
