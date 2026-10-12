import type { JSX } from "preact";
import type { Translate } from "../i18n";

/** "Talk to a human" while the AI answers; confirm-free, disabled while the request runs. */
export function HandoffButton(props: { t: Translate; busy: boolean; onClick: () => void }): JSX.Element {
  return (
    <button class="tq-link tq-handoff" type="button" disabled={props.busy} onClick={props.onClick}>
      {props.busy ? props.t("bot.handoffPending") : props.t("bot.handoff")}
    </button>
  );
}
