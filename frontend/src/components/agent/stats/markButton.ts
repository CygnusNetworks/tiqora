/** Props that make a chart mark a button (additive `onSelect`): role, tab stop, click, Enter/Space. */
export function markButtonProps(onActivate: () => void): {
  role: "button";
  tabIndex: 0;
  onClick: () => void;
  onKeyDown: (e: { key: string; preventDefault: () => void }) => void;
  style: { cursor: "pointer" };
} {
  return {
    role: "button",
    tabIndex: 0,
    onClick: onActivate,
    onKeyDown: (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onActivate();
      }
    },
    style: { cursor: "pointer" },
  };
}
