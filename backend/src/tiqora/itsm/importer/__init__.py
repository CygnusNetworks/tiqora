"""ITSM importer (wave 4, plan itsm-import-addon): Znuny ITSM -> Tiqora CMDB, Mode A -> B.

``ports``   — the interfaces the importer, the GI ``ConfigItem*`` operations, the process action
              and the MCP tools use (pure dataclasses and protocols).
``facades`` — the only module that imports itsm1 / cmdb internals; one class per port.
"""
