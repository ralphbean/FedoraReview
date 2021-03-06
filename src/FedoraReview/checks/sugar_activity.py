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
# (C) 2012 - Michael Scherer <misc@fedoraproject.org>


from FedoraReview import LangCheckBase


class SugarActivityCheckBase(LangCheckBase):
    header = 'SugarActivity'

    def is_applicable(self):
        return self.has_files_re('^/usr/(share|lib|lib64)/sugar/activities/')


class SugarActivityCheckNaming(SugarActivityCheckBase):
    ''' All activities MUST be named sugar-<activity name>  '''
    def __init__(self, base):
        SugarActivityCheckBase.__init__(self, base)
        self.url = 'https://fedoraproject.org/wiki/Packaging:SugarActivityGuidelines#Naming'
        self.text = 'Sugar activities must be named sugar-<activity name>'
        self.type = 'MUST'
        self.automatic = True

    def run(self):
        if not self.spec.name.startswith('sugar-'):
            self.set_passed(False)
            return
        # TODO check if sugar-foo is valid or if there is specific need for activity name
        self.set_passed('inconclusive')


class SugarActivityCheckBuildRequires(SugarActivityCheckBase):
    ''' All activities requires sugar-toolkit to build '''
    def __init__(self, base):
        SugarActivityCheckBase.__init__(self, base)
        self.url = 'https://fedoraproject.org/wiki/Packaging:SugarActivityGuidelines#Necessary_BuildRequires'
        self.text = 'Sugar activities depend on sugar-toolkit'
        self.type = 'MUST'
        self.automatic = True

    def run(self):
        br = self.spec.find_tag('BuildRequires')
        self.set_passed('sugar-toolkit' in br)


class SugarActivityCheckRuntimeDeps(SugarActivityCheckBase):
    ''' All runtime dependency information MUST be manually added. '''
    def __init__(self, base):
        SugarActivityCheckBase.__init__(self, base)
        self.url = 'https://fedoraproject.org/wiki/Packaging:SugarActivityGuidelines#Runtime_Dependencies'
        self.text = 'All runtime dependency information need to be manually added.'
        self.type = 'MUST'
        self.automatic = False
