"""Pure unit tests for ProcessManagement YAML config parsing (no DB)."""

from __future__ import annotations

from tiqora.process.config import (
    ActivityConfig,
    ActivityDialogConfig,
    ProcessConfig,
    ProcessLayout,
    TransitionActionConfig,
    TransitionConfig,
)

PROCESS_CONFIG_YAML = """
Description: Customer selenium ticket process
StartActivity: Activity-e2cd48a1807511cbe1b8cef2e750a9de
StartActivityDialog: ActivityDialog-ff3961b48b8966e39ff950e9f40148cf
Path:
  Activity-50235d211de0ea1d0d218f06254dc644:
    Transition-4165f99bd46906d549505dadc1efe495:
      ActivityEntityID: Activity-91bb50fff304574fdcf9e84b63242f7a
      TransitionAction:
      - TransitionAction-9c130d0106d6318ec0323c61a5a198a1
  Activity-91bb50fff304574fdcf9e84b63242f7a: {}
"""

PROCESS_LAYOUT_YAML = """
Activity-50235d211de0ea1d0d218f06254dc644:
  left: '708'
  top: '17'
"""

ACTIVITY_CONFIG_YAML = """
ActivityDialog:
  '2': ActivityDialog-second
  '1': ActivityDialog-24f90970400e3c2fc976eaf0f1e08b31
"""

ACTIVITY_DIALOG_CONFIG_YAML = """
DescriptionLong: ''
DescriptionShort: Completed
FieldOrder:
- Priority
- Article
Fields:
  Article:
    Config:
      ArticleType: phone
    DefaultValue: ''
    DescriptionLong: ''
    DescriptionShort: ''
    Display: '1'
  Priority:
    DefaultValue: ''
    DescriptionLong: ''
    DescriptionShort: ''
    Display: '2'
Interface:
- AgentInterface
- CustomerInterface
Permission: ''
RequiredLock: '0'
SubmitAdviceText: ''
SubmitButtonText: ''
"""

TRANSITION_CONFIG_YAML = """
Condition:
  '1':
    Fields:
      Priority:
        Match: 5 very high
        Type: String
    Type: and
ConditionLinking: and
"""

TRANSITION_ACTION_CONFIG_YAML = """
Config:
  State: open
Module: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet
"""


def test_process_config_parses_path_and_start_activity() -> None:
    config = ProcessConfig.from_yaml(PROCESS_CONFIG_YAML)

    assert config.description == "Customer selenium ticket process"
    assert config.start_activity == "Activity-e2cd48a1807511cbe1b8cef2e750a9de"
    assert config.start_activity_dialog == "ActivityDialog-ff3961b48b8966e39ff950e9f40148cf"
    assert set(config.path) == {
        "Activity-50235d211de0ea1d0d218f06254dc644",
        "Activity-91bb50fff304574fdcf9e84b63242f7a",
    }
    transitions = config.path["Activity-50235d211de0ea1d0d218f06254dc644"]
    entry = transitions["Transition-4165f99bd46906d549505dadc1efe495"]
    assert entry.activity_entity_id == "Activity-91bb50fff304574fdcf9e84b63242f7a"
    assert entry.transition_actions == ["TransitionAction-9c130d0106d6318ec0323c61a5a198a1"]
    # end-state activity has no outgoing transitions
    assert config.path["Activity-91bb50fff304574fdcf9e84b63242f7a"] == {}


def test_process_config_empty_input_yields_defaults() -> None:
    config = ProcessConfig.from_yaml(None)
    assert config.description == ""
    assert config.start_activity is None
    assert config.path == {}


def test_process_layout_parses_positions() -> None:
    layout = ProcessLayout.from_yaml(PROCESS_LAYOUT_YAML)
    assert layout.positions["Activity-50235d211de0ea1d0d218f06254dc644"]["left"] == "708"


def test_activity_config_orders_dialogs_numerically() -> None:
    config = ActivityConfig.from_yaml(ACTIVITY_CONFIG_YAML)
    assert config.ordered_activity_dialog_entity_ids == [
        "ActivityDialog-24f90970400e3c2fc976eaf0f1e08b31",
        "ActivityDialog-second",
    ]


def test_activity_dialog_config_parses_fields_and_display() -> None:
    config = ActivityDialogConfig.from_yaml(ACTIVITY_DIALOG_CONFIG_YAML)

    assert config.description_short == "Completed"
    assert config.field_order == ["Priority", "Article"]
    assert set(config.fields) == {"Priority", "Article"}

    article = config.fields["Article"]
    assert article.config == {"ArticleType": "phone"}
    assert article.display == "1"
    assert article.required is True

    priority = config.fields["Priority"]
    assert priority.display == "2"
    assert priority.required is False

    assert config.interface == ["AgentInterface", "CustomerInterface"]


def test_transition_config_parses_conditions() -> None:
    config = TransitionConfig.from_yaml(TRANSITION_CONFIG_YAML)

    assert config.condition_linking == "and"
    assert len(config.conditions) == 1
    block = config.conditions[0]
    assert block.type_ == "and"
    assert block.fields["Priority"].match == "5 very high"
    assert block.fields["Priority"].type_ == "String"


def test_transition_config_no_condition_key_is_empty_and_unconditional() -> None:
    config = TransitionConfig.from_yaml("ConditionLinking: and\n")
    assert config.conditions == []


def test_transition_action_config_preserves_module_and_config() -> None:
    config = TransitionActionConfig.from_yaml(TRANSITION_ACTION_CONFIG_YAML)
    assert config.module == "Kernel::System::ProcessManagement::TransitionAction::TicketStateSet"
    assert config.config == {"State": "open"}


def test_transition_action_config_module_last_segment() -> None:
    config = TransitionActionConfig.from_yaml(TRANSITION_ACTION_CONFIG_YAML)
    assert config.module.split("::")[-1] == "TicketStateSet"
