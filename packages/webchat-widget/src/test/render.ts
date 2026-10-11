import { render, type ComponentChild } from "preact";
import { act } from "preact/test-utils";

/** Renders a vnode into a fresh container attached to the document. */
export function renderPreact(vnode: ComponentChild): { container: HTMLElement } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  act(() => {
    render(vnode, container);
  });
  return { container };
}
