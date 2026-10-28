"""Shared approval engine ("Freigaben").

What lives where:

* ``contract``    cross-plan contract: principals, definition models, subject types,
                  resolvers, outcome/info dataclasses, error codes,
                  event and job names, extension points (pure)
* ``rules``       pure rule evaluation and stage progression (pure)
* ``models``      ORM tables ``tiqora_approval*``
* ``settings``    settings section ``approvals``
* ``core_types``  core subject type ``ticket`` and the core approver resolvers
* ``lists``, ``templates``   approver lists ("Freigabegruppen") and templates
* ``resolve``     approver entries -> principal snapshot
* ``service``     the stable service API consumers call (request, decide, cancel, ...)
* ``access``, ``delegation``, ``inbox``   visibility, standing delegations, inbox queries
* ``tokens``      e-mail decision links
* ``texts``, ``notify``   mail texts and the ``core.approval_mail`` job
* ``audit``, ``hooks``    audit rows, outbox events, history lines; ``core.approval_hook`` job
* ``sweep``       deadlines, reminders, escalation, token purge (daemon ``approvals``)
* ``schemas``     Pydantic API models

Rule: this package never imports ``tiqora.process``, ``tiqora.itsm``, ``tiqora.cmdb`` or
``tiqora.api``. Consumers import the engine (``contract``, ``service``), never the other way round;
they register their subject types and resolvers in their own block of ``register_core``.
"""
