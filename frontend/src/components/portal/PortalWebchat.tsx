import { useEffect, useRef } from "react";
import { useCustomerAuth } from "@/auth/CustomerAuthContext";
import { usePortalWebchat } from "@/lib/webchatApi";

const LOADER_SRC = "/chat/v1/loader.js";

type ChatHost = { Tiqora?: { chat?: { logout?: () => void } } };

/** Tells a running widget that the portal customer is gone (it forgets the signed-in chat). */
function widgetLogout() {
  try {
    (window as unknown as ChatHost).Tiqora?.chat?.logout?.();
  } catch {
    // the widget is optional; a failing host API must not break the logout
  }
}

/**
 * Loads the web chat widget in the customer portal when an admin switched
 * "Im Kundenportal anzeigen" on. The widget runs on Tiqora's own origin and
 * learns the logged-in customer from the portal session by itself, so the
 * script tag carries only the public key. The tag stays for the whole page
 * life (a second boot of the loader is refused); only the chat's own sign-in
 * is cleared when the customer leaves, and nothing else of `window.Tiqora`.
 */
export function PortalWebchat() {
  const { customer } = useCustomerAuth();
  const signedIn = !!customer;
  const { data } = usePortalWebchat(signedIn);
  const key = signedIn ? (data?.public_key ?? null) : null;

  useEffect(() => {
    if (!key) return;
    if (document.querySelector("script[data-tiqora-chat]")) return;
    const s = document.createElement("script");
    s.src = LOADER_SRC;
    s.async = true;
    s.setAttribute("data-tiqora-chat", key);
    document.body.appendChild(s);
  }, [key]);

  // Logout once: when the customer goes away while mounted, or when the portal
  // shell is torn down by that logout (its auth guard unmounts the children).
  const wasSignedIn = useRef(false);
  const loggedOut = useRef(false);
  useEffect(() => {
    if (signedIn) {
      wasSignedIn.current = true;
      loggedOut.current = false;
    } else if (wasSignedIn.current && !loggedOut.current) {
      loggedOut.current = true;
      wasSignedIn.current = false;
      widgetLogout();
    }
  }, [signedIn]);
  useEffect(
    () => () => {
      if (wasSignedIn.current && !loggedOut.current) {
        loggedOut.current = true;
        widgetLogout();
      }
    },
    [],
  );

  return null;
}
