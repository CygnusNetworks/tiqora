"""GenericInterface phase 2: the requester side (invokers), the debugger and its admin views.

What lives where:

* ``config`` - pure: requester config model parsed from ``gi_webservice_config`` YAML.
* ``loader`` - worker-side snapshot of the valid webservices incl. requester config.
* ``redact`` - pure: secret masking for headers, config dumps and URLs.
* ``debugger`` / ``debug_store`` - Znuny ``Debugger.pm`` semantics; rows in
  ``tiqora_gi_debug_entry`` / ``tiqora_gi_debug_content``; retention and GDPR delete.
* ``conditions`` - pure: invoker event conditions (``Event/Handler.pm``).
* ``error_handling`` - pure: ``ErrorHandling.pm`` + ``RequestRetry`` schedule.
* ``egress`` / ``ws_settings`` - per-webservice egress policy and Tiqora-side settings.
* ``transport_rest`` / ``soap_envelope`` / ``transport_soap`` - requester transports.
* ``deep_get`` / ``invoker_generic`` - ``Ticket::Generic`` request and response handling.
* ``requester`` - ``Requester.pm`` ``Run`` pipeline.
* ``jobs`` - job handlers ``core.gi_invoke`` / ``core.gi_invoke_sync`` (lanes ``gi`` / ``gi_sync``).
* ``events`` / ``follower`` / ``takeover`` - ticket history follower and the takeover switch.
* ``process_action`` - the ``ExecuteInvoker`` process transition action.

No logic here.
"""
