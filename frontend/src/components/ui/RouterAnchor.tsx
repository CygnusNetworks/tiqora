import type { AnchorHTMLAttributes, MouseEvent } from "react";
import { useRouter } from "@tanstack/react-router";

/**
 * An `<a href>` for an internal path that is only known as a string (for example
 * `ciDetailHref`, whose Mode-B target route the CMDB plugin registers): a plain left click goes
 * through the router instead of reloading the page; modifier clicks, middle clicks and
 * `target` links keep the browser's behaviour. Outside a router (isolated component tests) it is
 * a plain link.
 */
export function RouterAnchor({ href, onClick, target, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const router = useRouter({ warn: false });
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || !router || target || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    void router.navigate({ to: href });
  };
  return <a href={href} target={target} onClick={handle} {...rest} />;
}
