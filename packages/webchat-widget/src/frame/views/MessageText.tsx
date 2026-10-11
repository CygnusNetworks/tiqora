import { Fragment, type JSX } from "preact";
import { linkify } from "../linkify";

/** Message text as text nodes; http(s) URLs become links, "\n" becomes <br>. No HTML sinks. */
export function MessageText(props: { text: string }): JSX.Element {
  const lines = props.text.split("\n");
  return (
    <span class="tq-text">
      {lines.map((line, i) => (
        <Fragment key={i}>
          {i > 0 && <br />}
          {linkify(line).map((p) =>
            p.kind === "link" ? (
              <a href={p.href} target="_blank" rel="noopener noreferrer nofollow">
                {p.text}
              </a>
            ) : (
              p.text
            ),
          )}
        </Fragment>
      ))}
    </span>
  );
}
