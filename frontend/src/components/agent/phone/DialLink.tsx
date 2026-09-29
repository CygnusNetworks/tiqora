import { useQuery } from "@tanstack/react-query";
import { phoneApi } from "@/lib/phoneApi";
import { dialHref } from "@/lib/phoneCall";

/** Hook form of the click-to-call scheme (`tel` unless the admin chose `sip`). */
function useDialScheme() {
  const q = useQuery({
    queryKey: ["reference", "phone-config"],
    queryFn: () => phoneApi.phoneConfig(),
    staleTime: 10 * 60 * 1000,
  });
  return q.data?.dial_scheme ?? "tel";
}

/**
 * A phone number as a click-to-call link (`tel:`/`sip:` per
 * `channel.phone.dial_scheme`). The link dials through the OS/softphone
 * handler; `onDial` runs alongside, e.g. to open the call form.
 */
export function DialLink({
  number,
  onDial,
  testId,
}: {
  number: string;
  onDial?: () => void;
  testId?: string;
}) {
  const scheme = useDialScheme();
  return (
    <a
      href={dialHref(number, scheme)}
      onClick={onDial}
      data-testid={testId}
      className="font-mono text-accent hover:underline"
    >
      {number}
    </a>
  );
}
