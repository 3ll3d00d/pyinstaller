#-----------------------------------------------------------------------------
# Copyright (c) 2013, PyInstaller Development Team.
#
# Distributed under the terms of the GNU General Public License with exception
# for distributing bootloader.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------

"""
Define a modified ModuleGraph that can return its contents as
a TOC and in other ways act like the old ImpTracker.
TODO: This class, along with TOC and Tree should be in a separate module.

For reference, the ModuleGraph node types and their contents:

  nodetype       identifier       filename

 Script         full path to .py   full path to .py
 SourceModule     basename         full path to .py
 BuiltinModule    basename         None
 CompiledModule   basename         full path to .pyc
 Extension        basename         full path to .so
 MissingModule    basename         None
 Package          basename         full path to __init__.py
        packagepath is ['path to package']
        globalnames is set of global names __init__.py defines

The main extension here over ModuleGraph is a method to extract nodes
from the flattened graph and return them as a TOC, or added to a TOC.
Other added methods look up nodes by identifier and return facts
about them, replacing what the old ImpTracker list could do.
"""


import logging
import os
from PyInstaller import compat as compat
import PyInstaller.utils
from modulegraph.modulegraph import ModuleGraph

logger = logging.getLogger(__name__)


class PyiModuleGraph(ModuleGraph):
    def __init__ (self, *args) :
        super(PyiModuleGraph, self).__init__(*args)
        # Dict to map ModuleGraph node types to TOC typecodes
        self.typedict = {
            'Module' : 'PYMODULE',
            'SourceModule' : 'PYMODULE',
            'CompiledModule' : 'PYMODULE',
            'Package' : 'PYMODULE',
            'Extension' : 'EXTENSION',
            'Script' : 'PYSOURCE',
            'BuiltinModule' : 'BUILTIN',
            'MissingModule' : 'MISSING',
            'does not occur' : 'BINARY'
            }

    # Return the name, path and type of selected nodes as a TOC, or appended
    # to a TOC. The selection is via a list of PyInstaller TOC typecodes.
    # If that list is empty we return the complete flattened graph as a TOC
    # with the ModuleGraph note types in place of typecodes -- meant for
    # debugging only. Normally we return ModuleGraph nodes whose types map
    # to the requested PyInstaller typecode(s) as indicated in the typedict.
    #
    # We use the ModuleGraph (really, ObjectGraph) flatten() method to
    # scan all the nodes. This is patterned after ModuleGraph.report().

    def make_a_TOC(self, typecode = [], existing_TOC = None ):
        result = existing_TOC or TOC()
        # Keep references to module code objects constructed by ModuleGraph
        # to avoid writting .pyc/pyo files to hdd.
        code_dict = {}
        for node in self.flatten() :
            # get node type e.g. Script
            mg_type = type(node).__name__
            if mg_type is None:
                continue # some nodes are not typed?
            # translate to the corresponding TOC typecode, or leave as-is
            toc_type = self.typedict.get(mg_type, mg_type)
            # Does the caller care about the typecode?
            if len(typecode) :
                # Caller cares, so if there is a mismatch, skip this one
                if not (toc_type in typecode) :
                    continue
            # else: caller doesn't care, return ModuleGraph type in typecode
            # Extract the identifier and a path if any.
            if mg_type == "Script" :
                # for Script nodes only, identifier is a whole path
                (name, ext) = os.path.splitext(node.filename)
                name = os.path.basename(name)
            else:
                name = node.identifier
            path = node.filename if node.filename is not None else ''
            # TOC.append the data. This checks for a pre-existing name
            # and skips it if it exists.
            result.append( (name, path, toc_type) )
            # Keep references to module code objects constructed by ModuleGraph
            # to avoid compiling and writting .pyc/pyo files to hdd.
            if node.code:
                code_dict[name] = node.code
        return result, code_dict

    # Given a list of nodes, create a TOC representing those nodes.
    # This is mainly used to initialize a TOC of scripts with the
    # ones that are runtime hooks. The process is almost the same as
    # make_a_TOC, but the caller guarantees the nodes are
    # valid, so minimal checking.
    def nodes_to_TOC(self, node_list, existing_TOC = None ):
        result = existing_TOC or TOC()
        for node in node_list:
            mg_type = type(node).__name__
            toc_type = self.typedict[mg_type]
            if mg_type == "Script" :
                (name, ext) = os.path.splitext(node.filename)
                name = os.path.basename(name)
            else:
                name = node.identifier
            path = node.filename if node.filename is not None else ''
            result.append( (name, path, toc_type) )
        return result

    # Return true if the named item is in the graph as a BuiltinModule node.
    # The passed name is a basename.
    def is_a_builtin(self, name) :
        node = self.findNode(name)
        if node is None : return False
        return type(node).__name__ == 'BuiltinModule'

    # Return a list of the names that import a given name. Basically
    # just get the iterator for incoming-edges and return the
    # identifiers from the nodes it reports.
    def importer_names(self, name) :
        node = self.findNode(name)
        if node is None : return []
        _, iter_inc = self.get_edges(node)
        return [importer.identifier for importer in iter_inc]


# TODO Simplify the representation and use directly Modulegraph objects.
class TOC(compat.UserList):
    """
    TOC (Table of Contents) class is a list of tuples of the form (name, path, tytecode).

    typecode    name                   path                        description
    --------------------------------------------------------------------------------------
    EXTENSION   Python internal name.  Full path name in build.    Extension module.
    PYSOURCE    Python internal name.  Full path name in build.    Script.
    PYMODULE    Python internal name.  Full path name in build.    Pure Python module (including __init__ modules).
    PYZ         Runtime name.          Full path name in build.    A .pyz archive (ZlibArchive data structure).
    PKG         Runtime name.          Full path name in build.    A .pkg archive (Carchive data structure).
    BINARY      Runtime name.          Full path name in build.    Shared library.
    DATA        Runtime name.          Full path name in build.    Arbitrary files.
    OPTION      The option.            Unused.                     Python runtime option (frozen into executable).

    A TOC contains various types of files. A TOC contains no duplicates and preserves order.
    PyInstaller uses TOC data type to collect necessary files bundle them into an executable.
    """
    def __init__(self, initlist=None):
        compat.UserList.__init__(self)
        self.fltr = {}
        if initlist:
            for tpl in initlist:
                self.append(tpl)

    def append(self, tpl):
        try:
            fn = tpl[0]
            if tpl[2] == "BINARY":
                # Normalize the case for binary files only (to avoid duplicates
                # for different cases under Windows). We can't do that for
                # Python files because the import semantic (even at runtime)
                # depends on the case.
                fn = os.path.normcase(fn)
            if not self.fltr.get(fn):
                self.data.append(tpl)
                self.fltr[fn] = 1
        except TypeError:
            logger.info("TOC found a %s, not a tuple", tpl)
            raise

    def insert(self, pos, tpl):
        fn = tpl[0]
        if tpl[2] == "BINARY":
            fn = os.path.normcase(fn)
        if not self.fltr.get(fn):
            self.data.insert(pos, tpl)
            self.fltr[fn] = 1

    def __add__(self, other):
        rslt = TOC(self.data)
        rslt.extend(other)
        return rslt

    def __radd__(self, other):
        rslt = TOC(other)
        rslt.extend(self.data)
        return rslt

    def extend(self, other):
        for tpl in other:
            self.append(tpl)

    def __sub__(self, other):
        fd = self.fltr.copy()
        # remove from fd if it's in other
        for tpl in other:
            if fd.get(tpl[0], 0):
                del fd[tpl[0]]
        rslt = TOC()
        # return only those things still in fd (preserve order)
        for tpl in self.data:
            if fd.get(tpl[0], 0):
                rslt.append(tpl)
        return rslt

    def __rsub__(self, other):
        rslt = TOC(other)
        return rslt.__sub__(self)

    def intersect(self, other):
        rslt = TOC()
        for tpl in other:
            if self.fltr.get(tpl[0], 0):
                rslt.append(tpl)
        return rslt


class FakeModule(object):
    """
    Create a "mod": an object with info about an imported module.
    This is the historic API object passed to the hook(mod) method
    of a hook-modname.py file. Originally a mod object was created
    by the old ImpTracker, and was similar to a modulegraph node, although
    with more data. Hooks relied on the following properties:
         mod.__file__ for the full path to a script
       * mod.__path__ for the full path to a package or module
       * mod.co for the compiled code of a script or module
       * mod.datas for a list of associated data files
       * mod.imports for a list of things this module imports
       * mod.binaries for a list of (name,path,'BINARY') tuples (or a TOC)
    (* means, the hook might modify this member)
    The new mod provides these members for examination only but has
    methods for modification:
       mod.add_binary( (name, path, typecode) ) add a binary dependency
       mod.add_import( modname ) add a python import
       mod.del_import( modname ) remove a python dependency
       mod.retarget( path, code ) retarget to a different piece of code (hook-site)


    #########################################################
    The mod object is just used for communication with hooks.
    #########################################################


    It is constructed before the call from modulegraph info.
    Afterward, changes are returned to the graph and other dicts.
    """
    def __init__(self, identifier, graph) :
        # Go into the module graph and get the node for this identifier.
        # It should always exist because the caller should be working
        # from the graph itself, or a TOC made from the graph.
        node = graph.findNode(identifier)
        assert(node is not None) # should not occur
        self.name = identifier
        # keep a pointer back to the original node
        self.node = node
        # keep a pointer back to the original graph
        self.graph = graph
        # Add the __file__ member
        self.__file__ = node.filename
        # Add the __path__ member which is either None or, if
        # the node type is Package, a list of one element, the
        # path string to the package directory -- just like a mod.
        # Note that if the hook changes it, it will change in the node proper.
        self.__path__ = node.packagepath
        # Stick in the .co (compiled code) member. One hook (hook-distutiles)
        # wants to change both __path__ and .co. TODO: HOW HANDLE?
        self.co = node.code
        # Create the datas member as an empty list
        self.datas = []
        # Add the binaries and imports lists and populate with names.
        # The node imports whatever is reachable in the graph
        # starting at that node. Put Extension names in binaries.
        self.binaries = []
        self.imports = []
        for impnode in graph.flatten(None,node) :
            if type(impnode).__name__ != 'Extension' :
                self.imports.append([impnode.identifier,1,0,-1])
            else:
                self.binaries.append( [(impnode.identifier, impnode.filename, 'BINARY')] )
        # Private members to collect changes.
        self._added_imports = []
        self._deleted_imports = []
        self._added_binaries = []

    def add_import(self,names):
        if not isinstance(names, list):
            names = [names]  # Allow passing string or list.
        self._added_imports.extend(names) # save change to implement in graph later
        for name in names:
            self.imports.append([name,1,0,-1]) # make change visible to caller

    def del_import(self,names):
        # just save to implement in graph later
        if not isinstance(names, list):
            names = [names]  # Allow passing string or list.
        self._deleted_imports.extend(names)

    def add_binary(self,list_of_tuples):
        self._added_binaries.append(list_of_tuples)
        self.binaries.append(list_of_tuples)

    def retarget(self, path_to_new_code):
        # Used by hook-site (and others?) to retarget a module to a simpler one
        # more suited to being frozen.

        # Keep the original filename in the fake code object.
        new_code = PyInstaller.utils.misc.get_code_object(path_to_new_code, new_filename=self.node.filename)
        # Update node.
        self.node.code = new_code
        self.node.filename = path_to_new_code
        # Update dependencies in the graph.
        self.graph.scan_code(new_code, self.node)