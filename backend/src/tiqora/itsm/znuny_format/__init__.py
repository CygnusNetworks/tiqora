"""Znuny data-shape conversions for the ITSM importer and the GenericInterface operations.

Pure modules: no DB, no network, no file system. Definition parsing is not implemented here
(R8): the grammar is read by the step-0 safe parser (``tiqora.znuny.perl_data``) and itsm1's
definition preparation, mapped onto Tiqora attributes by ``tiqora.cmdb.znuny_definition``.
"""
