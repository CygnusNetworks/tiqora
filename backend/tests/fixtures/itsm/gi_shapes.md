# GenericInterface `ConfigItem*` - source survey (Task 21)

Sources: ITSMConfigurationManagement 6.5.4 and 7.3.5 (`tests/golden/itsm-packages/src/` of the itsm1
branch), paths below relative to the package root. Every statement has a file/line reference; the
tests in `test_compat_config_items_get.py` pin it.

## ConfigItemGet (`Kernel/GenericInterface/Operation/ConfigItem/ConfigItemGet.pm`)

6.5.4 and 7.3.5 are byte-identical for this file (`diff` clean), so there is one code path.

| Topic | Znuny behaviour | Line |
|---|---|---|
| Parameters | `ConfigItemID` (required; comma separated string or array), `Attachments` (Perl truthy, default 0) | 74, 177 |
| Auth | `Auth()` fails -> `AuthFail`, message `ConfigItemGet: Authorization failing!` | 147 |
| Missing id | `!$Param{Data}->{ConfigItemID}` (undef, `''`, `'0'`, `0`) -> `ConfigItemGet.MissingParameter`, `ConfigItemGet: ConfigItemID parameter is missing!` | 156 |
| Id shape | non-empty string -> `split /,/`; non-empty array ok; anything else (hash, `[]`) -> `ConfigItemGet.WrongStructure`, `ConfigItemGet: Structure for ConfigItemID is not correct!` | 166-173 |
| Permission | `ITSMConfigItem->Permission(Scope => 'Item', ItemID, UserID, Type => SysConfig GenericInterface::Operation::ConfigItemGet###Permission)`, default `ro` (`Kernel/Config/Files/XML/ITSMConfigItem.xml:1432-1438`). `ItemClassGroupCheck` reads the item, then the class' `Permission` preference (a group id) and compares with the user's `Type` groups | 191; `Permission/ItemClassGroupCheck.pm` Run() |
| Denied | `ConfigItemGet.AccessDenied`, `ConfigItemGet: Can not get configuration item!`. **The first denied id aborts the call, nothing is omitted.** An unknown id has no class, so no `Permission` -> the same `AccessDenied` (`scripts/test/GenericInterface/Operation/ConfigItem/ConfigItemGet.t:491-522`: "Wrong ConfigItemID" -> `ConfigItemGet.AccessDenied`) | 200 |
| Version | `VersionGet` of the item (latest version); not a hash -> error code `'$Self->{OperationName}.InvalidParameter'` | 223 |
| No items | empty result -> `'$Self->{OperationName}.NoConfigItemData'` | 297 |
| Removed keys | `ClassID, CurDeplStateID, CurInciStateID, DeplStateID, InciStateID, XMLDefinitionID`; `XMLDefinition` is used for the data conversion, then dropped | 229-236 |
| CIXMLData | `InvertFormatXMLData(XMLData->[1]->{Version})` then `InvertReplaceXMLData(Definition)`; `XMLData` is renamed `CIXMLData` | 238-250 |
| Attachments | only when requested: list of `ConfigItemAttachmentGet` hashes, `Content` = `encode_base64` (76-char lines, `\n` terminated), `Attachment => ''` (present, empty string) when there are none | 257-283 |
| Response | `{ConfigItem => [ ... ]}` - always a list, one entry per requested id in request order | 304-306 |

Keys of one entry (`Version/VersionGet`, `Kernel/System/ITSMConfigItem/Version.pm:341-481`):
`VersionID, ConfigItemID, Name, DefinitionID, CreateTime, CreateBy` (all **of the version**),
`DeplState, DeplStateType, InciState, InciStateType` (the version's states, `Type` = general
catalog preference `Functionality`), `Class, LastVersionID, Number, CurDeplState, CurDeplStateType,
CurInciState, CurInciStateType` (the CI's current states), `CIXMLData`, optional `Attachment`.
There is no `ChangeTime` and no `*ID` state key. `CreateTime` is `YYYY-MM-DD HH:MM:SS`.

Attachment hash (`Kernel/System/ITSMConfigItem.pm:693-743`, `VirtualFS->Read`): `Filename`,
`Content`, `ContentType`, `Type => 'attachment'`, `Filesize` = `Preferences->{FilesizeRaw}`
(bytes, the "4.6 KBytes" form of the POD is in `Preferences->{Filesize}`), `Preferences`.

## Differences 6.5.4 vs 7.3.5

| File | Difference |
|---|---|
| `ConfigItemGet.pm` | none |
| `ConfigItem/Common.pm` | `Valid => 1` on the catalog `ItemList` call (see `gi_cixml.py`); folded into `GiLookups` |

## Error codes (decision recorded here, pinned by tests)

Znuny has three codes in single quotes (`'$Self->{OperationName}.AuthFail'`,
`'...InvalidParameter'`, `'...NoConfigItemData'`): they are sent **literally with the unexpanded
variable** (a Znuny bug). Tiqora sends `ConfigItemGet.AuthFail`, `ConfigItemGet.InvalidParameter`,
`ConfigItemGet.NoConfigItemData` (expanded, like every `Ticket*` operation of the compat layer; a
client that matches on the suffix works with both). `MissingParameter`, `WrongStructure` and
`AccessDenied` are interpolated in Znuny and are identical.

Tiqora additions: more than 500 ids -> `ConfigItemGet.InvalidParameter` (Znuny has no limit);
in Mode A and `off` see below.

## Customers

`Operation/Common.pm` `Auth()` hands a customer back as `($CustomerLogin, 'Customer')`; the
`Permission` check then asks for the groups of a user whose "id" is a login, finds none and answers
`AccessDenied`. The operations are agent-only; a customer login gets `AccessDenied` (after the
parameter checks).

## Routes

Neither tag ships a REST connector YAML (only `scripts/webservices/GenericConfigItemConnectorSOAP.yml`),
so the canonical routes mirror the ticket connector: `POST /ConfigItem` (create), `GET /ConfigItem/:ID`
(get), `PATCH /ConfigItem/:ID` (update), `DELETE /ConfigItem/:ID` (delete), `GET|POST /ConfigItemSearch`.
The webservice types are `ConfigItem::ConfigItemGet` etc. (`OPERATION_TYPES`).

## Modes

| Mode | Get / Search | Create / Update / Delete |
|---|---|---|
| `tiqora` | Tiqora CMDB | Tiqora CMDB |
| `znuny_readthrough` (A) | Znuny tables through the importer facade (`ModeAReader`) | `<Op>.NotAvailable`, "Die CMDB wird in Znuny geführt (Modus A)" |
| `off` | `Operation.NotImplemented` | `Operation.NotImplemented` |

Order: authentication, then the mode gate (an anonymous caller does not learn the mode).
